import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from app.settings import settings

SCHEMA_PATH = Path(__file__).parent / "schema.sql"


# `CREATE TABLE IF NOT EXISTS` var olan tabloya yeni sütun eklemez; sonradan
# gelen sütunlar buraya yazılır ve her açılışta idempotent olarak uygulanır.
MIGRATIONS: tuple[tuple[str, str, str], ...] = (
    # (tablo, sütun, ALTER ifadesi)
    ("provider_state", "note", "ALTER TABLE provider_state ADD COLUMN note TEXT"),
    # Faz 8b'de Google OAuth denendi ve kaldırıldı (bkz. DECISIONS.md);
    # sütun duruyor çünkü SQLite'ta sütun silmek tabloyu yeniden yazmayı
    # gerektiriyor ve bu alanın bir maliyeti yok. Hep 'password'.
    (
        "automation_steps",
        "kind",
        "ALTER TABLE automation_steps ADD COLUMN kind TEXT DEFAULT 'islem'",
    ),
    (
        "mail_accounts",
        "auth_type",
        "ALTER TABLE mail_accounts ADD COLUMN auth_type TEXT DEFAULT 'password'",
    ),
)


def init_db() -> None:
    db_path = settings.resolved_db_path
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        _apply_migrations(conn)


# Sütun değil VERİ göçleri: bir kategori yeniden adlandırıldığında eski
# satırlar da taşınmalı, yoksa kullanıcının kutusunda artık UI'da karşılığı
# olmayan bir kategori kalır ve o mailler hiçbir sekmede görünmez.
DATA_MIGRATIONS: tuple[tuple[str, tuple[Any, ...]], ...] = (
    # 2026-08-18: `bulten` → `reklam` (bkz. app/mail/classify.py RENAMED).
    ("UPDATE mail_messages SET category = 'reklam' WHERE category = 'bulten'", ()),
)


def _apply_migrations(conn: sqlite3.Connection) -> None:
    for table, column, statement in MIGRATIONS:
        columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in columns:
            conn.execute(statement)
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for statement, params in DATA_MIGRATIONS:
        if "mail_messages" in statement and "mail_messages" not in tables:
            continue
        conn.execute(statement, params)
    conn.commit()


@contextmanager
def get_connection() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(settings.resolved_db_path)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()
