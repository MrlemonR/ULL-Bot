"""Kalıcı hafıza aracı: `remember` (spec §6.2, tek satır: "Kalıcı nota yaz").

Okuma tarafı ayrı bir araç değil — `list_notes()` her turun sistem
promptuna gömülüyor (`app/agent/prompts.py`), yani model "hatırladıklarını"
bir araç çağırmadan, ambient bağlam olarak görüyor. Spec sadece `remember`i
listeliyor, ayrı bir "recall" aracı tanımlamıyor.
"""

from __future__ import annotations

from typing import Any

from app.agent.tools.base import Tool, ToolContext, ToolPreview, ToolResult, register
from app.memory.store import set_note

MAX_KEY_LENGTH = 100
MAX_VALUE_LENGTH = 2000


class Remember(Tool):
    name = "remember"
    description = (
        "Oturumlar arası kalıcı bir not yaz (kullanıcının tercihleri, tekrar eden "
        "bağlam gibi). Aynı `key` ile tekrar çağrılırsa eski değerin üzerine yazılır — "
        "düzeltmek/güncellemek için budur. Sohbetin kendisi zaten kaydediliyor, "
        "bunu sadece SONRAKİ oturumlarda da hatırlanması gereken şeyler için kullan."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "key": {
                "type": "string",
                "description": "Kısa, makine-okunur bir anahtar (örn. 'preferred_shell').",
            },
            "value": {
                "type": "string",
                "description": "Hatırlanacak bilgi, düz metin.",
            },
        },
        "required": ["key", "value"],
    }
    # Dosya sistemine/kabuğa dokunmuyor, sadece kendi SQLite'ımıza bir satır
    # yazıyor — dry-run'ın konusu olan "kullanıcının sistemini değiştirme"
    # riskiyle aynı kategoride değil, o yüzden safe (spec §6.2 tablosu da
    # `remember`i safe listeliyor).
    risk = "safe"

    def preview(self, ctx: ToolContext, **kwargs: Any) -> ToolPreview:
        return ToolPreview(summary=f"Not al: {kwargs.get('key')!r}")

    def run(self, ctx: ToolContext, **kwargs: Any) -> ToolResult:
        key = str(kwargs.get("key", "")).strip()
        value = str(kwargs.get("value", "")).strip()
        if not key:
            return ToolResult(False, "Boş anahtar (key) ile not alınamaz.", untrusted=False)
        if not value:
            return ToolResult(False, "Boş değer (value) ile not alınamaz.", untrusted=False)
        if len(key) > MAX_KEY_LENGTH:
            return ToolResult(
                False, f"Anahtar çok uzun ({len(key)} > {MAX_KEY_LENGTH}).", untrusted=False
            )
        if len(value) > MAX_VALUE_LENGTH:
            value = value[:MAX_VALUE_LENGTH]

        set_note(key, value)
        return ToolResult(True, f"Kaydedildi: {key} = {value!r}", meta={"key": key})


remember = register(Remember())
