"""Otomasyon kayıtları: tanımlar, adımlar, çalıştırma günlükleri."""

from __future__ import annotations

import json
from typing import Any

from app.db.connection import get_connection
from app.mail.parser import now_utc_iso


def _row(row: Any) -> dict[str, Any]:
    data = dict(row)
    if "allowlist" in data:
        data["allowlist"] = json.loads(data.get("allowlist") or "[]")
    if "action" in data:
        data["action"] = json.loads(data["action"]) if data.get("action") else None
    return data


# --- otomasyonlar -----------------------------------------------------------


def list_automations() -> list[dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT a.*, (SELECT COUNT(*) FROM automation_steps s "
            " WHERE s.automation_id = a.id) AS step_count "
            "FROM automations a ORDER BY a.id"
        ).fetchall()
    return [_row(row) for row in rows]


def get_automation(automation_id: int) -> dict[str, Any] | None:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM automations WHERE id = ?", (automation_id,)).fetchone()
    return _row(row) if row else None


def create_automation(name: str, *, goal: str = "", start_url: str = "",
                      allowlist: list[str] | None = None) -> dict[str, Any]:
    with get_connection() as conn:
        cursor = conn.execute(
            "INSERT INTO automations (name, goal, start_url, allowlist, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (name.strip(), goal.strip(), start_url.strip(),
             json.dumps(allowlist or []), now_utc_iso()),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM automations WHERE id = ?", (cursor.lastrowid,)).fetchone()
    return _row(row)


def update_automation(automation_id: int, **fields: Any) -> dict[str, Any] | None:
    allowed = {"name", "goal", "start_url", "allowlist", "last_run_at", "last_status"}
    sets, params = [], []
    for key, value in fields.items():
        if key not in allowed:
            continue
        sets.append(f"{key} = ?")
        params.append(json.dumps(value) if key == "allowlist" else value)
    if not sets:
        return get_automation(automation_id)
    params.append(automation_id)
    with get_connection() as conn:
        conn.execute(f"UPDATE automations SET {', '.join(sets)} WHERE id = ?", params)
        conn.commit()
    return get_automation(automation_id)


def delete_automation(automation_id: int) -> None:
    with get_connection() as conn:
        # Şema `ON DELETE CASCADE` diyor ama SQLite'ta yabancı anahtarlar
        # bağlantı başına açılıyor; garanti olsun diye elle siliyoruz.
        conn.execute("DELETE FROM automation_steps WHERE automation_id = ?", (automation_id,))
        conn.execute("DELETE FROM automation_runs WHERE automation_id = ?", (automation_id,))
        conn.execute("DELETE FROM automations WHERE id = ?", (automation_id,))
        conn.commit()


# --- adımlar ----------------------------------------------------------------


def list_steps(automation_id: int) -> list[dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM automation_steps WHERE automation_id = ? ORDER BY position",
            (automation_id,),
        ).fetchall()
    return [_row(row) for row in rows]


def replace_steps(automation_id: int, steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Planlayıcının ürettiği adım listesini yaz (eskisinin yerine)."""
    with get_connection() as conn:
        conn.execute("DELETE FROM automation_steps WHERE automation_id = ?", (automation_id,))
        for position, step in enumerate(steps):
            conn.execute(
                "INSERT INTO automation_steps "
                "(automation_id, position, intent, kind, action, status, updated_at) "
                "VALUES (?, ?, ?, ?, ?, 'bekliyor', ?)",
                (automation_id, position, str(step.get("intent") or "").strip(),
                 str(step.get("kind") or "islem"),
                 json.dumps(step["action"]) if step.get("action") else None, now_utc_iso()),
            )
        conn.commit()
    return list_steps(automation_id)


def add_step(automation_id: int, intent: str, *, kind: str = "islem",
             position: int | None = None) -> dict[str, Any]:
    """Elle adım ekle (planlayıcı dışında).

    `position` verilmezse sona eklenir; verilirse araya girer ve sonraki
    adımlar bir kaydırılır.
    """
    steps = list_steps(automation_id)
    index = len(steps) if position is None else max(0, min(int(position), len(steps)))
    with get_connection() as conn:
        for step in steps[index:]:
            conn.execute("UPDATE automation_steps SET position = ? WHERE id = ?",
                         (step["position"] + 1, step["id"]))
        cursor = conn.execute(
            "INSERT INTO automation_steps "
            "(automation_id, position, intent, kind, status, updated_at) "
            "VALUES (?, ?, ?, ?, 'bekliyor', ?)",
            (automation_id, index, intent.strip(), kind, now_utc_iso()),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM automation_steps WHERE id = ?",
                           (cursor.lastrowid,)).fetchone()
    return _row(row)


def move_step(step_id: int, delta: int) -> list[dict[str, Any]]:
    """Adımı bir yukarı/aşağı taşı (kullanıcı sırayı düzeltiyor)."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT automation_id, position FROM automation_steps WHERE id = ?", (step_id,)
        ).fetchone()
    if row is None:
        return []
    steps = list_steps(row["automation_id"])
    index = next((i for i, s in enumerate(steps) if s["id"] == step_id), None)
    target = None if index is None else index + delta
    if index is None or target is None or not (0 <= target < len(steps)):
        return steps
    steps[index], steps[target] = steps[target], steps[index]
    with get_connection() as conn:
        for position, step in enumerate(steps):
            conn.execute("UPDATE automation_steps SET position = ? WHERE id = ?",
                         (position, step["id"]))
        conn.commit()
    return list_steps(row["automation_id"])


def update_step(step_id: int, **fields: Any) -> None:
    allowed = {"intent", "action", "status", "last_error", "position", "kind"}
    sets, params = [], []
    for key, value in fields.items():
        if key not in allowed:
            continue
        sets.append(f"{key} = ?")
        params.append(json.dumps(value) if key == "action" and value is not None else value)
    if not sets:
        return
    sets.append("updated_at = ?")
    params.extend([now_utc_iso(), step_id])
    with get_connection() as conn:
        conn.execute(f"UPDATE automation_steps SET {', '.join(sets)} WHERE id = ?", params)
        conn.commit()


def delete_step(step_id: int) -> None:
    """Adımı sil ve kalanları sıkıştır (kullanıcı yanlış adımı siliyor)."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT automation_id FROM automation_steps WHERE id = ?", (step_id,)
        ).fetchone()
        if row is None:
            return
        conn.execute("DELETE FROM automation_steps WHERE id = ?", (step_id,))
        remaining = conn.execute(
            "SELECT id FROM automation_steps WHERE automation_id = ? ORDER BY position",
            (row["automation_id"],),
        ).fetchall()
        for position, item in enumerate(remaining):
            conn.execute("UPDATE automation_steps SET position = ? WHERE id = ?",
                         (position, item["id"]))
        conn.commit()


def reset_steps(automation_id: int) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE automation_steps SET status = 'bekliyor', last_error = NULL "
            "WHERE automation_id = ?",
            (automation_id,),
        )
        conn.commit()


# --- çalıştırmalar ----------------------------------------------------------


def start_run(automation_id: int) -> int:
    with get_connection() as conn:
        cursor = conn.execute(
            "INSERT INTO automation_runs (automation_id, started_at, status, log) "
            "VALUES (?, ?, 'calisiyor', '[]')",
            (automation_id, now_utc_iso()),
        )
        conn.commit()
        return int(cursor.lastrowid)


def finish_run(run_id: int, status: str, log: list[dict[str, Any]]) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE automation_runs SET finished_at = ?, status = ?, log = ? WHERE id = ?",
            (now_utc_iso(), status, json.dumps(log, ensure_ascii=False), run_id),
        )
        conn.commit()
