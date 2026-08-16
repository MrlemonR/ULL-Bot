"""Sağlayıcıdan canlı kota çekme (spec §4.2, madde 2 ve 3).

İki kaynak var ve ikisi de "otorite" kabul edilir, yerel sayacı düzeltir:

- **Probe:** OpenRouter'ın `GET /api/v1/key` ucu.
- **Cevap header'ları:** Groq her cevapta `x-ratelimit-*` döndürüyor.

Üçüncü sinyal 429: sağlayıcı `cooldown_until` ile işaretlenir, `Retry-After`
varsa o kullanılır.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from app.quota.models import get_quota_config
from app.quota.state import get_state, iso, save_probe, set_cooldown, utc_now
from app.safety.audit import audit
from app.settings import settings

OPENROUTER_KEY_URL = "https://openrouter.ai/api/v1/key"
PROBE_TIMEOUT_SECONDS = 15

# "2m59.56s", "1.5s", "88ms" gibi süreleri saniyeye çevirmek için.
_DURATION_PATTERN = re.compile(r"(?:(\d+(?:\.\d+)?)h)?(?:(\d+(?:\.\d+)?)m(?!s))?(?:(\d+(?:\.\d+)?)s)?(?:(\d+(?:\.\d+)?)ms)?")


def parse_duration(text: str | None) -> float | None:
    """Groq'un reset header'ları ("2m59.56s") saniyeye."""
    if not text:
        return None
    text = text.strip()
    try:
        return float(text)  # düz saniye
    except ValueError:
        pass
    match = _DURATION_PATTERN.fullmatch(text)
    if not match or not any(match.groups()):
        return None
    hours, minutes, seconds, millis = (float(g) if g else 0.0 for g in match.groups())
    return hours * 3600 + minutes * 60 + seconds + millis / 1000


def parse_retry_after(value: str | None, *, default: int) -> int:
    """`Retry-After`: saniye ya da HTTP tarihi olabilir."""
    if not value:
        return default
    value = value.strip()
    if value.isdigit():
        return max(1, int(value))
    try:
        moment = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return default
    if moment is None:
        return default
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return max(1, int((moment - utc_now()).total_seconds()))


def _int(value: Any) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def parse_groq_headers(headers: dict[str, str], *, now: datetime | None = None) -> list[dict[str, Any]]:
    """Groq'un `x-ratelimit-*` header'larını pencerelere eşle.

    Groq'un dokümante ettiği anlam (2026-08-16):
      - `x-ratelimit-*-requests` -> GÜNLÜK istek limiti
      - `x-ratelimit-*-tokens`   -> DAKİKALIK token limiti
    İkisi farklı pencereye ait; o yüzden ayrı kayıtlar üretiliyor.
    """
    now = now or utc_now()
    lower = {key.lower(): value for key, value in headers.items()}
    entries: list[dict[str, Any]] = []

    limit_requests = _int(lower.get("x-ratelimit-limit-requests"))
    remaining_requests = _int(lower.get("x-ratelimit-remaining-requests"))
    if limit_requests is not None or remaining_requests is not None:
        entries.append(
            {
                "window": "day",
                "limit_requests": limit_requests,
                "remaining_requests": remaining_requests,
                "resets_in": parse_duration(lower.get("x-ratelimit-reset-requests")),
                "ts": iso(now),
            }
        )

    limit_tokens = _int(lower.get("x-ratelimit-limit-tokens"))
    remaining_tokens = _int(lower.get("x-ratelimit-remaining-tokens"))
    if limit_tokens is not None or remaining_tokens is not None:
        entries.append(
            {
                "window": "minute",
                "limit_tokens": limit_tokens,
                "remaining_tokens": remaining_tokens,
                "resets_in": parse_duration(lower.get("x-ratelimit-reset-tokens")),
                "ts": iso(now),
            }
        )
    return entries


def record_response_headers(provider: str, headers: dict[str, str]) -> None:
    """Cevap header'larında kota bilgisi varsa sakla (probe: response_headers)."""
    quota = get_quota_config().get(provider)
    if quota is None or quota.probe != "response_headers":
        return
    entries = parse_groq_headers(headers)
    if not entries:
        return
    state = get_state(provider)
    payload = dict(state.probe_payload)
    payload["live"] = entries
    save_probe(provider, payload, health=state.health if state.health != "down" else "down")


async def probe_openrouter() -> dict[str, Any] | None:
    """`GET /api/v1/key` — kredi kullanımı ve hesabın ücretsiz katmanda olup olmadığı.

    Dikkat: bu uç **kredi** kullanımını söyler, ":free" modellerin günlük istek
    sayacını değil. Buradan alınan asıl bilgi `is_free_tier`: hesap hiç kredi
    almadıysa günlük ücretsiz istek limiti 50, aldıysa 1000 (bkz. quotas.yaml).
    İstek sayımı yerel sayaçtan gelir.
    """
    api_key = settings.openrouter_api_key.strip()
    if not api_key:
        return None
    try:
        async with httpx.AsyncClient(timeout=PROBE_TIMEOUT_SECONDS) as client:
            response = await client.get(
                OPENROUTER_KEY_URL, headers={"Authorization": f"Bearer {api_key}"}
            )
            response.raise_for_status()
            body = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        audit("probe_failed", provider="openrouter", error=str(exc)[:300])
        save_probe("openrouter", {"error": str(exc)[:300]}, health="degraded")
        return None

    data = body.get("data") if isinstance(body, dict) else None
    data = data if isinstance(data, dict) else {}
    is_free_tier = bool(data.get("is_free_tier", True))
    payload = {
        "funded": not is_free_tier,
        "is_free_tier": is_free_tier,
        "usage": data.get("usage"),
        "usage_daily": data.get("usage_daily"),
        "limit": data.get("limit"),
        "limit_remaining": data.get("limit_remaining"),
        "raw": data,
    }
    save_probe("openrouter", payload, health="ok")
    audit("probe_ok", provider="openrouter", funded=payload["funded"])
    return payload


async def probe_all() -> dict[str, Any]:
    """Probe'u olan tüm sağlayıcıları yokla."""
    results: dict[str, Any] = {}
    for name, quota in get_quota_config().providers.items():
        if quota.probe == "openrouter_key_endpoint":
            results[name] = await probe_openrouter()
    return results


def record_rate_limit(
    provider: str, *, retry_after: str | None = None, note: str = "429"
) -> datetime:
    """429 geldi: sağlayıcıyı cooldown'a al (spec §4.2, madde 3)."""
    config = get_quota_config()
    seconds = parse_retry_after(retry_after, default=config.default_cooldown_seconds)
    until = set_cooldown(provider, seconds, note=note)
    audit("rate_limited", provider=provider, cooldown_seconds=seconds, until=iso(until))
    return until
