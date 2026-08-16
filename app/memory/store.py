"""Oturum ve mesaj kalıcılığı (SQLite).

Faz 1'de bu mantık `main.py` içindeydi; ajan döngüsü de yazmaya başlayınca
tek yere taşındı.
"""

from __future__ import annotations

from typing import Any

from app.db.connection import get_connection

# Modele geri verilecek geçmiş mesaj sayısı (bağlamı şişirmemek için).
HISTORY_LIMIT = 20


def ensure_session(session_id: str) -> None:
    with get_connection() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO sessions (id, created_at, title) VALUES (?, datetime('now'), NULL)",
            (session_id,),
        )
        conn.commit()


def save_message(
    session_id: str,
    role: str,
    content: str,
    *,
    tool_name: str | None = None,
    model: str | None = None,
) -> None:
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO messages (session_id, role, content, tool_name, model, ts) "
            "VALUES (?, ?, ?, ?, ?, datetime('now'))",
            (session_id, role, content, tool_name, model),
        )
        conn.commit()


def load_history(session_id: str, limit: int = HISTORY_LIMIT) -> list[dict[str, Any]]:
    """Modele verilecek geçmiş: yalnızca kullanıcı ve asistan metin mesajları.

    `tool` rolündeki satırlar bilinçli olarak atlanıyor: bir `tool` mesajı ancak
    kendisini doğuran `tool_calls`'lu asistan mesajıyla birlikte geçerlidir,
    yarısını taşımak API tarafında hataya yol açar. Önceki turların araç
    sonuçları zaten asistanın özetinde duruyor.
    """
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT role, content FROM messages "
            "WHERE session_id = ? AND role IN ('user', 'assistant') "
            "AND content IS NOT NULL AND content != '' "
            "ORDER BY id DESC LIMIT ?",
            (session_id, limit),
        ).fetchall()
    return [{"role": row["role"], "content": row["content"]} for row in reversed(rows)]
