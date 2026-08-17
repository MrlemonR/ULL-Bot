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


# --- Faz 7: kalıcı hafıza (`memory_notes`, spec §4.4 / §6.2 `remember`) ------


def set_note(key: str, value: str) -> None:
    """`remember` aracının yazdığı yer — anahtar zaten varsa üzerine yazar."""
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO memory_notes (key, value, updated_at) VALUES (?, ?, datetime('now')) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            (key, value),
        )
        conn.commit()


def get_note(key: str) -> str | None:
    with get_connection() as conn:
        row = conn.execute("SELECT value FROM memory_notes WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def list_notes() -> list[dict[str, Any]]:
    """Sistem promptuna gömülecek sıralı liste — spec'in "oturumlar arası

    kalıcı notlar" dediği şey (`system_prompt()` bunu okuyup modele ambient
    bağlam olarak veriyor, ayrı bir "recall" aracı yok — spec §6.2 sadece
    `remember` (yaz) tanımlıyor).
    """
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT key, value, updated_at FROM memory_notes ORDER BY key"
        ).fetchall()
    return [dict(row) for row in rows]


def delete_note(key: str) -> bool:
    """UI/API'nin yanlış bir notu silebilmesi için (araç değil — spec §6.2

    `remember` dışında bir hafıza aracı tanımlamıyor, silme kullanıcı
    tarafından yapılan bir yönetim işlemi).
    """
    with get_connection() as conn:
        cursor = conn.execute("DELETE FROM memory_notes WHERE key = ?", (key,))
        conn.commit()
    return cursor.rowcount > 0


# --- Faz 7: oturum geçmişi ve arama -----------------------------------------


def list_sessions(limit: int = 50) -> list[dict[str, Any]]:
    """Kota panelindeki gibi UI'ın listeleyeceği oturumlar — en yeni önce.

    `title` boşsa ilk kullanıcı mesajından türetilir (ayrı bir yazma/migration
    gerekmesin diye sorguda hesaplanıyor, satır eklenirken değil).
    """
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT
                s.id,
                s.created_at,
                COALESCE(
                    s.title,
                    (SELECT substr(m.content, 1, 60) FROM messages m
                     WHERE m.session_id = s.id AND m.role = 'user'
                     ORDER BY m.id ASC LIMIT 1)
                ) AS title,
                (SELECT COUNT(*) FROM messages m WHERE m.session_id = s.id) AS message_count,
                (SELECT MAX(m.ts) FROM messages m WHERE m.session_id = s.id) AS last_message_at
            FROM sessions s
            ORDER BY COALESCE(last_message_at, s.created_at) DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def get_session_messages(session_id: str) -> list[dict[str, Any]]:
    """Bir oturumun TÜM mesajları (tool dahil) — geçmiş görüntüleme için.

    `load_history()`'den farkı: o modele geri verilecek özet listeyi üretir
    (tool mesajları hariç), bu ise UI'ın "bu oturumda ne oldu" diye
    göstereceği tam kayıt.
    """
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, role, content, tool_name, model, ts FROM messages "
            "WHERE session_id = ? ORDER BY id ASC",
            (session_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def search_messages(query: str, limit: int = 50) -> list[dict[str, Any]]:
    """Basit `LIKE` araması (spec "oturum geçmişi ve arama", FTS5 değil —

    veri hacmi kişisel bir kullanıcının sohbet geçmişi, tam metin indeksinin
    bakım maliyetini (trigger'larla senkron tutmak) karşılamaz).

    Kullanıcının sorgusundaki `%`/`_` LIKE joker karakteri olarak
    yorumlanmasın diye kaçırılıyor — "50%" araması gerçekten "50%" arasın,
    "50" + herhangi bir şey değil.
    """
    stripped = query.strip()
    if not stripped:
        return []
    escaped = stripped.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    pattern = f"%{escaped}%"
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT m.session_id, m.id AS message_id, m.role, m.content, m.ts, "
            "       COALESCE(s.title, '') AS session_title "
            "FROM messages m JOIN sessions s ON s.id = m.session_id "
            "WHERE m.content LIKE ? ESCAPE '\\' "
            "ORDER BY m.id DESC LIMIT ?",
            (pattern, limit),
        ).fetchall()
    return [dict(row) for row in rows]
