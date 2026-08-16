"""`provider_state` tablosu: cooldown, sağlık, son probe verisi (spec §4.4).

Cooldown "bu sağlayıcıyı şu ana kadar kullanma" demektir ve iki yerden gelir:
429 cevabı (`Retry-After`) veya kullanıcının UI'dan manuel devre dışı
bırakması.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from app.db.connection import get_connection

Health = str  # ok | degraded | down


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(moment: datetime) -> str:
    """Sabit biçim: sözlük sırası = zaman sırası (SQL karşılaştırması buna dayanıyor)."""
    return moment.astimezone(timezone.utc).isoformat(timespec="microseconds")


def parse_iso(text: str | None) -> datetime | None:
    if not text:
        return None
    try:
        return datetime.fromisoformat(text).astimezone(timezone.utc)
    except ValueError:
        return None


@dataclass
class ProviderState:
    provider: str
    cooldown_until: datetime | None = None
    last_probe_ts: datetime | None = None
    probe_payload: dict[str, Any] = field(default_factory=dict)
    health: Health = "ok"
    note: str = ""

    def in_cooldown(self, now: datetime | None = None) -> bool:
        if self.cooldown_until is None:
            return False
        return (now or utc_now()) < self.cooldown_until

    def cooldown_seconds_left(self, now: datetime | None = None) -> int:
        if not self.in_cooldown(now):
            return 0
        return int((self.cooldown_until - (now or utc_now())).total_seconds())


def get_state(provider: str) -> ProviderState:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT provider, cooldown_until, last_probe_ts, probe_payload, health, note "
            "FROM provider_state WHERE provider = ?",
            (provider,),
        ).fetchone()
    if row is None:
        return ProviderState(provider=provider)
    payload: dict[str, Any] = {}
    if row["probe_payload"]:
        try:
            payload = json.loads(row["probe_payload"])
        except json.JSONDecodeError:
            payload = {}
    return ProviderState(
        provider=row["provider"],
        cooldown_until=parse_iso(row["cooldown_until"]),
        last_probe_ts=parse_iso(row["last_probe_ts"]),
        probe_payload=payload,
        health=row["health"] or "ok",
        note=row["note"] or "",
    )


def all_states(providers: list[str]) -> dict[str, ProviderState]:
    return {name: get_state(name) for name in providers}


def _upsert(provider: str, **columns: Any) -> None:
    if not columns:
        return
    assignments = ", ".join(f"{key} = ?" for key in columns)
    with get_connection() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO provider_state (provider, health) VALUES (?, 'ok')",
            (provider,),
        )
        conn.execute(
            f"UPDATE provider_state SET {assignments} WHERE provider = ?",
            (*columns.values(), provider),
        )
        conn.commit()


def set_cooldown(provider: str, seconds: int, *, note: str = "", health: Health = "degraded") -> datetime:
    until = utc_now() + timedelta(seconds=max(1, seconds))
    _upsert(provider, cooldown_until=iso(until), note=note, health=health)
    return until


def clear_cooldown(provider: str, *, note: str = "") -> None:
    _upsert(provider, cooldown_until=None, note=note, health="ok")


def set_health(provider: str, health: Health, *, note: str = "") -> None:
    _upsert(provider, health=health, note=note)


def disable(provider: str, *, note: str = "kullanıcı devre dışı bıraktı") -> None:
    """UI'daki 'sağlayıcıyı devre dışı bırak' düğmesi (spec §7.2).

    Cooldown'dan farkı: süresi yok, kullanıcı tekrar açana kadar kapalı.
    """
    _upsert(provider, health="down", note=note)


def enable(provider: str) -> None:
    _upsert(provider, health="ok", note="", cooldown_until=None)


def save_probe(provider: str, payload: dict[str, Any], *, health: Health = "ok") -> None:
    _upsert(
        provider,
        last_probe_ts=iso(utc_now()),
        probe_payload=json.dumps(payload, ensure_ascii=False),
        health=health,
    )
