"""SQLite deposu: ayarlar, kişilikler, projeler, sohbetler ve mesajlar.

Tek kullanıcı, tek süreç. Bağlantı tek ve bir kilitle korunuyor; FastAPI'nin
thread havuzundan da event loop'tan da güvenle çağrılabilir.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

DEFAULT_MODEL = "qwen2.5:7b"

DEFAULT_PERSONA = {
    "name": "Varsayılan",
    "prompt": (
        "Sen ULL-Bot'sun: kullanıcının kendi bilgisayarında çalışan, yardımsever "
        "ve samimi bir yapay zekâ asistanısın. Kullanıcı hangi dilde yazarsa o "
        "dilde cevap ver; kısa, net ve dürüst ol."
    ),
}

DEFAULT_SETTINGS: dict[str, Any] = {
    "model": DEFAULT_MODEL,
    "active_persona_id": None,
    "temperature": 0.7,
    # Ollama'nın varsayılanı (2048-4096) Türkçe bir sohbette birkaç mesajda
    # doluyor; dolunca en eski mesajlar kesilip model konuşmayı "unutuyor".
    "num_ctx": 8192,
    # Küçük modeller uzun sohbette sistem promptundan kopuyor; son mesaja kısa
    # bir hatırlatma eklemek kişiliği ayakta tutuyor.
    "persona_reminder": True,
    "auto_title": True,
    # Boşsa ortam değişkeni (OLLAMA_URL / OLLAMA_HOST) ya da varsayılan adres.
    "ollama_url": "",
    # Code modunun modeli (boşsa sohbet modeli kullanılır).
    "code_model": "",
    "code_num_ctx": 16384,
    "appearance": {},
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS personas (
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    prompt     TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS projects (
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    context    TEXT NOT NULL DEFAULT '',
    pinned     INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS chats (
    id           TEXT PRIMARY KEY,
    title        TEXT NOT NULL,
    title_locked INTEGER NOT NULL DEFAULT 0,
    project_id   TEXT REFERENCES projects(id) ON DELETE SET NULL,
    pinned       INTEGER NOT NULL DEFAULT 0,
    created_at   REAL NOT NULL,
    updated_at   REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id    TEXT NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    role       TEXT NOT NULL,
    content    TEXT NOT NULL,
    thinking   TEXT NOT NULL DEFAULT '',
    model      TEXT,
    persona_id TEXT,
    meta       TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_chat ON messages(chat_id, id);
CREATE TABLE IF NOT EXISTS code_sessions (
    id         TEXT PRIMARY KEY,
    title      TEXT NOT NULL,
    cwd        TEXT NOT NULL,
    mode       TEXT NOT NULL DEFAULT 'ask',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS code_messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES code_sessions(id) ON DELETE CASCADE,
    data       TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_code_messages ON code_messages(session_id, id);
CREATE INDEX IF NOT EXISTS idx_chats_updated ON chats(updated_at);
"""


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


def _like(q: str) -> str:
    q = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{q.casefold()}%"


class DB:
    def __init__(self, path: Path, legacy_state: Path | None = None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA journal_mode = WAL")
        # SQLite'ın LOWER/LIKE'ı yalnızca ASCII'yi küçültüyor; Türkçe arama için
        # Python'un casefold'unu kullanıyoruz.
        self._conn.create_function("fold", 1, lambda s: s.casefold() if s else s, deterministic=True)
        self._conn.executescript(SCHEMA)
        self._seed(legacy_state)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # --- iç yardımcılar ------------------------------------------------------

    def _q(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, args).fetchall()

    def _x(self, sql: str, args: tuple = ()) -> sqlite3.Cursor:
        with self._lock, self._conn:
            return self._conn.execute(sql, args)

    def _seed(self, legacy_state: Path | None) -> None:
        if self._q("SELECT 1 FROM settings WHERE key = 'seeded'"):
            return
        personas, settings = [], {}
        # İlk sürüm her şeyi data/state.json'da tutuyordu; kişilikler kaybolmasın.
        if legacy_state and legacy_state.exists():
            try:
                raw = json.loads(legacy_state.read_text("utf-8"))
                personas = [
                    {"id": str(p["id"]), "name": str(p["name"]), "prompt": str(p.get("prompt", ""))}
                    for p in raw.get("personas", [])
                    if isinstance(p, dict) and p.get("id") and p.get("name")
                ]
                if raw.get("model"):
                    settings["model"] = str(raw["model"])
                settings["active_persona_id"] = raw.get("active_persona_id")
            except (OSError, ValueError, TypeError, KeyError):
                personas = []
        if not personas:
            personas = [{"id": _new_id(), **DEFAULT_PERSONA}]
            settings["active_persona_id"] = personas[0]["id"]
        now = time.time()
        with self._lock, self._conn:
            for i, p in enumerate(personas):
                self._conn.execute(
                    "INSERT OR IGNORE INTO personas (id, name, prompt, created_at) VALUES (?, ?, ?, ?)",
                    (p["id"], p["name"], p["prompt"], now + i * 1e-3),
                )
            for k, v in settings.items():
                self._conn.execute("INSERT OR REPLACE INTO settings VALUES (?, ?)", (k, json.dumps(v)))
            self._conn.execute("INSERT OR REPLACE INTO settings VALUES ('seeded', 'true')")

    # --- ayarlar ----------------------------------------------------------------

    def get_settings(self) -> dict[str, Any]:
        out = json.loads(json.dumps(DEFAULT_SETTINGS))
        for row in self._q("SELECT key, value FROM settings"):
            if row["key"] in out:
                out[row["key"]] = json.loads(row["value"])
        # None = bilerek "kişiliksiz" seçildi. Yalnızca silinmiş bir kişiliğe işaret
        # ediyorsa ilk kişiliğe düş.
        ids = {p["id"] for p in self.list_personas()}
        if out["active_persona_id"] is not None and out["active_persona_id"] not in ids:
            out["active_persona_id"] = next(iter(self.list_personas()), {}).get("id")
        return out

    def update_settings(self, patch: dict[str, Any]) -> dict[str, Any]:
        with self._lock, self._conn:
            for k, v in patch.items():
                if k in DEFAULT_SETTINGS:
                    self._conn.execute("INSERT OR REPLACE INTO settings VALUES (?, ?)", (k, json.dumps(v)))
        return self.get_settings()

    # --- kişilikler -----------------------------------------------------------------

    def list_personas(self) -> list[dict]:
        return [dict(r) for r in self._q("SELECT id, name, prompt FROM personas ORDER BY created_at")]

    def get_persona(self, persona_id: str | None) -> dict | None:
        rows = self._q("SELECT id, name, prompt FROM personas WHERE id = ?", (persona_id,))
        return dict(rows[0]) if rows else None

    def add_persona(self, name: str, prompt: str) -> dict:
        p = {"id": _new_id(), "name": name, "prompt": prompt}
        self._x("INSERT INTO personas VALUES (?, ?, ?, ?)", (p["id"], name, prompt, time.time()))
        return p

    def update_persona(self, persona_id: str, name: str, prompt: str) -> dict | None:
        cur = self._x("UPDATE personas SET name = ?, prompt = ? WHERE id = ?", (name, prompt, persona_id))
        return self.get_persona(persona_id) if cur.rowcount else None

    def upsert_persona(self, persona_id: str | None, name: str, prompt: str) -> dict:
        """Dosyadan yükleme için: kimlik varsa güncelle, yoksa aynı adlıyı güncelle, o da yoksa ekle."""
        with self._lock:
            if not persona_id or not self.get_persona(persona_id):
                same = self._q("SELECT id FROM personas WHERE name = ? ORDER BY created_at LIMIT 1", (name,))
                if same:
                    persona_id = same[0]["id"]
            persona_id = persona_id or _new_id()
            self._x(
                "INSERT INTO personas (id, name, prompt, created_at) VALUES (?, ?, ?, ?)"
                " ON CONFLICT(id) DO UPDATE SET name = excluded.name, prompt = excluded.prompt",
                (persona_id, name, prompt, time.time()),
            )
            return self.get_persona(persona_id)

    def delete_persona(self, persona_id: str) -> bool:
        return self._x("DELETE FROM personas WHERE id = ?", (persona_id,)).rowcount > 0

    # --- projeler ---------------------------------------------------------------------

    @staticmethod
    def _project(row: sqlite3.Row) -> dict:
        d = dict(row)
        d["pinned"] = bool(d["pinned"])
        return d

    def list_projects(self) -> list[dict]:
        rows = self._q("SELECT * FROM projects ORDER BY pinned DESC, created_at")
        return [self._project(r) for r in rows]

    def get_project(self, project_id: str | None) -> dict | None:
        rows = self._q("SELECT * FROM projects WHERE id = ?", (project_id,))
        return self._project(rows[0]) if rows else None

    def add_project(self, name: str, context: str = "") -> dict:
        pid, now = _new_id(), time.time()
        self._x("INSERT INTO projects VALUES (?, ?, ?, 0, ?, ?)", (pid, name, context, now, now))
        return self.get_project(pid)

    def update_project(self, project_id: str, fields: dict[str, Any]) -> dict | None:
        allowed = {k: v for k, v in fields.items() if k in {"name", "context", "pinned"}}
        if "pinned" in allowed:
            allowed["pinned"] = int(bool(allowed["pinned"]))
        if allowed:
            cols = ", ".join(f"{k} = ?" for k in allowed)
            self._x(
                f"UPDATE projects SET {cols}, updated_at = ? WHERE id = ?",
                (*allowed.values(), time.time(), project_id),
            )
        return self.get_project(project_id)

    def delete_project(self, project_id: str) -> bool:
        # Sohbetler silinmiyor, "Diğer"e düşüyor (ON DELETE SET NULL).
        return self._x("DELETE FROM projects WHERE id = ?", (project_id,)).rowcount > 0

    # --- sohbetler ------------------------------------------------------------------------

    @staticmethod
    def _chat(row: sqlite3.Row) -> dict:
        d = dict(row)
        d["pinned"] = bool(d["pinned"])
        d["title_locked"] = bool(d["title_locked"])
        return d

    def list_chats(self, q: str | None = None) -> list[dict]:
        if q and q.strip():
            pat = _like(q.strip())
            rows = self._q(
                """
                SELECT * FROM chats c
                WHERE fold(c.title) LIKE ? ESCAPE '\\'
                   OR EXISTS (SELECT 1 FROM messages m
                              WHERE m.chat_id = c.id AND fold(m.content) LIKE ? ESCAPE '\\')
                ORDER BY c.updated_at DESC LIMIT 200
                """,
                (pat, pat),
            )
        else:
            rows = self._q("SELECT * FROM chats ORDER BY updated_at DESC")
        return [self._chat(r) for r in rows]

    def get_chat(self, chat_id: str | None) -> dict | None:
        rows = self._q("SELECT * FROM chats WHERE id = ?", (chat_id,))
        return self._chat(rows[0]) if rows else None

    def create_chat(self, title: str, project_id: str | None = None) -> dict:
        cid, now = _new_id(), time.time()
        self._x(
            "INSERT INTO chats (id, title, project_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (cid, title, project_id, now, now),
        )
        return self.get_chat(cid)

    def update_chat(self, chat_id: str, fields: dict[str, Any]) -> dict | None:
        allowed = {k: v for k, v in fields.items() if k in {"title", "project_id", "pinned", "title_locked"}}
        for k in ("pinned", "title_locked"):
            if k in allowed:
                allowed[k] = int(bool(allowed[k]))
        if allowed:
            cols = ", ".join(f"{k} = ?" for k in allowed)
            self._x(f"UPDATE chats SET {cols} WHERE id = ?", (*allowed.values(), chat_id))
        return self.get_chat(chat_id)

    def delete_chat(self, chat_id: str) -> bool:
        return self._x("DELETE FROM chats WHERE id = ?", (chat_id,)).rowcount > 0

    # --- mesajlar ----------------------------------------------------------------------------

    @staticmethod
    def _message(row: sqlite3.Row) -> dict:
        d = dict(row)
        d["meta"] = json.loads(d["meta"] or "{}")
        return d

    def messages(self, chat_id: str) -> list[dict]:
        rows = self._q("SELECT * FROM messages WHERE chat_id = ? ORDER BY id", (chat_id,))
        return [self._message(r) for r in rows]

    def add_message(
        self,
        chat_id: str,
        role: str,
        content: str,
        *,
        thinking: str = "",
        model: str | None = None,
        persona_id: str | None = None,
        meta: dict | None = None,
    ) -> int:
        now = time.time()
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT INTO messages (chat_id, role, content, thinking, model, persona_id, meta, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (chat_id, role, content, thinking, model, persona_id, json.dumps(meta or {}, ensure_ascii=False), now),
            )
            self._conn.execute("UPDATE chats SET updated_at = ? WHERE id = ?", (now, chat_id))
            return cur.lastrowid

    def delete_trailing_assistant(self, chat_id: str) -> None:
        """'Yeniden üret' için: sondaki asistan cevaplarını sil."""
        with self._lock, self._conn:
            while True:
                row = self._conn.execute(
                    "SELECT id, role FROM messages WHERE chat_id = ? ORDER BY id DESC LIMIT 1", (chat_id,)
                ).fetchone()
                if not row or row["role"] != "assistant":
                    return
                self._conn.execute("DELETE FROM messages WHERE id = ?", (row["id"],))

    # --- code oturumları ------------------------------------------------------------------------

    def list_code_sessions(self) -> list[dict]:
        return [dict(r) for r in self._q("SELECT * FROM code_sessions ORDER BY updated_at DESC")]

    def get_code_session(self, sid: str) -> dict | None:
        rows = self._q("SELECT * FROM code_sessions WHERE id = ?", (sid,))
        return dict(rows[0]) if rows else None

    def create_code_session(self, cwd: str, title: str) -> dict:
        sid, now = _new_id(), time.time()
        self._x("INSERT INTO code_sessions VALUES (?, ?, ?, 'ask', ?, ?)", (sid, title, cwd, now, now))
        return self.get_code_session(sid)

    def update_code_session(self, sid: str, fields: dict[str, Any]) -> dict | None:
        allowed = {k: v for k, v in fields.items() if k in {"title", "mode"}}
        if allowed:
            cols = ", ".join(f"{k} = ?" for k in allowed)
            self._x(f"UPDATE code_sessions SET {cols} WHERE id = ?", (*allowed.values(), sid))
        return self.get_code_session(sid)

    def delete_code_session(self, sid: str) -> bool:
        return self._x("DELETE FROM code_sessions WHERE id = ?", (sid,)).rowcount > 0

    def code_messages(self, sid: str) -> list[dict]:
        rows = self._q("SELECT data FROM code_messages WHERE session_id = ? ORDER BY id", (sid,))
        return [json.loads(r["data"]) for r in rows]

    def add_code_message(self, sid: str, message: dict) -> None:
        now = time.time()
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO code_messages (session_id, data, created_at) VALUES (?, ?, ?)",
                (sid, json.dumps(message, ensure_ascii=False), now),
            )
            self._conn.execute("UPDATE code_sessions SET updated_at = ? WHERE id = ?", (now, sid))
