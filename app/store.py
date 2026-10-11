"""Ayarların ve kişiliklerin (master prompt) saklandığı küçük JSON deposu.

Tek kullanıcı, tek süreç: bir kilit ve atomik yazma yetiyor, veritabanına
gerek yok. Dosya `data/state.json` (bkz. ULL_DATA_DIR).
"""

from __future__ import annotations

import copy
import json
import os
import threading
import time
import uuid
from pathlib import Path

DEFAULT_MODEL = "qwen2.5:7b"

DEFAULT_PERSONA = {
    "name": "Varsayılan",
    "prompt": (
        "Sen ULL-Bot'sun: kullanıcının kendi bilgisayarında çalışan, yardımsever "
        "ve samimi bir yapay zekâ asistanısın. Kullanıcı hangi dilde yazarsa o "
        "dilde cevap ver; kısa, net ve dürüst ol."
    ),
}


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


class Store:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._data = self._load()

    # --- okuma / yazma ------------------------------------------------------

    def _default(self) -> dict:
        persona = {"id": _new_id(), **DEFAULT_PERSONA}
        return {
            "model": DEFAULT_MODEL,
            "active_persona_id": persona["id"],
            "personas": [persona],
        }

    def _load(self) -> dict:
        if not self.path.exists():
            return self._default()
        try:
            raw = json.loads(self.path.read_text("utf-8"))
            return self._normalize(raw)
        except (OSError, ValueError, TypeError, AttributeError):
            # Bozuk dosyanın üstüne sessizce yazmak kullanıcının promptlarını
            # yok eder; kenara alıp temiz başlıyoruz.
            backup = self.path.with_name(f"{self.path.stem}.bozuk-{int(time.time())}.json")
            try:
                self.path.replace(backup)
            except OSError:
                pass
            return self._default()

    @staticmethod
    def _normalize(raw: dict) -> dict:
        personas = []
        for p in raw.get("personas") or []:
            if not isinstance(p, dict):
                continue
            personas.append(
                {
                    "id": str(p.get("id") or _new_id()),
                    "name": str(p.get("name") or "Adsız"),
                    "prompt": str(p.get("prompt") or ""),
                }
            )
        ids = {p["id"] for p in personas}
        active = raw.get("active_persona_id")
        if active not in ids:
            active = personas[0]["id"] if personas else None
        return {
            "model": str(raw.get("model") or DEFAULT_MODEL),
            "active_persona_id": active,
            "personas": personas,
        }

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data, ensure_ascii=False, indent=2), "utf-8")
        os.replace(tmp, self.path)

    # --- genel API -----------------------------------------------------------

    def snapshot(self) -> dict:
        with self._lock:
            return copy.deepcopy(self._data)

    def get_persona(self, persona_id: str | None) -> dict | None:
        with self._lock:
            for p in self._data["personas"]:
                if p["id"] == persona_id:
                    return dict(p)
        return None

    def set_model(self, model: str) -> None:
        with self._lock:
            self._data["model"] = model
            self._save()

    def add_persona(self, name: str, prompt: str) -> dict:
        persona = {"id": _new_id(), "name": name, "prompt": prompt}
        with self._lock:
            self._data["personas"].append(persona)
            if self._data["active_persona_id"] is None:
                self._data["active_persona_id"] = persona["id"]
            self._save()
        return dict(persona)

    def update_persona(self, persona_id: str, name: str, prompt: str) -> dict | None:
        with self._lock:
            for p in self._data["personas"]:
                if p["id"] == persona_id:
                    p["name"] = name
                    p["prompt"] = prompt
                    self._save()
                    return dict(p)
        return None

    def delete_persona(self, persona_id: str) -> bool:
        with self._lock:
            personas = self._data["personas"]
            remaining = [p for p in personas if p["id"] != persona_id]
            if len(remaining) == len(personas):
                return False
            self._data["personas"] = remaining
            if self._data["active_persona_id"] == persona_id:
                self._data["active_persona_id"] = remaining[0]["id"] if remaining else None
            self._save()
            return True

    def set_active(self, persona_id: str | None) -> bool:
        with self._lock:
            if persona_id is not None and not any(
                p["id"] == persona_id for p in self._data["personas"]
            ):
                return False
            self._data["active_persona_id"] = persona_id
            self._save()
            return True
