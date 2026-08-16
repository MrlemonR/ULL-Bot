import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from app.settings import settings

SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def init_db() -> None:
    db_path = settings.resolved_db_path
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))


@contextmanager
def get_connection() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(settings.resolved_db_path)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()
