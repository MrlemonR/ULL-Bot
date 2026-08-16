"""LiteLLM proxy istemcisi: streaming + tool call biriktirme.

Orchestrator sağlayıcıdan habersiz kalır (spec §1) — burada sadece
OpenAI-uyumlu `/chat/completions` konuşulur, hangi modelin arkasında kim
olduğu `config/litellm.*.yaml` işidir.
"""

from __future__ import annotations

import json
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


class LLMError(RuntimeError):
    pass


class LLMClient(Protocol):
    async def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        on_token: TokenCallback,
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
    ) -> LLMResponse:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": True,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        response = LLMResponse(model=self.model)
        pending: dict[int, ToolCall] = {}

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=payload,
            ) as resp:
                if resp.status_code >= 400:
                    body = (await resp.aread()).decode("utf-8", errors="replace")
                    raise LLMError(f"LiteLLM {resp.status_code}: {body[:500]}")
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
                    if isinstance(chunk, dict) and chunk.get("error"):
                        raise LLMError(str(chunk["error"])[:500])
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

        response.tool_calls = [
            call for _, call in sorted(pending.items()) if call.name
        ]
        for position, call in enumerate(response.tool_calls):
            if not call.id:
                call.id = f"call_{position}"
        return response
