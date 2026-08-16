"""Ajan döngüsü: model → tool_call → politika → (onay) → araç → model (spec §6.1).

Döngü UI'dan bağımsızdır: dışarıyla iki callback üzerinden konuşur —
`emit` (olay yayınla) ve `approve` (kullanıcıya sor, cevabı bekle). Bu sayede
testlerde WebSocket olmadan, sahte bir LLM istemcisiyle çalıştırılabilir.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any, Awaitable, Callable

from app.agent.llm import LLMClient, LLMError, LiteLLMClient, RateLimited, ToolCall
from app.agent.prompts import system_prompt, wrap_tool_result
from app.agent.tools import get_tool, tool_schemas
from app.agent.tools.base import ToolContext, ToolResult
from app.memory.store import ensure_session, load_history, save_message
from app.quota.probes import record_rate_limit
from app.quota.tracker import record_usage
from app.router.selector import NoProviderAvailable, Selection, choose
from app.safety.audit import audit
from app.settings import settings

Emitter = Callable[[dict[str, Any]], Awaitable[None]]
Approver = Callable[[dict[str, Any]], Awaitable[bool]]


async def _noop_emit(event: dict[str, Any]) -> None:
    return None


async def _deny_all(request: dict[str, Any]) -> bool:
    """Onay kanalı yoksa varsayılan cevap "hayır"dır."""
    return False


class AgentLoop:
    def __init__(
        self,
        *,
        session_id: str,
        llm: LLMClient | None = None,
        emit: Emitter | None = None,
        approve: Approver | None = None,
        cwd: Path | None = None,
        max_steps: int | None = None,
        dry_run: bool | None = None,
    ) -> None:
        self.session_id = session_id
        self.llm = llm or LiteLLMClient()
        self.emit = emit or _noop_emit
        self.approve = approve or _deny_all
        self.max_steps = max_steps if max_steps is not None else settings.max_agent_steps
        self.ctx = ToolContext(
            cwd=cwd or settings.resolved_workspace_root,
            session_id=session_id,
            dry_run=settings.dry_run if dry_run is None else dry_run,
        )
        self.steps_used = 0
        # Bu oturumda hâlâ hangi sağlayıcıyı kullanıyoruz (model devri rozeti için).
        self.current_provider: str = ""

    # --- ana döngü --------------------------------------------------------

    async def run(self, user_message: str) -> str:
        ensure_session(self.session_id)
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt(str(self.ctx.cwd))}
        ]
        messages.extend(load_history(self.session_id))
        messages.append({"role": "user", "content": user_message})
        save_message(self.session_id, "user", user_message)

        audit(
            "turn_start",
            session=self.session_id,
            dry_run=self.ctx.dry_run,
            cwd=str(self.ctx.cwd),
            message=user_message[:500],
        )

        budget = self.max_steps
        final_text = ""

        while True:
            self.steps_used += 1
            await self.emit({"type": "step", "step": self.steps_used})

            async def on_token(text: str) -> None:
                await self.emit({"type": "token", "content": text})

            try:
                response = await self._call_model(messages, on_token)
            except LLMError as exc:
                await self.emit({"type": "error", "message": str(exc)})
                audit("llm_error", session=self.session_id, error=str(exc)[:500])
                return final_text
            except Exception as exc:  # ağ hatası vb. — sohbeti düşürme
                await self.emit({"type": "error", "message": f"Model çağrısı başarısız: {exc}"})
                audit("llm_error", session=self.session_id, error=repr(exc)[:500])
                return final_text

            if not response.tool_calls:
                final_text = response.content
                if final_text:
                    save_message(
                        self.session_id, "assistant", final_text, model=response.model
                    )
                await self.emit(
                    {
                        "type": "done",
                        "steps": self.steps_used,
                        "model": response.model,
                        "provider": response.provider or self.current_provider,
                        "tokens": response.prompt_tokens + response.completion_tokens,
                        "ms": response.latency_ms,
                    }
                )
                audit("turn_end", session=self.session_id, steps=self.steps_used)
                return final_text

            messages.append(
                {
                    "role": "assistant",
                    "content": response.content or "",
                    "tool_calls": [
                        {
                            "id": call.id,
                            "type": "function",
                            "function": {"name": call.name, "arguments": call.arguments or "{}"},
                        }
                        for call in response.tool_calls
                    ],
                }
            )
            if response.content:
                save_message(
                    self.session_id, "assistant", response.content, model=response.model
                )

            for call in response.tool_calls:
                output, untrusted = await self._handle_tool_call(call)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "name": call.name,
                        "content": wrap_tool_result(call.name, output, untrusted=untrusted),
                    }
                )

            if self.steps_used >= budget:
                approved = await self.approve(
                    {
                        "type": "continue_request",
                        "id": f"continue-{self.steps_used}",
                        "steps": self.steps_used,
                        "limit": budget,
                        "summary": (
                            f"Ajan {self.steps_used} adımdır çalışıyor ve hâlâ bitmedi. "
                            f"{self.max_steps} adım daha devam edeyim mi?"
                        ),
                    }
                )
                audit(
                    "step_limit",
                    session=self.session_id,
                    steps=self.steps_used,
                    approved=approved,
                )
                if not approved:
                    message = (
                        f"Adım limitine ({budget}) ulaşıldı, kullanıcı devam etmek "
                        "istemedi. İşlem yarıda kesildi."
                    )
                    await self.emit({"type": "stopped", "message": message})
                    save_message(self.session_id, "assistant", message)
                    return message
                budget += self.max_steps

    # --- model çağrısı ve sağlayıcı devri ---------------------------------

    async def _call_model(self, messages: list[dict[str, Any]], on_token) -> Any:
        """Sağlayıcı seç, çağır, 429 alırsan sessizce bir sonrakine geç.

        Spec §9 Faz 3 kabul kriteri: "bir sağlayıcının limitini kasten doldur;
        sistem sessizce diğerine geçsin ve UI'da sebebi görünsün." Sessizce =
        kullanıcıya hata gösterilmez; "UI'da sebebi görünür" = `model_switch`
        olayı gönderilir.
        """
        attempted: set[str] = set()
        last_error: Exception | None = None

        for _ in range(max(1, settings.max_provider_attempts)):
            try:
                selection = choose(exclude=attempted)
            except NoProviderAvailable as exc:
                raise LLMError(str(exc)) from exc

            await self._announce(selection)

            try:
                response = await self.llm.complete(
                    messages,
                    tool_schemas(),
                    on_token,
                    model=selection.model,
                    provider=selection.provider,
                )
            except RateLimited as exc:
                record_rate_limit(selection.provider, retry_after=exc.retry_after)
                record_usage(
                    provider=selection.provider,
                    model=selection.model,
                    status="rate_limited",
                    session_id=self.session_id,
                )
                attempted.add(selection.provider)
                last_error = exc
                audit(
                    "provider_rate_limited",
                    session=self.session_id,
                    provider=selection.provider,
                    retry_after=exc.retry_after,
                )
                continue
            except LLMError as exc:
                # Sağlayıcıya özgü bir hata (502, model yok vb.) — onu bu tur
                # için ele ve sıradakini dene.
                record_usage(
                    provider=selection.provider,
                    model=selection.model,
                    status="error",
                    session_id=self.session_id,
                )
                attempted.add(selection.provider)
                last_error = exc
                audit(
                    "provider_error",
                    session=self.session_id,
                    provider=selection.provider,
                    error=str(exc)[:300],
                )
                continue

            record_usage(
                provider=selection.provider,
                model=selection.model,
                prompt_tokens=response.prompt_tokens,
                completion_tokens=response.completion_tokens,
                latency_ms=response.latency_ms,
                status="ok",
                session_id=self.session_id,
            )
            return response

        raise LLMError(
            f"Tüm sağlayıcılar denendi ({', '.join(sorted(attempted)) or 'yok'}), "
            f"sonuncusu: {last_error}"
        )

    async def _announce(self, selection: Selection) -> None:
        """Sağlayıcı değiştiyse UI'da rozet göster (spec §6.1)."""
        if selection.provider == self.current_provider:
            return
        previous = self.current_provider
        self.current_provider = selection.provider
        await self.emit(
            {
                "type": "model_switch",
                "provider": selection.provider,
                "model": selection.model,
                "previous": previous,
                "reason": selection.reason,
                "forced": selection.forced,
                "rejected": [
                    {"provider": item.provider, "reason": item.reason}
                    for item in selection.rejected
                ],
                "explanation": selection.explain(),
            }
        )
        audit(
            "provider_selected",
            session=self.session_id,
            provider=selection.provider,
            previous=previous,
            explanation=selection.explain(),
        )

    # --- araç çalıştırma --------------------------------------------------

    async def _handle_tool_call(self, call: ToolCall) -> tuple[str, bool]:
        tool = get_tool(call.name)
        arguments = call.parsed_arguments()

        if tool is None:
            output = f"Böyle bir araç yok: '{call.name}'. Kullanılabilir araçlar: {', '.join(t['function']['name'] for t in tool_schemas())}"
            await self._emit_tool_result(call, ToolResult(False, output, untrusted=False), 0, "unknown")
            return output, False

        if not isinstance(arguments, dict):
            output = (
                f"'{call.name}' çağrısının argümanları geçerli JSON değil: {arguments!r}"
            )
            await self._emit_tool_result(call, ToolResult(False, output, untrusted=False), 0, "bad-args")
            return output, False

        try:
            decision = tool.assess(self.ctx, **arguments)
        except TypeError as exc:
            output = f"'{call.name}' çağrısı geçersiz argüman aldı: {exc}"
            await self._emit_tool_result(call, ToolResult(False, output, untrusted=False), 0, "bad-args")
            return output, False

        await self.emit(
            {
                "type": "tool_call",
                "id": call.id,
                "name": call.name,
                "args": arguments,
                "risk": decision.risk,
                "reason": decision.reason,
            }
        )
        audit(
            "tool_call",
            session=self.session_id,
            tool=call.name,
            args=arguments,
            risk=decision.risk,
            rule=decision.rule,
            reason=decision.reason,
            dry_run=self.ctx.dry_run,
        )

        if decision.risk == "blocked":
            output = (
                f"REDDEDİLDİ — güvenlik politikası bu çağrıya izin vermiyor.\n"
                f"Sebep: {decision.reason}\n"
                "Bu isteği başka bir yazımla tekrar deneme; kullanıcıya durumu bildir."
            )
            audit("tool_blocked", session=self.session_id, tool=call.name, rule=decision.rule)
            await self._emit_tool_result(call, ToolResult(False, output, untrusted=False), 0, "blocked")
            save_message(self.session_id, "tool", output, tool_name=call.name)
            return output, False

        if decision.risk == "confirm":
            preview = tool.preview(self.ctx, **arguments)
            approved = await self.approve(
                {
                    "type": "approval_request",
                    "id": call.id,
                    "tool": call.name,
                    "args": arguments,
                    "risk": decision.risk,
                    "reason": decision.reason,
                    "summary": preview.summary,
                    "paths": preview.paths,
                    "detail": preview.detail,
                    "dry_run": self.ctx.dry_run,
                }
            )
            audit(
                "tool_approval",
                session=self.session_id,
                tool=call.name,
                approved=approved,
                summary=preview.summary,
            )
            if not approved:
                output = (
                    "Kullanıcı bu işlemi onaylamadı. Aynı işlemi tekrar deneme; "
                    "gerekiyorsa neden gerekli olduğunu açıkla veya başka bir yol öner."
                )
                await self._emit_tool_result(call, ToolResult(False, output, untrusted=False), 0, "denied")
                save_message(self.session_id, "tool", output, tool_name=call.name)
                return output, False

        started = time.monotonic()
        try:
            result = await asyncio.to_thread(tool.run, self.ctx, **arguments)
        except Exception as exc:  # araç hatası döngüyü düşürmemeli
            result = ToolResult(False, f"Araç hatası: {exc!r}", untrusted=False)
        duration_ms = int((time.monotonic() - started) * 1000)

        if len(result.output) > settings.tool_output_limit:
            from app.agent.tools import truncate_middle

            result.output = truncate_middle(result.output, settings.tool_output_limit)

        if result.untrusted and result.ok:
            self.ctx.tainted = True

        await self._emit_tool_result(call, result, duration_ms, decision.risk)
        audit(
            "tool_result",
            session=self.session_id,
            tool=call.name,
            ok=result.ok,
            ms=duration_ms,
            meta=result.meta,
        )
        save_message(self.session_id, "tool", result.output, tool_name=call.name)
        return result.output, result.untrusted

    async def _emit_tool_result(
        self, call: ToolCall, result: ToolResult, duration_ms: int, risk: str
    ) -> None:
        await self.emit(
            {
                "type": "tool_result",
                "id": call.id,
                "name": call.name,
                "ok": result.ok,
                "output": result.output,
                "ms": duration_ms,
                "risk": risk,
            }
        )


def format_args(arguments: dict[str, Any]) -> str:
    return json.dumps(arguments, ensure_ascii=False)
