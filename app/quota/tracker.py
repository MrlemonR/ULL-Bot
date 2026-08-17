"""Yerel kota sayacı: her istek `usage_events`'e yazılır (spec §4.2, madde 1).

Bu, kota API'si olmayan sağlayıcılar (Gemini, Cerebras) için **tek** bilgi
kaynağı. Probe'u olan sağlayıcılarda ise probe otorite kabul edilir ve buradaki
sayımı düzeltir — drift olur, olacak (spec §4.2).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from app.db.connection import get_connection
from app.quota.models import (
    QuotaConfig,
    Window,
    WindowUsage,
    get_quota_config,
    window_reset_at,
    window_start,
)
from app.quota.state import ProviderState, get_state, iso, utc_now

# Probe verisi bu süreden eskiyse artık "canlı" sayılmaz, yerel sayaca dönülür.
LIVE_DATA_MAX_AGE_SECONDS = 900


def record_usage(
    *,
    provider: str,
    model: str,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    latency_ms: int | None = None,
    status: str = "ok",
    session_id: str | None = None,
    task_type: str | None = None,
    ts: datetime | None = None,
) -> None:
    """Tek bir istek kaydı. Sayaç bozulsa bile sohbeti düşürmez."""
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO usage_events "
            "(ts, provider, model, task_type, prompt_tokens, completion_tokens, "
            " latency_ms, status, session_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                iso(ts or utc_now()),
                provider,
                model,
                task_type,
                int(prompt_tokens or 0),
                int(completion_tokens or 0),
                latency_ms,
                status,
                session_id,
            ),
        )
        conn.commit()


def usage_in_window(
    provider: str,
    window: Window,
    reset: str,
    *,
    now: datetime | None = None,
    count_failures: bool = False,
) -> tuple[int, int]:
    """(istek sayısı, token sayısı) — pencerenin başlangıcından itibaren.

    Varsayılan olarak yalnızca sağlayıcıya ulaşan istekler sayılır: bizim
    tarafımızda oluşan hatalar (`status='error'`) kota tüketmez. 429'lar ise
    sayılır — sağlayıcıya gidip reddedilmiştir.
    """
    now = now or utc_now()
    start = window_start(window, reset, now)
    statuses = ("ok", "rate_limited")
    query = (
        "SELECT COUNT(*) AS requests, "
        "       COALESCE(SUM(prompt_tokens + completion_tokens), 0) AS tokens "
        "FROM usage_events WHERE provider = ? AND ts >= ?"
    )
    params: list[Any] = [provider, iso(start)]
    if not count_failures:
        query += f" AND status IN ({','.join('?' * len(statuses))})"
        params.extend(statuses)

    with get_connection() as conn:
        row = conn.execute(query, params).fetchone()
    return int(row["requests"] or 0), int(row["tokens"] or 0)


def _live_override(state: ProviderState, window: Window, now: datetime) -> dict[str, Any] | None:
    """Probe/header'dan gelen canlı veri bu pencere için var ve taze mi?

    Groq tek cevapta iki farklı pencereye ait bilgi döndürüyor (istek=gün,
    token=dakika), o yüzden `live` bir liste.
    """
    live = (state.probe_payload or {}).get("live")
    entries = live if isinstance(live, list) else [live] if isinstance(live, dict) else []
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("window") != window:
            continue
        stamp = entry.get("ts")
        if not stamp:
            continue
        try:
            moment = datetime.fromisoformat(stamp).astimezone(timezone.utc)
        except ValueError:
            continue
        if (now - moment).total_seconds() > LIVE_DATA_MAX_AGE_SECONDS:
            continue
        return entry
    return None


def snapshot(
    provider: str,
    *,
    config: QuotaConfig | None = None,
    state: ProviderState | None = None,
    now: datetime | None = None,
) -> list[WindowUsage]:
    """Sağlayıcının tüm pencerelerdeki durumu — UI ve seçici bunu okur."""
    config = config or get_quota_config()
    quota = config.get(provider)
    if quota is None or not quota.limits:
        return []

    now = now or utc_now()
    state = state or get_state(provider)
    funded = bool((state.probe_payload or {}).get("funded"))

    usages: list[WindowUsage] = []
    for limit in quota.limits:
        requests, tokens = usage_in_window(provider, limit.window, quota.reset, now=now)
        usage = WindowUsage(
            window=limit.window,
            requests=requests,
            tokens=tokens,
            max_requests=limit.requests_cap(funded=funded),
            max_tokens=limit.max_tokens,
            source="local",
            resets_at=window_reset_at(limit.window, quota.reset, now),
        )

        # Sağlayıcının kendi söylediği rakam varsa o kazanır (spec §4.2).
        live = _live_override(state, limit.window, now)
        if live is not None:
            if live.get("limit_requests") is not None:
                usage.max_requests = int(live["limit_requests"])
            if live.get("limit_tokens") is not None:
                usage.max_tokens = int(live["limit_tokens"])
            if live.get("remaining_requests") is not None and usage.max_requests is not None:
                usage.requests = max(0, usage.max_requests - int(live["remaining_requests"]))
            if live.get("remaining_tokens") is not None and usage.max_tokens is not None:
                usage.tokens = max(0, usage.max_tokens - int(live["remaining_tokens"]))
            usage.source = "live"
        usages.append(usage)
    return usages


def free_ratio(
    provider: str,
    *,
    config: QuotaConfig | None = None,
    state: ProviderState | None = None,
    now: datetime | None = None,
) -> float:
    """Sağlayıcının en dar penceresindeki boş kota oranı (0.0 - 1.0)."""
    usages = snapshot(provider, config=config, state=state, now=now)
    if not usages:
        return 1.0
    return min(usage.free_ratio() for usage in usages)


def has_capacity(
    provider: str,
    *,
    reserve_ratio: float | None = None,
    config: QuotaConfig | None = None,
    state: ProviderState | None = None,
    now: datetime | None = None,
) -> bool:
    """Kotanın `reserve_ratio` kadarı acil işler için saklanır (spec §5.3)."""
    config = config or get_quota_config()
    threshold = config.reserve_ratio if reserve_ratio is None else reserve_ratio
    return free_ratio(provider, config=config, state=state, now=now) > threshold


def recent_events(limit: int = 100) -> list[dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT ts, provider, model, prompt_tokens, completion_tokens, "
            "       latency_ms, status FROM usage_events ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def usage_by_day(days: int = 14, *, now: datetime | None = None) -> list[dict[str, Any]]:
    """Faz 7 "kullanım grafiği": gün × sağlayıcı kırılımında istek/token.

    UTC güne göre gruplanıyor — kota pencereleri gibi farklı sağlayıcılara
    göre farklı gece yarısı kullanmak (spec §4.2) burada anlamsız, bu sadece
    bir görselleştirme, kota hesaplaması değil. UI hiç yazılmadı (bkz.
    NEXT_PHASE.md) — bu sadece veriyi üretiyor, `GET /api/usage/graph` bunu
    JSON olarak veriyor.
    """
    now = now or utc_now()
    start = (now - timedelta(days=days - 1)).date().isoformat()
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT substr(ts, 1, 10) AS day, provider, "
            "       COUNT(*) AS requests, "
            "       COALESCE(SUM(prompt_tokens + completion_tokens), 0) AS tokens, "
            "       SUM(CASE WHEN status = 'rate_limited' THEN 1 ELSE 0 END) AS rate_limited, "
            "       SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) AS errors "
            "FROM usage_events "
            "WHERE substr(ts, 1, 10) >= ? "
            "GROUP BY day, provider "
            "ORDER BY day ASC, provider ASC",
            (start,),
        ).fetchall()
    return [dict(row) for row in rows]
