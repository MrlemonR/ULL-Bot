"""Tool arayüzü ve kayıt defteri (spec §6.2).

Her araç üç soruya cevap verir:

- `assess()` → bu çağrı `safe` mi, `confirm` mi, `blocked` mı? (risk statik
  değil: `run_shell` için komuta, dosya araçları için yola bağlı.)
- `preview()` → onay diyaloğunda kullanıcıya ne gösterilecek?
- `run()` → işi yap.

Ajan döngüsü aracı doğrudan çağırmaz; önce `assess`, gerekirse onay, sonra
`run`. Böylece onay mantığı tek yerde durur.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

from app.safety.policy import Decision, Risk


@dataclass
class ToolContext:
    """Bir araç çağrısının çalıştığı ortam."""

    cwd: Path
    session_id: str
    dry_run: bool = True
    # Bağlama daha önce güvenilmeyen içerik (dosya/web) girdi mi? Girdiyse
    # kabuk çağrıları bir seviye yükseltilir (spec §6.4, prompt injection).
    tainted: bool = False
    # Bu TURDA yapılmış arama sorguları (normalize edilmiş). `AgentLoop` her
    # tur için yeni bir `ToolContext` kuruyor, yani liste tur bitince düşüyor.
    # Zayıf modellerin aynı sorguyu döndürüp durmasını kesmek için (bkz.
    # `app/agent/tools/web.py` → `WebSearch.run`).
    searched: list[str] = field(default_factory=list)


@dataclass
class ToolResult:
    ok: bool
    output: str
    meta: dict[str, Any] = field(default_factory=dict)
    # Çıktı dış dünyadan mı geliyor? Öyleyse modele "untrusted" işaretiyle
    # verilir ve oturum "tainted" sayılır.
    untrusted: bool = True


@dataclass
class ToolPreview:
    """Onay diyaloğunun içeriği."""

    summary: str
    paths: list[str] = field(default_factory=list)
    detail: str = ""


class Tool:
    name: ClassVar[str] = ""
    description: ClassVar[str] = ""
    parameters: ClassVar[dict[str, Any]] = {"type": "object", "properties": {}}
    # Statik risk; `assess()` bunu çağrı bazında sıkılaştırabilir.
    risk: ClassVar[Risk] = "confirm"
    # Yazma yapabilir mi? Dry-run modunda bunlar çalıştırılmaz.
    writes: ClassVar[bool] = False

    def assess(self, ctx: ToolContext, **kwargs: Any) -> Decision:
        return Decision(self.risk, f"'{self.name}' varsayılan riski: {self.risk}.", "tool-default")

    def preview(self, ctx: ToolContext, **kwargs: Any) -> ToolPreview:
        args = ", ".join(f"{k}={v!r}" for k, v in kwargs.items())
        return ToolPreview(summary=f"{self.name}({args})")

    def run(self, ctx: ToolContext, **kwargs: Any) -> ToolResult:  # pragma: no cover
        raise NotImplementedError

    def openai_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


_REGISTRY: dict[str, Tool] = {}


def register(tool: Tool) -> Tool:
    if not tool.name:
        raise ValueError("Aracın adı olmalı.")
    _REGISTRY[tool.name] = tool
    return tool


def get_tool(name: str) -> Tool | None:
    return _REGISTRY.get(name)


def all_tools() -> dict[str, Tool]:
    return dict(_REGISTRY)


def tool_schemas() -> list[dict[str, Any]]:
    return [tool.openai_schema() for tool in _REGISTRY.values()]


def truncate_middle(text: str, limit: int) -> str:
    """Uzun çıktıyı ortadan kırp (spec §6.1: max 4000 karakter, ortadan)."""
    if len(text) <= limit:
        return text
    keep = limit - 80
    head = int(keep * 0.6)
    tail = keep - head
    omitted = len(text) - head - tail
    return (
        f"{text[:head]}\n\n... [{omitted} karakter kırpıldı — çıktının ortası atlandı] ...\n\n"
        f"{text[-tail:]}"
    )
