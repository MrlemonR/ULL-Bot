"""Ajan döngüsü: model → tool_call → politika → (onay) → araç → model (spec §6.1).

Döngü UI'dan bağımsızdır: dışarıyla iki callback üzerinden konuşur —
`emit` (olay yayınla) ve `approve` (kullanıcıya sor, cevabı bekle). Bu sayede
testlerde WebSocket olmadan, sahte bir LLM istemcisiyle çalıştırılabilir.
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable

from app.agent.llm import LLMClient, LLMError, LiteLLMClient, RateLimited, ToolCall
from app.agent.prompts import system_prompt, wrap_tool_result
from app.agent.tools import get_tool, tool_schemas
from app.agent.tools.base import ToolContext, ToolResult
from app.memory.store import ensure_session, load_history, save_message
from app.quota.probes import record_rate_limit
from app.quota.tracker import record_usage
from app.router.classifier import Classification, classify_cached
from app.router.selector import NoProviderAvailable, Selection, choose
from app.safety.audit import audit
from app.settings import settings

# Aynı araç çağrısı bu kadar kez üst üste başarısız olursa tur durdurulur.
# 3: bir kez şans, bir kez düzeltme denemesi, üçüncüde artık düzelmiyor.
MAX_REPEATED_FAILURES = 3

# --- bağlam bütçesi -------------------------------------------------------
#
# Her adımda konuşmanın TAMAMI modele yeniden gönderiliyor. Araştırma
# turlarında bu hızla patlıyor: `fetch_url` tek başına 20.000 karaktere
# kadar dönebiliyor ve 4-5 adım sonra istek yüz binlerce karakter oluyor.
#
# Ölçülen sonuç: Groq'un ücretsiz katmanı **dakikada 8000 token** veriyor
# (`x-ratelimit-limit-tokens`; gpt-oss-120b ve 20b için aynı). Yani iki
# adımda bütçe doluyor, 429 geliyor ve tur zincirin sonundaki zayıf yerel
# modele düşüyor — kullanıcının gördüğü bütün kötü sonuçların sebebi buydu.
#
# Çözüm: ESKİ araç çıktılarını kırp. Son ikisi tam kalıyor (model üzerinde
# çalıştığı veriyi tam görsün), daha eskiler baş tarafından kısaltılıyor —
# başlıklar ve adresler orada, yani model neyi zaten okuduğunu ve hangi
# sayfaya döneceğini hâlâ biliyor.
TOOL_RESULTS_KEPT_FULL = 2
TOOL_RESULT_TRIM_CHARS = 700
_TRIM_MARK = "\n[… bu araç çıktısının devamı kısaltıldı (bağlam bütçesi) …]"

# 429'dan sonra sağlayıcı bu süre içinde geri geliyorsa BEKLE, zayıf modele
# düşme. Groq TPM aşımında çoğu zaman "5 saniye" diyor; 5 saniye beklemek,
# turu araştırmayı yürütemeyen bir modele devretmekten iyi. Daha uzun
# cooldown'larda (60 sn) beklemiyoruz — kullanıcı donmuş sanır.
SHORT_COOLDOWN_WAIT = 12.0

Emitter = Callable[[dict[str, Any]], Awaitable[None]]
Approver = Callable[[dict[str, Any]], Awaitable[bool]]


def trim_context(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Eski araç çıktılarını kısalt (yukarıdaki bağlam bütçesi notu).

    Girdi DEĞİŞTİRİLMEZ: döngü kendi tam geçmişini korur, kırpma yalnızca
    modele giden kopyada olur. Böylece bir sonraki adımda "son iki çıktı"
    yine tam hâlinden hesaplanır.
    """
    tool_positions = [i for i, message in enumerate(messages) if message.get("role") == "tool"]
    keep_full = set(tool_positions[-TOOL_RESULTS_KEPT_FULL:])

    trimmed: list[dict[str, Any]] = []
    for index, message in enumerate(messages):
        content = message.get("content")
        if (
            message.get("role") == "tool"
            and index not in keep_full
            and isinstance(content, str)
            and len(content) > TOOL_RESULT_TRIM_CHARS
        ):
            message = {**message, "content": content[:TOOL_RESULT_TRIM_CHARS] + _TRIM_MARK}
        trimmed.append(message)
    return trimmed


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
        task_type: str | None = None,
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
        # Aynı başarısız araç çağrısı kaç kez tekrarlandı? (bkz. `_stuck_on`)
        #
        # Neden gerekli: canlı testte zayıf bir model `web_search`i BOŞ
        # sorguyla çağırdı, araç "reddedildi" dedi, model aynı çağrıyı
        # tekrarladı — ve bu adım limiti dolana kadar sürdü. Kullanıcı
        # dakikalarca cevap bekledi, kota boşa gitti. Reddedilen bir çağrı
        # kendi başına döngüyü durdurmuyor; bu sayaç durduruyor.
        self._call_failures: dict[str, int] = {}
        # Bu oturumda hâlâ hangi sağlayıcıyı kullanıyoruz (model devri rozeti için).
        self.current_provider: str = ""
        # Bu turun görev tipi (spec §5.1) — `run()` başında bir kere belirlenir.
        self.turn_task_type: str = "default"
        # Testler/harici çağıranlar için: verilirse sınıflandırıcı hiç
        # çalışmaz, bu tip zorlanır. Üretimde kullanılmaz (`None` kalır).
        self._forced_task_type = task_type

    # --- ana döngü --------------------------------------------------------

    async def run(self, user_message: str) -> str:
        ensure_session(self.session_id)
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt(str(self.ctx.cwd))}
        ]
        messages.extend(load_history(self.session_id))
        messages.append({"role": "user", "content": user_message})
        save_message(self.session_id, "user", user_message)

        if self._forced_task_type is not None:
            classification = Classification(
                self._forced_task_type, 1.0, "zorlandı (test/harici override)"
            )
        else:
            classification = classify_cached(self.session_id, user_message)
        self.turn_task_type = classification.task_type
        await self.emit(
            {
                "type": "classification",
                "task_type": classification.task_type,
                "confidence": classification.confidence,
                "reason": classification.reason,
            }
        )

        audit(
            "turn_start",
            session=self.session_id,
            dry_run=self.ctx.dry_run,
            cwd=str(self.ctx.cwd),
            message=user_message[:500],
            task_type=classification.task_type,
        )

        budget = self.max_steps
        final_text = ""

        while True:
            self.steps_used += 1
            await self.emit({"type": "step", "step": self.steps_used})

            async def on_token(text: str) -> None:
                await self.emit({"type": "token", "content": text})

            # İlk adım turun sınıflandırmasını kullanır; sonraki adımlar
            # ajan döngüsünün ara adımıdır (tool sonucu değerlendirme) —
            # spec §5.1/§5.2: bu her zaman `tool_use`.
            step_task_type = self.turn_task_type if self.steps_used == 1 else "tool_use"

            try:
                response = await self._call_model(messages, on_token, step_task_type)
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

            stuck = self._stuck_on()
            if stuck is not None:
                audit("stuck_loop", session=self.session_id, call=stuck, steps=self.steps_used)
                fallback = (
                    f"Aynı çağrı ({stuck}) {MAX_REPEATED_FAILURES} kez üst üste "
                    "başarısız oldu ve her seferinde aynı hatayı verdi — işlem "
                    "durduruldu. Bu genellikle modelin aracı yanlış argümanla "
                    "çağırmasından olur; isteği biraz daha açık yazıp tekrar dene."
                )
                return await self._wrap_up(messages, on_token, fallback)

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
                    fallback = (
                        f"Adım limitine ({budget}) ulaşıldı, kullanıcı devam etmek "
                        "istemedi. İşlem yarıda kesildi."
                    )
                    return await self._wrap_up(messages, on_token, fallback)
                budget += self.max_steps

    # --- turu toparlama ---------------------------------------------------

    async def _wrap_up(
        self, messages: list[dict[str, Any]], on_token, fallback: str
    ) -> str:
        """Tur yarıda kesildi — eldeki verilerle YİNE DE bir cevap yazdır.

        Neden: canlı iki turda da araştırmanın kendisi başarılıydı. Model
        Razer Barracuda X, Logitech G435 ve Corsair HS55'i bulmuş, fiyatlarını
        aratmıştı. Sonra zincirin sonundaki küçük model aynı sorguyu
        tekrarlamaya başladı, döngü koruması turu kesti ve kullanıcı
        **topladığı onca verinin hiçbirini görmedi** — sadece "işlem
        durduruldu" yazısını gördü. En sinir bozucu son bu.

        Bu yüzden kesme anında son bir çağrı yapılıyor: araçlar KAPALI
        (`tools=False`), tek iş eldekini yazmak. Araçlar kapalı olduğu için
        model yeniden döngüye giremiyor.

        Bu çağrı da başarısız olursa (sağlayıcı yok, hata, boş cevap)
        `fallback` metnine düşülüyor — yani davranış hiçbir durumda
        eskisinden kötü değil.
        """
        messages = [
            *messages,
            {
                "role": "user",
                "content": (
                    "DUR — başka araç çağırma. Yukarıda topladığın bilgilerle "
                    "kullanıcının sorusunu ŞİMDİ cevapla. Karşılaştırma "
                    "istendiyse markdown tablosu kullan. Eksik kalan bir şey "
                    "varsa cevabın sonunda tek cümleyle belirt; eksik diye "
                    "cevapsız bırakma ve bilgi UYDURMA."
                ),
            },
        ]
        # Önce `reasoning` zinciri (özet yazmak düşünme işi), o tükendiyse
        # `tool_use` zinciri.
        #
        # Neden iki zincir: `reasoning` = openrouter + gemini. Canlı olarak
        # ikisi de aynı saniyede 429 yedi ve toparlama "uygun sağlayıcı
        # kalmadı" deyip hiç cevap üretmeden pes etti — oysa `tool_use`
        # zincirinin sonundaki yerel model ayaktaydı. Yerel modelin özeti
        # zayıf olabilir ama toplanan verinin tamamen çöpe gitmesinden iyi;
        # zaten araçlar kapalı olduğu için döngüye giremiyor.
        response = None
        errors: list[str] = []
        for chain in ("reasoning", "tool_use"):
            try:
                response = await self._call_model(messages, on_token, chain, tools=False)
                break
            except Exception as exc:  # sağlayıcı yok, ağ hatası, hepsi boş döndü…
                errors.append(f"{chain}: {exc}")

        if response is None:
            audit("wrap_up_failed", session=self.session_id, error="; ".join(errors)[:300])
            await self.emit({"type": "stopped", "message": fallback})
            save_message(self.session_id, "assistant", fallback)
            return fallback

        final_text = (response.content or "").strip()
        if not final_text:
            await self.emit({"type": "stopped", "message": fallback})
            save_message(self.session_id, "assistant", fallback)
            return fallback

        save_message(self.session_id, "assistant", final_text, model=response.model)
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
        audit("turn_end", session=self.session_id, steps=self.steps_used, wrapped_up=True)
        return final_text

    # --- model çağrısı ve sağlayıcı devri ---------------------------------

    async def _call_model(
        self,
        messages: list[dict[str, Any]],
        on_token,
        task_type: str = "default",
        *,
        tools: bool = True,
    ) -> Any:
        """Sağlayıcı seç, çağır, 429 alırsan sessizce bir sonrakine geç.

        Spec §9 Faz 3 kabul kriteri: "bir sağlayıcının limitini kasten doldur;
        sistem sessizce diğerine geçsin ve UI'da sebebi görünsün." Sessizce =
        kullanıcıya hata gösterilmez; "UI'da sebebi görünür" = `model_switch`
        olayı gönderilir.

        Faz 4: `task_type` görev tipine göre zinciri belirler; o zincir yine
        kota/cooldown süzgecinden geçer (spec §9 Faz 4 kabul notu — görev
        tipi seçimi elemenin ÜSTÜNE değil, ALTINA eklenir).
        """
        attempted: set[str] = set()
        # Kısa cooldown'da beklediğimiz sağlayıcılar — her biri için bir kez.
        waited: set[str] = set()
        last_error: Exception | None = None

        for _ in range(max(1, settings.max_provider_attempts)):
            try:
                selection = choose(task_type=task_type, exclude=attempted)
            except NoProviderAvailable as exc:
                raise LLMError(str(exc)) from exc

            await self._announce(selection)

            try:
                response = await self.llm.complete(
                    trim_context(messages),
                    tool_schemas() if tools else [],
                    on_token,
                    model=selection.model,
                    provider=selection.provider,
                )
            except RateLimited as exc:
                until = record_rate_limit(selection.provider, retry_after=exc.retry_after)
                record_usage(
                    provider=selection.provider,
                    model=selection.model,
                    status="rate_limited",
                    session_id=self.session_id,
                    task_type=task_type,
                )
                last_error = exc
                audit(
                    "provider_rate_limited",
                    session=self.session_id,
                    provider=selection.provider,
                    retry_after=exc.retry_after,
                )

                # Sağlayıcı birazdan geri geliyorsa BEKLE, elemeyi erteleme.
                # Groq TPM aşımında genelde "5 saniye" diyor; beklemek, turu
                # araştırmayı yürütemeyen yerel modele devretmekten iyi.
                # Sağlayıcı başına bir kez — yoksa aynı yerde tur boyu döneriz.
                wait = (until - datetime.now(until.tzinfo)).total_seconds()
                if 0 < wait <= SHORT_COOLDOWN_WAIT and selection.provider not in waited:
                    waited.add(selection.provider)
                    audit(
                        "provider_short_wait",
                        session=self.session_id,
                        provider=selection.provider,
                        seconds=round(wait, 1),
                    )
                    await asyncio.sleep(wait + 0.3)
                    continue

                attempted.add(selection.provider)
                continue
            except LLMError as exc:
                # Sağlayıcıya özgü bir hata (502, model yok vb.) — onu bu tur
                # için ele ve sıradakini dene.
                record_usage(
                    provider=selection.provider,
                    model=selection.model,
                    status="error",
                    session_id=self.session_id,
                    task_type=task_type,
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

            # Boş cevap = sağlayıcı hatası, "cevap yok" değil.
            #
            # OpenRouter'ın ücretsiz modeli ara sıra ne metin ne de araç
            # çağrısı döndürüyor (NEXT_PHASE.md §7). Bu cevabı olduğu gibi
            # kabul edersek döngü `done` yayımlayıp BOŞ metinle bitiyor:
            # kullanıcı araçların çalıştığını görüyor, sonra hiçbir şey
            # gelmiyor ve neden olduğunu anlamıyor. Canlı görüldü — "4 adım
            # sürdü, sonuç vermedi".
            #
            # Diğer sağlayıcı hataları gibi ele alıyoruz: bu sağlayıcıyı tur
            # için ele, sıradakini dene. Hepsi boş dönerse aşağıdaki
            # `LLMError` fırlıyor ve kullanıcı sessizlik yerine sebebi görüyor.
            if not response.tool_calls and not (response.content or "").strip():
                record_usage(
                    provider=selection.provider,
                    model=selection.model,
                    prompt_tokens=response.prompt_tokens,
                    completion_tokens=response.completion_tokens,
                    latency_ms=response.latency_ms,
                    status="error",
                    session_id=self.session_id,
                    task_type=task_type,
                )
                attempted.add(selection.provider)
                last_error = LLMError(f"{selection.provider} boş cevap döndürdü")
                audit(
                    "provider_empty_response",
                    session=self.session_id,
                    provider=selection.provider,
                    model=selection.model,
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
                task_type=task_type,
            )
            return response

        # Bu mesajı kullanıcı okuyor: teknik dökümden önce NE YAPACAĞINI söyle.
        # Ücretsiz katmanlarda en sık sebep kota; günlük kotalar gece
        # yenileniyor, dakikalık limitler birkaç dakikada.
        raise LLMError(
            "Şu an kullanılabilir bir model sağlayıcısı yok — ücretsiz kotalar "
            "dolmuş görünüyor. Dakikalık limitse birkaç dakika sonra, günlük "
            "kotaysa yarın tekrar dene; kota panelinden durumu görebilirsin. "
            f"(Denenenler: {', '.join(sorted(attempted)) or 'yok'} — son hata: {last_error})"
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

    def _record_failure(self, call: ToolCall, arguments: Any) -> None:
        """Başarısız bir çağrıyı say — aynı çağrı tekrarlanıyor mu diye."""
        try:
            signature = f"{call.name}({json.dumps(arguments, sort_keys=True, ensure_ascii=False)})"
        except (TypeError, ValueError):
            signature = f"{call.name}({arguments!r})"
        self._call_failures[signature] = self._call_failures.get(signature, 0) + 1

    def _stuck_on(self) -> str | None:
        """Takıldığımız çağrının imzası (varsa).

        Başarılı bir çağrı sayacı sıfırlamıyor bilerek: model iki aracı
        dönüşümlü çağırıp birinde sürekli başarısız olabiliyor ve bu da
        aynı ölçüde kısır bir döngü.
        """
        for signature, count in self._call_failures.items():
            if count >= MAX_REPEATED_FAILURES:
                return signature
        return None

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
            self._record_failure(call, str(arguments))
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
            self._record_failure(call, arguments)
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

        if not result.ok:
            self._record_failure(call, arguments)
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
