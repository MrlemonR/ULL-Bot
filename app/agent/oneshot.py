"""Tek seferlik model çağrısı — araçsız, akışsız, döngüsüz.

Ajan döngüsü (`loop.py`) bir sohbet turunu yönetir: araçlar, onaylar, adım
limiti. Ama Faz 8'in iki ihtiyacı bunların hiçbirini istemiyor:

- bir maili özetle,
- kuralın kararsız kaldığı birkaç maili kategorilere ayır.

İkisi de "bir istem gönder, metni al" işi. Yine de sağlayıcı seçimi, kota
sayımı ve 429 sonrası devir aynı olmalı — yoksa mail özetleri kotayı
görünmez şekilde tüketir ve panel yalan söyler. Bu modül tam olarak o
ortak kısmı yeniden kullanır (`selector.choose` + `record_usage` +
`record_rate_limit`), fazlasını değil.

`loop.py`'deki `_call_model` ile akrabadır ama onu değiştirmedik: o metot
`emit`/`self.current_provider` üzerinden UI olayları da üretiyor, bu ise
sessiz. Ortak bir tabana indirmek Faz 8'in kapsamı dışında bırakıldı
(bkz. DECISIONS.md).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.agent.llm import LiteLLMClient, LLMError, RateLimited
from app.quota.probes import record_rate_limit
from app.quota.tracker import record_usage
from app.router.selector import NoProviderAvailable, choose
from app.settings import settings


@dataclass
class OneShotResult:
    text: str
    model: str
    provider: str
    tokens: int = 0
    ms: int = 0


async def _noop(_: str) -> None:
    return None


async def complete_once(
    messages: list[dict[str, Any]],
    *,
    task_type: str = "trivial",
    session_id: str = "",
    max_attempts: int | None = None,
) -> OneShotResult:
    """Bir istem gönder, metni döndür. Sağlayıcı devri dahil.

    `task_type` varsayılanı `trivial`: `routing.yaml`da bu zincirin başında
    local model var, yani mail özetleri önce ücretsiz/yerel modelde denenir
    ve bulut kotasını hiç tüketmez.

    Hata durumunda `LLMError` fırlatır — çağıran taraf bunu kullanıcıya
    gösterilebilir bir mesaja çevirmeli.
    """
    client = LiteLLMClient()
    attempted: set[str] = set()
    last_error: Exception | None = None
    attempts = max_attempts or max(1, settings.max_provider_attempts)

    for _ in range(attempts):
        try:
            selection = choose(task_type=task_type, exclude=attempted)
        except NoProviderAvailable as exc:
            raise LLMError(str(exc)) from exc

        try:
            response = await client.complete(
                messages, None, _noop, model=selection.model, provider=selection.provider
            )
        except RateLimited as exc:
            record_rate_limit(selection.provider, retry_after=exc.retry_after)
            record_usage(
                provider=selection.provider, model=selection.model,
                status="rate_limited", session_id=session_id, task_type=task_type,
            )
            attempted.add(selection.provider)
            last_error = exc
            continue
        except LLMError as exc:
            record_usage(
                provider=selection.provider, model=selection.model,
                status="error", session_id=session_id, task_type=task_type,
            )
            attempted.add(selection.provider)
            last_error = exc
            continue

        record_usage(
            provider=selection.provider,
            model=selection.model,
            prompt_tokens=response.prompt_tokens,
            completion_tokens=response.completion_tokens,
            latency_ms=response.latency_ms,
            status="ok",
            session_id=session_id,
            task_type=task_type,
        )
        return OneShotResult(
            text=response.content.strip(),
            model=response.model,
            provider=selection.provider,
            tokens=response.prompt_tokens + response.completion_tokens,
            ms=response.latency_ms,
        )

    raise LLMError(
        f"Tüm sağlayıcılar denendi ({', '.join(sorted(attempted)) or 'yok'}), "
        f"sonuncusu: {last_error}"
    )
