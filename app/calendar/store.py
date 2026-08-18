"""Takvim etkinlikleri (SQLite CRUD).

Zaman biçimi kuralı: her şey **ofsetli ISO8601** olarak saklanır
(`2026-08-20T15:00:00+03:00`). Saat dilimsiz ("naive") bir değer geldiğinde
sistemin yerel saat dilimi varsayılır — kullanıcı kendi makinesindeki tek
kişi, "15:00" dediğinde kendi saatini kastediyor. UTC'ye çevirip saklamıyoruz
çünkü hatırlatıcı ve takvim ızgarası yerel saatle çalışıyor, ofseti korumak
yaz saati geçişlerinde de doğru sonucu veriyor.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from app.db.connection import get_connection
from app.settings import settings


def local_tz():
    return datetime.now().astimezone().tzinfo or timezone.utc


def normalize_dt(value: str | datetime | None) -> str:
    """Girdiyi ofsetli ISO8601'e çevir. Boş/bozuksa boş string."""
    if value is None:
        return ""
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).strip()
        if not text:
            return ""
        # "2026-08-20 15:00" gibi boşluklu biçim de kabul edilsin.
        text = text.replace(" ", "T", 1) if " " in text and "T" not in text else text
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            # Sadece tarih verilmiş olabilir: "2026-08-20"
            try:
                parsed = datetime.strptime(text[:10], "%Y-%m-%d")
            except ValueError:
                return ""
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=local_tz())
    return parsed.isoformat()


def _row(row) -> dict[str, Any]:
    event = dict(row)
    event["all_day"] = bool(event.get("all_day"))
    raw = event.get("attendees")
    try:
        event["attendees"] = json.loads(raw) if raw else []
    except (TypeError, json.JSONDecodeError):
        event["attendees"] = []
    return event


def create_event(
    *,
    title: str,
    starts_at: str,
    ends_at: str = "",
    description: str = "",
    location: str = "",
    all_day: bool = False,
    attendees: list[str] | None = None,
    meeting_url: str = "",
    source: str = "manual",
    source_ref: str = "",
    color: str = "",
    reminder_minutes: int | None = None,
    uid: str = "",
) -> dict[str, Any]:
    """Etkinlik oluştur. `starts_at` zorunlu; `ends_at` boşsa +1 saat.

    `uid` verilirse (ICS içe aktarımı) aynı UID'li kayıt GÜNCELLENİR — bir
    davet maili iki kez senkronlandığında takvimde iki kopya oluşmasın.
    """
    start = normalize_dt(starts_at)
    if not start:
        raise ValueError(f"Geçersiz başlangıç zamanı: {starts_at!r}")
    end = normalize_dt(ends_at)
    if not end:
        delta = timedelta(days=1) if all_day else timedelta(hours=1)
        end = (datetime.fromisoformat(start) + delta).isoformat()

    if reminder_minutes is None:
        reminder_minutes = settings.default_reminder_minutes
    event_uid = uid or f"{uuid.uuid4()}@ull-bot"

    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO calendar_events
              (uid, title, description, location, starts_at, ends_at, all_day, attendees,
               meeting_url, source, source_ref, color, reminder_minutes, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
            ON CONFLICT(uid) DO UPDATE SET
              title = excluded.title, description = excluded.description,
              location = excluded.location, starts_at = excluded.starts_at,
              ends_at = excluded.ends_at, all_day = excluded.all_day,
              attendees = excluded.attendees, meeting_url = excluded.meeting_url,
              color = excluded.color, updated_at = datetime('now'),
              -- Zaman değiştiyse hatırlatma yeniden kurulmalı.
              reminded_at = CASE WHEN calendar_events.starts_at != excluded.starts_at
                                 THEN NULL ELSE calendar_events.reminded_at END
            """,
            (
                event_uid, title.strip() or "(başlıksız)", description, location, start, end,
                int(all_day), json.dumps(attendees or [], ensure_ascii=False), meeting_url,
                source, str(source_ref or ""), color, int(reminder_minutes),
            ),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM calendar_events WHERE uid = ?", (event_uid,)).fetchone()
    return _row(row)


def update_event(event_id: int, **fields: Any) -> dict[str, Any] | None:
    """Verilen alanları güncelle. Bilinmeyen alanlar sessizce atlanır."""
    allowed = {
        "title", "description", "location", "starts_at", "ends_at", "all_day",
        "attendees", "meeting_url", "color", "reminder_minutes",
    }
    updates: list[str] = []
    params: list[Any] = []
    for key, value in fields.items():
        if key not in allowed or value is None:
            continue
        if key in ("starts_at", "ends_at"):
            value = normalize_dt(value)
            if not value:
                continue
        elif key == "attendees":
            value = json.dumps(value, ensure_ascii=False)
        elif key in ("all_day",):
            value = int(bool(value))
        elif key == "reminder_minutes":
            value = int(value)
        updates.append(f"{key} = ?")
        params.append(value)

    if not updates:
        return get_event(event_id)

    # Başlangıç değiştiyse hatırlatma bayrağı sıfırlanmalı, yoksa yeni saat
    # için bildirim hiç gitmez.
    if "starts_at" in fields:
        updates.append("reminded_at = NULL")
    updates.append("updated_at = datetime('now')")
    params.append(event_id)

    with get_connection() as conn:
        conn.execute(f"UPDATE calendar_events SET {', '.join(updates)} WHERE id = ?", params)
        conn.commit()
    return get_event(event_id)


def get_event(event_id: int) -> dict[str, Any] | None:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM calendar_events WHERE id = ?", (event_id,)).fetchone()
    return _row(row) if row else None


def delete_event(event_id: int) -> bool:
    with get_connection() as conn:
        cursor = conn.execute("DELETE FROM calendar_events WHERE id = ?", (event_id,))
        conn.commit()
    return cursor.rowcount > 0


def list_events(
    *, start: str = "", end: str = "", limit: int = 500, query: str = ""
) -> list[dict[str, Any]]:
    """Bir aralıktaki etkinlikler (başlangıcına göre artan).

    Aralık testi etkinliğin BAŞLANGICINA bakar; çok günlük etkinliklerin
    ortasındaki günlere düşmesi bu takvimin kapsamı dışında (tekrarlama da
    yok — bkz. `ics.py` docstring).
    """
    clauses: list[str] = []
    params: list[Any] = []
    if start:
        clauses.append("starts_at >= ?")
        params.append(normalize_dt(start))
    if end:
        clauses.append("starts_at < ?")
        params.append(normalize_dt(end))
    if query.strip():
        escaped = query.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped}%"
        clauses.append(
            "(title LIKE ? ESCAPE '\\' OR description LIKE ? ESCAPE '\\' "
            " OR location LIKE ? ESCAPE '\\')"
        )
        params.extend([pattern] * 3)

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(limit)
    with get_connection() as conn:
        rows = conn.execute(
            f"SELECT * FROM calendar_events {where} ORDER BY starts_at ASC LIMIT ?", params
        ).fetchall()
    return [_row(row) for row in rows]


def upcoming(limit: int = 10, *, within_days: int = 30) -> list[dict[str, Any]]:
    """Şu andan itibaren yaklaşan etkinlikler — sohbet ve panel için."""
    now = datetime.now(local_tz())
    return list_events(
        start=now.isoformat(),
        end=(now + timedelta(days=within_days)).isoformat(),
        limit=limit,
    )


def due_reminders(now: datetime | None = None) -> list[dict[str, Any]]:
    """Bildirimi gönderilmesi gereken etkinlikler.

    Koşullar: hatırlatma kapalı değil (`reminder_minutes >= 0`), daha önce
    gönderilmemiş (`reminded_at IS NULL`), ve hatırlatma anı geçmiş ama
    etkinlik henüz bitmemiş. Son koşul önemli: uygulama kapalıyken geçmiş
    bir etkinliğin bildirimi açılışta patlamasın.
    """
    moment = now or datetime.now(local_tz())
    rows = []
    with get_connection() as conn:
        candidates = conn.execute(
            "SELECT * FROM calendar_events "
            "WHERE reminded_at IS NULL AND reminder_minutes >= 0 "
            "  AND starts_at >= ? AND starts_at <= ? "
            "ORDER BY starts_at ASC",
            (
                (moment - timedelta(minutes=5)).isoformat(),
                (moment + timedelta(days=2)).isoformat(),
            ),
        ).fetchall()

    for row in candidates:
        event = _row(row)
        try:
            starts = datetime.fromisoformat(event["starts_at"])
        except (TypeError, ValueError):
            continue
        trigger = starts - timedelta(minutes=int(event["reminder_minutes"] or 0))
        if trigger <= moment:
            rows.append(event)
    return rows


def mark_reminded(event_id: int) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE calendar_events SET reminded_at = ? WHERE id = ?",
            (datetime.now(local_tz()).isoformat(), event_id),
        )
        conn.commit()


def find_by_source(source: str, source_ref: str) -> dict[str, Any] | None:
    """Bir mailden zaten etkinlik oluşturulmuş mu? (çift kayıt önleme)"""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM calendar_events WHERE source = ? AND source_ref = ? LIMIT 1",
            (source, str(source_ref)),
        ).fetchone()
    return _row(row) if row else None


def stats() -> dict[str, Any]:
    now = datetime.now(local_tz())
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    with get_connection() as conn:
        total = conn.execute("SELECT COUNT(*) AS n FROM calendar_events").fetchone()["n"]
        today = conn.execute(
            "SELECT COUNT(*) AS n FROM calendar_events WHERE starts_at >= ? AND starts_at < ?",
            (today_start.isoformat(), (today_start + timedelta(days=1)).isoformat()),
        ).fetchone()["n"]
        week = conn.execute(
            "SELECT COUNT(*) AS n FROM calendar_events WHERE starts_at >= ? AND starts_at < ?",
            (now.isoformat(), (now + timedelta(days=7)).isoformat()),
        ).fetchone()["n"]
    return {"total": total, "today": today, "next_7_days": week}
