"""Dosya araçları: `read_file`, `list_dir`, `search_files`.

Üçü de salt okunur, ama "salt okunur" tek başına yeterli değil: her yol
`safety.sandbox` üzerinden geçer, yani çalışma alanı dışına çıkan, symlink ile
kaçmaya çalışan veya `.env`/`id_rsa` gibi hassas kalıplara uyan hedefler
araç seviyesinde reddedilir.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

from app.agent.tools.base import (
    Tool,
    ToolContext,
    ToolPreview,
    ToolResult,
    register,
    truncate_middle,
)
from app.safety.policy import Decision
from app.safety.sandbox import PathViolation, get_workspace_config, resolve_path

SEARCH_TIMEOUT_SECONDS = 20


def _violation_result(exc: PathViolation) -> ToolResult:
    return ToolResult(
        ok=False,
        output=f"Erişim reddedildi: {exc}",
        meta={"error": "path_violation"},
        untrusted=False,
    )


def _assess_path(ctx: ToolContext, raw: str | None) -> Decision:
    """Yol geçerliyse safe, değilse blocked — kullanıcıya sorulacak bir şey yok."""
    if not raw:
        return Decision("safe", "Varsayılan dizin.", "path-ok")
    try:
        resolve_path(raw, base=ctx.cwd)
    except PathViolation as exc:
        return Decision("blocked", str(exc), "path-violation")
    return Decision("safe", "Yol çalışma alanı içinde.", "path-ok")


class ReadFile(Tool):
    name = "read_file"
    description = (
        "Bir metin dosyasını oku. İsteğe bağlı satır aralığı verilebilir. "
        "Yalnızca çalışma alanı içindeki dosyalar okunabilir."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Dosya yolu (mutlak veya çalışma dizinine göre)."},
            "start_line": {"type": "integer", "description": "1'den başlayan ilk satır."},
            "end_line": {"type": "integer", "description": "Son satır (dahil)."},
        },
        "required": ["path"],
    }
    risk = "safe"

    def assess(self, ctx: ToolContext, **kwargs: Any) -> Decision:
        return _assess_path(ctx, kwargs.get("path"))

    def preview(self, ctx: ToolContext, **kwargs: Any) -> ToolPreview:
        return ToolPreview(summary=f"Dosya oku: {kwargs.get('path')}", paths=[str(kwargs.get("path"))])

    def run(self, ctx: ToolContext, **kwargs: Any) -> ToolResult:
        raw_path = kwargs.get("path", "")
        start_line = kwargs.get("start_line")
        end_line = kwargs.get("end_line")
        try:
            path = resolve_path(raw_path, base=ctx.cwd, must_exist=True)
        except PathViolation as exc:
            return _violation_result(exc)

        if path.is_dir():
            return ToolResult(False, f"{path} bir dizin — list_dir kullan.", untrusted=False)

        config = get_workspace_config()
        try:
            size = path.stat().st_size
            with path.open("rb") as handle:
                raw = handle.read(config.max_read_bytes)
        except OSError as exc:
            return ToolResult(False, f"Dosya okunamadı: {exc}", untrusted=False)

        if b"\x00" in raw[:4096]:
            return ToolResult(
                False,
                f"{path.name} ikili (binary) görünüyor, metin olarak okunamıyor. Boyut: {size} byte.",
                untrusted=False,
            )

        text = raw.decode("utf-8", errors="replace")
        lines = text.splitlines()
        total = len(lines)
        if start_line or end_line:
            start = max(1, int(start_line or 1))
            end = min(total, int(end_line or total))
            lines = lines[start - 1 : end]
            offset = start
        else:
            offset = 1

        numbered = "\n".join(f"{offset + i:>6}\t{line}" for i, line in enumerate(lines))
        note = ""
        if size > config.max_read_bytes:
            note = f"\n\n[dosya {size} byte, ilk {config.max_read_bytes} byte okundu]"
        return ToolResult(
            ok=True,
            output=numbered + note,
            meta={"path": str(path), "lines": total, "bytes": size},
        )


class ListDir(Tool):
    name = "list_dir"
    description = (
        "Bir dizinin içeriğini listele. Her satır: tip, boyut, ad. "
        "Yalnızca çalışma alanı içindeki dizinler listelenebilir."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Dizin yolu. Boşsa çalışma dizini."},
            "pattern": {"type": "string", "description": "Ad filtresi, glob (örn. '*.pdf')."},
            "show_hidden": {"type": "boolean", "description": "Nokta ile başlayan dosyaları da göster."},
        },
    }
    risk = "safe"

    def assess(self, ctx: ToolContext, **kwargs: Any) -> Decision:
        return _assess_path(ctx, kwargs.get("path"))

    def preview(self, ctx: ToolContext, **kwargs: Any) -> ToolPreview:
        return ToolPreview(summary=f"Dizin listele: {kwargs.get('path') or ctx.cwd}")

    def run(self, ctx: ToolContext, **kwargs: Any) -> ToolResult:
        raw_path = kwargs.get("path") or str(ctx.cwd)
        pattern = kwargs.get("pattern")
        show_hidden = bool(kwargs.get("show_hidden", False))
        try:
            path = resolve_path(raw_path, base=ctx.cwd, must_exist=True)
        except PathViolation as exc:
            return _violation_result(exc)

        if not path.is_dir():
            return ToolResult(False, f"{path} bir dizin değil.", untrusted=False)

        try:
            entries = sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except OSError as exc:
            return ToolResult(False, f"Dizin okunamadı: {exc}", untrusted=False)

        rows: list[str] = []
        hidden_count = 0
        for entry in entries:
            if not show_hidden and entry.name.startswith("."):
                hidden_count += 1
                continue
            if pattern and not entry.match(pattern):
                continue
            try:
                stat = entry.stat()
                size = "-" if entry.is_dir() else str(stat.st_size)
            except OSError:
                size = "?"
            kind = "dir " if entry.is_dir() else "link" if entry.is_symlink() else "file"
            rows.append(f"{kind}\t{size:>10}\t{entry.name}")

        if not rows:
            body = "(eşleşen giriş yok)"
        else:
            body = "\n".join(rows)
        if hidden_count:
            body += f"\n\n[{hidden_count} gizli giriş atlandı — show_hidden ile görebilirsin]"
        return ToolResult(True, f"{path}:\n{body}", meta={"path": str(path), "count": len(rows)})


class SearchFiles(Tool):
    name = "search_files"
    description = (
        "Dosya içeriğinde metin ara (ripgrep). Sonuç: dosya:satır:eşleşme. "
        "Arama kökü çalışma alanı içinde olmalı."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Aranacak metin."},
            "path": {"type": "string", "description": "Arama kökü. Boşsa çalışma dizini."},
            "glob": {"type": "string", "description": "Dosya filtresi (örn. '*.py')."},
            "regex": {"type": "boolean", "description": "Sorguyu regex olarak yorumla (varsayılan: düz metin)."},
            "max_results": {"type": "integer", "description": "En fazla kaç eşleşme (varsayılan 50)."},
        },
        "required": ["query"],
    }
    risk = "safe"

    def assess(self, ctx: ToolContext, **kwargs: Any) -> Decision:
        return _assess_path(ctx, kwargs.get("path"))

    def preview(self, ctx: ToolContext, **kwargs: Any) -> ToolPreview:
        return ToolPreview(summary=f"Ara: {kwargs.get('query')!r} @ {kwargs.get('path') or ctx.cwd}")

    def run(self, ctx: ToolContext, **kwargs: Any) -> ToolResult:
        query = kwargs.get("query", "")
        if not query:
            return ToolResult(False, "Boş arama sorgusu.", untrusted=False)
        max_results = int(kwargs.get("max_results") or 50)
        try:
            root = resolve_path(kwargs.get("path") or str(ctx.cwd), base=ctx.cwd, must_exist=True)
        except PathViolation as exc:
            return _violation_result(exc)

        rg = shutil.which("rg")
        if rg:
            matches = self._ripgrep(rg, query, root, kwargs.get("glob"), bool(kwargs.get("regex")), max_results)
        else:
            matches = self._python_search(query, root, kwargs.get("glob"), max_results)

        if isinstance(matches, ToolResult):
            return matches
        if not matches:
            return ToolResult(True, "Eşleşme yok.", meta={"count": 0})
        return ToolResult(
            True,
            truncate_middle("\n".join(matches), 4000),
            meta={"count": len(matches), "root": str(root)},
        )

    def _keep(self, file_path: str, root: Path) -> bool:
        """Sonuçları da sandbox'tan geçir — `.env` gibi kalıplar burada elenir."""
        try:
            resolve_path(file_path, base=root)
        except PathViolation:
            return False
        return True

    def _ripgrep(
        self, rg: str, query: str, root: Path, glob: str | None, regex: bool, max_results: int
    ) -> list[str] | ToolResult:
        cmd = [
            rg, "--line-number", "--no-heading", "--color", "never",
            "--max-filesize", "2M", "--max-count", str(max_results),
        ]
        if not regex:
            cmd.append("--fixed-strings")
        if glob:
            cmd.extend(["--glob", glob])
        cmd.extend(["--", query, str(root)])
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=SEARCH_TIMEOUT_SECONDS, check=False
            )
        except subprocess.TimeoutExpired:
            return ToolResult(False, "Arama zaman aşımına uğradı.", untrusted=False)
        if proc.returncode not in (0, 1):
            return ToolResult(False, f"Arama başarısız: {proc.stderr.strip()}", untrusted=False)

        results: list[str] = []
        for line in proc.stdout.splitlines():
            file_part = line.split(":", 1)[0]
            if not self._keep(file_part, root):
                continue
            results.append(line)
            if len(results) >= max_results:
                break
        return results

    def _python_search(
        self, query: str, root: Path, glob: str | None, max_results: int
    ) -> list[str]:
        """ripgrep yoksa yedek: yavaş ama çalışır."""
        results: list[str] = []
        pattern = glob or "*"
        for path in root.rglob(pattern):
            if len(results) >= max_results:
                break
            if not path.is_file() or path.name.startswith("."):
                continue
            if not self._keep(str(path), root):
                continue
            try:
                if path.stat().st_size > 2_000_000:
                    continue
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for number, line in enumerate(text.splitlines(), start=1):
                if query in line:
                    results.append(f"{path}:{number}:{line.strip()[:200]}")
                    if len(results) >= max_results:
                        break
        return results


read_file = register(ReadFile())
list_dir = register(ListDir())
search_files = register(SearchFiles())
