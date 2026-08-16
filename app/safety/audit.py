"""Denetim kaydı — her araç çağrısı buraya yazılır (spec §6.3).

Log `~/.local/share/ai-orchestrator/audit.log` altında, JSON Lines biçiminde
tutulur. Dosya 0600 izinle açılır ve bulunduğu dizin `sandbox.py`'de
`denied_paths`'e eklidir — yani ajan kendi izini okuyamaz veya silemez.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from typing import Any

from app.settings import settings

_lock = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def audit(event: str, **fields: Any) -> None:
    """Tek satırlık bir denetim kaydı yaz. Hiçbir koşulda çağıranı patlatmaz."""
    record = {"ts": _now(), "event": event, **fields}
    line = json.dumps(record, ensure_ascii=False, default=str)
    path = settings.audit_log_path
    try:
        with _lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                os.write(fd, (line + "\n").encode("utf-8"))
            finally:
                os.close(fd)
    except OSError as exc:  # disk dolu, izin hatası vb. — sohbeti düşürme
        print(f"[audit] kayıt yazılamadı: {exc}")


def read_recent(limit: int = 50) -> list[dict]:
    """Son kayıtları oku (UI/hata ayıklama için; ajan araçları bunu çağıramaz)."""
    path = settings.audit_log_path
    if not path.is_file():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()[-limit:]
    records: list[dict] = []
    for line in lines:
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records
