"""`run_shell` — komut çalıştırma. Riski `safety.policy` belirler.

Üç savunma katmanı:

1. `assess()` komutu sınıflandırır; `blocked` ise döngü hiç çalıştırmaz.
2. `run()` sınıflandırmayı **tekrar** yapar — döngüde bir hata olsa bile
   yasaklı komut buradan geçemez.
3. Süreç kısıtlı bir ortamda (API anahtarları temizlenmiş env, çalışma dizini
   çalışma alanı içinde, zaman aşımı) çalışır.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
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
from app.safety.policy import Decision, classify_command, escalate
from app.safety.sandbox import PathViolation, resolve_path
from app.settings import settings

# Alt sürece sızdırılmayacak env değişkenleri.
_SECRET_ENV_PATTERN = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL)", re.IGNORECASE)


def _child_environment() -> dict[str, str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if not _SECRET_ENV_PATTERN.search(key)
    }
    env["TERM"] = "dumb"
    env["GIT_PAGER"] = "cat"
    env["PAGER"] = "cat"
    env["NO_COLOR"] = "1"
    return env


class RunShell(Tool):
    name = "run_shell"
    description = (
        "Kabuk komutu çalıştır (bash). Salt okunur komutlar doğrudan çalışır; "
        "diğerleri kullanıcı onayı ister; yıkıcı komutlar tamamen reddedilir. "
        "Komut çalışma dizininde çalışır."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "Çalıştırılacak komut."},
            "cwd": {"type": "string", "description": "Çalışma dizini (çalışma alanı içinde olmalı)."},
            "timeout": {"type": "integer", "description": "Saniye cinsinden zaman aşımı."},
        },
        "required": ["command"],
    }
    risk = "confirm"
    writes = True

    def _resolve_cwd(self, ctx: ToolContext, raw: str | None) -> Path | PathViolation:
        if not raw:
            return ctx.cwd
        try:
            path = resolve_path(raw, base=ctx.cwd, must_exist=True)
        except PathViolation as exc:
            return exc
        if not path.is_dir():
            return PathViolation(f"{path} bir dizin değil.")
        return path

    def assess(self, ctx: ToolContext, **kwargs: Any) -> Decision:
        if sys.platform.startswith("win"):
            return Decision(
                "blocked",
                "Kabuk politikası şimdilik yalnızca POSIX komutları için yazıldı; "
                "Windows'ta run_shell devre dışı (bkz. DECISIONS.md).",
                "windows-unsupported",
            )
        command = kwargs.get("command", "")
        cwd = self._resolve_cwd(ctx, kwargs.get("cwd"))
        if isinstance(cwd, PathViolation):
            return Decision("blocked", str(cwd), "cwd-violation")

        decision = classify_command(command, cwd=cwd)
        if decision.risk == "safe" and ctx.tainted:
            # Bağlama güvenilmeyen içerik girdi: salt okunur bile olsa sor
            # (spec §6.4 — dış içerik tetiklediği çağrılarda risk yükseltilir).
            return Decision(
                escalate(decision.risk, "confirm"),
                "Bağlamda güvenilmeyen dosya/web içeriği var; komut yine de onaya sunuluyor.",
                "taint-escalation",
            )
        return decision

    def preview(self, ctx: ToolContext, **kwargs: Any) -> ToolPreview:
        cwd = self._resolve_cwd(ctx, kwargs.get("cwd"))
        cwd_text = str(cwd) if not isinstance(cwd, PathViolation) else str(ctx.cwd)
        decision = self.assess(ctx, **kwargs)
        return ToolPreview(
            summary=kwargs.get("command", ""),
            paths=[cwd_text],
            detail=f"Çalışma dizini: {cwd_text}\nPolitika: {decision.risk} — {decision.reason}",
        )

    def run(self, ctx: ToolContext, **kwargs: Any) -> ToolResult:
        command = kwargs.get("command", "")
        timeout = int(kwargs.get("timeout") or settings.shell_timeout_seconds)

        cwd = self._resolve_cwd(ctx, kwargs.get("cwd"))
        if isinstance(cwd, PathViolation):
            return ToolResult(False, f"Erişim reddedildi: {cwd}", meta={"error": "path_violation"}, untrusted=False)

        # 2. katman: onay akışında bir hata olsa bile yasaklı komut çalışmasın.
        decision = classify_command(command, cwd=cwd)
        if decision.risk == "blocked":
            return ToolResult(
                False,
                f"Komut güvenlik politikası tarafından reddedildi: {decision.reason}",
                meta={"error": "blocked", "rule": decision.rule},
                untrusted=False,
            )

        if ctx.dry_run and decision.risk != "safe":
            return ToolResult(
                True,
                "[dry-run] Komut çalıştırılmadı. Gerçek modda şu çalışacaktı:\n"
                f"  $ {command}\n"
                f"  (dizin: {cwd})\n"
                "Dry-run kapatmak için .env içindeki DRY_RUN=false yap.",
                meta={"dry_run": True, "risk": decision.risk},
                untrusted=False,
            )

        try:
            proc = subprocess.run(
                ["bash", "-c", command],
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=_child_environment(),
                check=False,
            )
        except subprocess.TimeoutExpired:
            return ToolResult(
                False, f"Komut {timeout} saniyede bitmedi, sonlandırıldı.",
                meta={"error": "timeout"}, untrusted=False,
            )
        except OSError as exc:
            return ToolResult(False, f"Komut başlatılamadı: {exc}", meta={"error": "oserror"}, untrusted=False)

        parts: list[str] = []
        if proc.stdout.strip():
            parts.append(proc.stdout.rstrip())
        if proc.stderr.strip():
            parts.append(f"[stderr]\n{proc.stderr.rstrip()}")
        if not parts:
            parts.append("(çıktı yok)")
        body = truncate_middle("\n".join(parts), settings.tool_output_limit)
        status = f"[çıkış kodu {proc.returncode}]"
        return ToolResult(
            ok=proc.returncode == 0,
            output=f"{body}\n{status}",
            meta={"returncode": proc.returncode, "risk": decision.risk, "cwd": str(cwd)},
        )


run_shell = register(RunShell())
