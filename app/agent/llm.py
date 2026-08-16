"""LiteLLM proxy istemcisi: streaming, tool call biriktirme, kota sinyalleri.

Orchestrator sağlayıcıdan habersiz kalır (spec §1) — burada sadece
OpenAI-uyumlu `/chat/completions` konuşulur, hangi modelin arkasında kim
olduğu `config/litellm.*.yaml` işidir. İstemcinin sağlayıcı hakkında bildiği
tek şey, çağıranın verdiği `provider` etiketi: kota sayacına ve 429
işaretine yazmak için gerekiyor.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Protocol

import httpx

from app.settings import settings

TokenCallback = Callable[[str], Awaitable[None]]


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str = ""

    def parsed_arguments(self) -> dict[str, Any] | str:
        """Argümanları çöz; bozuksa ham metni döndür (döngü hata mesajı üretir)."""
        try:
            value = json.loads(self.arguments or "{}")
        except json.JSONDecodeError:
            return self.arguments
        return value if isinstance(value, dict) else {"value": value}


@dataclass
class LLMResponse:
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    model: str = ""
    finish_reason: str = ""
    provider: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: int = 0


class LLMError(RuntimeError):
    pass


class RateLimited(LLMError):
    """429 — sağlayıcı limiti doldu. `retry_after` varsa cooldown süresi."""

    def __init__(self, message: str, *, provider: str = "", retry_after: str | None = None) -> None:
        super().__init__(message)
        self.provider = provider
        self.retry_after = retry_after


class LLMClient(Protocol):
    async def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        on_token: TokenCallback,
        *,
        model: str | None = None,
        provider: str = "",
    ) -> LLMResponse: ...


class LiteLLMClient:
    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        timeout: float = 180.0,
    ) -> None:
        self.base_url = (base_url or settings.litellm_base_url).rstrip("/")
        self.api_key = api_key or settings.litellm_master_key
        self.model = model or settings.litellm_model
        self.timeout = timeout

    async def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        on_token: TokenCallback,
        *,
        model: str | None = None,
        provider: str = "",
    ) -> LLMResponse:
        chosen_model = model or self.model
        payload: dict[str, Any] = {
            "model": chosen_model,
            "messages": messages,
            "stream": True,
            # Son chunk'ta token sayımını da iste — kota sayacı buna dayanıyor.
            # Desteklemeyen sağlayıcıda LiteLLM `drop_params` ile atıyor.
            "stream_options": {"include_usage": True},
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        response = LLMResponse(model=chosen_model, provider=provider)
        pending: dict[int, ToolCall] = {}
        started = time.monotonic()

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=payload,
            ) as resp:
                if resp.status_code == 429:
                    body = (await resp.aread()).decode("utf-8", errors="replace")
                    raise RateLimited(
                        f"LiteLLM 429: {body[:300]}",
                        provider=provider,
                        retry_after=resp.headers.get("retry-after"),
                    )
                if resp.status_code >= 400:
                    body = (await resp.aread()).decode("utf-8", errors="replace")
                    raise LLMError(f"LiteLLM {resp.status_code}: {body[:500]}")

                self._record_headers(provider, resp.headers)

                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    raw = line[len("data: ") :].strip()
                    if raw == "[DONE]":
                        break
                    try:
                        chunk = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(chunk, dict):
                        continue
                    if chunk.get("error"):
                        raise LLMError(str(chunk["error"])[:500])

                    usage = chunk.get("usage")
                    if isinstance(usage, dict):
                        response.prompt_tokens = int(usage.get("prompt_tokens") or 0)
                        response.completion_tokens = int(usage.get("completion_tokens") or 0)

                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    choice = choices[0]
                    if choice.get("finish_reason"):
                        response.finish_reason = choice["finish_reason"]
                    delta = choice.get("delta") or {}

                    content = delta.get("content")
                    if content:
                        response.content += content
                        await on_token(content)

                    for item in delta.get("tool_calls") or []:
                        index = item.get("index", 0)
                        call = pending.setdefault(index, ToolCall(id="", name=""))
                        if item.get("id"):
                            call.id = item["id"]
                        function = item.get("function") or {}
                        if function.get("name"):
                            call.name = function["name"]
                        if function.get("arguments"):
                            call.arguments += function["arguments"]

        response.latency_ms = int((time.monotonic() - started) * 1000)
        response.tool_calls = [call for _, call in sorted(pending.items()) if call.name]
        for position, call in enumerate(response.tool_calls):
            if not call.id:
                call.id = f"call_{position}"
        return response

    def _record_headers(self, provider: str, headers: Any) -> None:
        """Sağlayıcının kota header'larını sakla — sayacı düzeltmek için."""
        if not provider:
            return
        try:
            from app.quota.probes import record_response_headers

            record_response_headers(provider, dict(headers))
        except Exception:  # kota kaydı sohbeti düşürmemeli
            pass
