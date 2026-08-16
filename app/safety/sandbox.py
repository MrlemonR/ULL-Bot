"""Dosya yolu doğrulama: çalışma alanı sınırları, traversal ve symlink koruması.

Tüm dosya araçları (ve kabuk komutlarındaki yol argümanları) buradan geçer.
Tek kural: yol önce **sembolik bağlar çözülerek** normalize edilir, sonra
allow/deny listelerine bakılır. Bu sayede hem `../../../etc/passwd` hem de
çalışma alanı içine kurulmuş `link -> /etc` gibi bir symlink aynı noktada
yakalanır.
"""

from __future__ import annotations

import fnmatch
import os
from dataclasses import dataclass
from pathlib import Path

import yaml

from app.settings import settings

CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "workspace.yaml"

# Ajanın çalışma alanı ne olursa olsun asla dokunamayacağı yollar.
# Config'den gevşetilemez — audit log'un kendisi ve sistem dizinleri buraya ait.
HARD_DENIED_PATHS: tuple[Path, ...] = (
    Path("/etc"),
    Path("/boot"),
    Path("/sys"),
    Path("/proc"),
    Path("/dev"),
    Path("/root"),
    Path("/usr"),
    Path("/bin"),
    Path("/sbin"),
    Path("/lib"),
    Path("/lib64"),
    Path("/var/lib"),
    Path("/var/log"),
)


class PathViolation(Exception):
    """Yol çalışma alanının dışında ya da açıkça yasaklanmış."""


@dataclass(frozen=True)
class WorkspaceConfig:
    allowed_paths: tuple[Path, ...]
    denied_paths: tuple[Path, ...]
    denied_globs: tuple[str, ...]
    max_read_bytes: int = 262_144


def _expand(raw: str) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(str(raw))))


def _normalize_root(raw: str | Path) -> Path:
    """Kökleri symlink çözerek normalize et.

    Kökün kendisi symlink ise (örn. `/home` -> `/mnt/home`) çözülmezse
    `is_relative_to` karşılaştırması hatalı negatif verir.
    """
    path = _expand(str(raw))
    try:
        return path.resolve()
    except OSError:  # pragma: no cover - erişilemeyen mount vb.
        return path.absolute()


def load_workspace_config(path: Path | None = None) -> WorkspaceConfig:
    config_path = path or CONFIG_PATH
    raw: dict = {}
    if config_path.is_file():
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}

    allowed = [_normalize_root(p) for p in raw.get("allowed_paths") or []]
    # WORKSPACE_ROOT her zaman izinli; config'de tekrar yazılmasına gerek yok.
    workspace_root = _normalize_root(settings.resolved_workspace_root)
    if workspace_root not in allowed:
        allowed.insert(0, workspace_root)

    denied = [_normalize_root(p) for p in raw.get("denied_paths") or []]
    # Sistemin kendisi ve ajanın kendi kayıtları her zaman yasak (spec §6.3:
    # "audit log asla ajan tarafından okunabilir/yazılabilir olmasın").
    denied.extend(HARD_DENIED_PATHS)
    denied.append(_normalize_root(settings.data_dir))

    return WorkspaceConfig(
        allowed_paths=tuple(allowed),
        denied_paths=tuple(denied),
        denied_globs=tuple(raw.get("denied_globs") or []),
        max_read_bytes=int(raw.get("max_read_bytes") or 262_144),
    )


_cached_config: WorkspaceConfig | None = None


def get_workspace_config(*, refresh: bool = False) -> WorkspaceConfig:
    global _cached_config
    if _cached_config is None or refresh:
        _cached_config = load_workspace_config()
    return _cached_config


def _is_within(path: Path, root: Path) -> bool:
    return path == root or path.is_relative_to(root)


def _matches_glob(path: Path, patterns: tuple[str, ...]) -> str | None:
    text = path.as_posix()
    for pattern in patterns:
        if fnmatch.fnmatch(path.name, pattern) or fnmatch.fnmatch(text, pattern):
            return pattern
        # "*/.git/config" gibi kalıplar yolun herhangi bir sonekine de uymalı.
        if "/" in pattern and fnmatch.fnmatch(text, f"*{pattern}"):
            return pattern
    return None


def resolve_path(
    raw: str | Path,
    *,
    base: Path | None = None,
    config: WorkspaceConfig | None = None,
    must_exist: bool = False,
) -> Path:
    """Yolu normalize et ve çalışma alanına karşı doğrula.

    Göreli yollar `base` (varsayılan: WORKSPACE_ROOT) altında çözülür.
    İhlal varsa `PathViolation` fırlatır.
    """
    config = config or get_workspace_config()
    base = base or settings.resolved_workspace_root

    text = str(raw).strip()
    if not text:
        raise PathViolation("Boş yol.")
    if "\x00" in text:
        raise PathViolation("Yol null byte içeriyor.")

    candidate = _expand(text)
    if not candidate.is_absolute():
        candidate = Path(base) / candidate

    try:
        resolved = candidate.resolve()
    except (OSError, RuntimeError) as exc:  # symlink döngüsü vb.
        raise PathViolation(f"Yol çözümlenemedi: {exc}") from exc

    for denied in config.denied_paths:
        if _is_within(resolved, denied):
            raise PathViolation(f"Yasaklı dizin: {denied} ({resolved} buraya düşüyor).")

    pattern = _matches_glob(resolved, config.denied_globs)
    if pattern is not None:
        raise PathViolation(f"Yasaklı dosya kalıbı: '{pattern}' ({resolved.name}).")

    if not any(_is_within(resolved, allowed) for allowed in config.allowed_paths):
        allowed_list = ", ".join(str(p) for p in config.allowed_paths)
        raise PathViolation(
            f"{resolved} çalışma alanının dışında. İzinli kökler: {allowed_list}"
        )

    if must_exist and not resolved.exists():
        raise PathViolation(f"Yol bulunamadı: {resolved}")

    return resolved


def is_allowed(raw: str | Path, *, base: Path | None = None) -> bool:
    """`resolve_path`in istisna fırlatmayan hâli."""
    try:
        resolve_path(raw, base=base)
    except PathViolation:
        return False
    return True


def describe_workspace() -> str:
    config = get_workspace_config()
    allowed = ", ".join(str(p) for p in config.allowed_paths)
    return f"İzinli kökler: {allowed}"
