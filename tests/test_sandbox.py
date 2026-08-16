"""Yol doğrulama testleri: traversal, symlink kaçışı, yasaklı kalıplar (spec §10)."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.safety.sandbox import PathViolation, is_allowed, resolve_path


def test_path_inside_workspace_is_allowed(workspace: Path) -> None:
    (workspace / "notlar.txt").write_text("x", encoding="utf-8")
    resolved = resolve_path("notlar.txt", base=workspace, must_exist=True)
    assert resolved == (workspace / "notlar.txt").resolve()


def test_nested_path_is_allowed(workspace: Path) -> None:
    nested = workspace / "a" / "b"
    nested.mkdir(parents=True)
    assert is_allowed(nested, base=workspace)


@pytest.mark.parametrize(
    "attempt",
    [
        "../../../etc/passwd",
        "../../etc/passwd",
        "..",
        "a/../../..",
        "./a/b/../../../../etc",
        "/etc/passwd",
        "/etc",
        "~/.ssh/id_rsa",
        "/root/.bashrc",
        "/proc/self/environ",
        "/sys/kernel",
        "/boot/vmlinuz",
    ],
)
def test_path_traversal_is_rejected(attempt: str, workspace: Path) -> None:
    with pytest.raises(PathViolation):
        resolve_path(attempt, base=workspace)


def test_symlink_escape_is_rejected(workspace: Path) -> None:
    """Çalışma alanı içindeki symlink dışarıyı gösteriyorsa geçersiz."""
    link = workspace / "kacis"
    link.symlink_to("/etc")
    with pytest.raises(PathViolation):
        resolve_path(link, base=workspace)
    with pytest.raises(PathViolation):
        resolve_path("kacis/passwd", base=workspace)


def test_symlink_to_parent_of_workspace_is_rejected(workspace: Path, tmp_path: Path) -> None:
    outside = tmp_path / "disarida"
    outside.mkdir()
    (outside / "gizli.txt").write_text("sır", encoding="utf-8")
    link = workspace / "baglanti"
    link.symlink_to(outside)
    with pytest.raises(PathViolation):
        resolve_path("baglanti/gizli.txt", base=workspace)


def test_symlink_inside_workspace_is_fine(workspace: Path) -> None:
    target = workspace / "gercek.txt"
    target.write_text("veri", encoding="utf-8")
    link = workspace / "kisayol.txt"
    link.symlink_to(target)
    assert resolve_path(link, base=workspace) == target.resolve()


@pytest.mark.parametrize(
    "name",
    [".env", ".env.local", "gizli.pem", "sunucu.key", "id_rsa", "id_ed25519.pub", ".npmrc"],
)
def test_denied_globs_are_rejected_even_inside_workspace(name: str, workspace: Path) -> None:
    path = workspace / name
    path.write_text("secret", encoding="utf-8")
    with pytest.raises(PathViolation):
        resolve_path(path, base=workspace)


def test_agent_cannot_reach_its_own_audit_log(workspace: Path) -> None:
    """Audit log ve DB, `settings.data_dir` altında ve bu dizin yasaklı (spec §6.3)."""
    from app.settings import settings

    with pytest.raises(PathViolation):
        resolve_path(settings.audit_log_path, base=workspace)
    with pytest.raises(PathViolation):
        resolve_path(settings.resolved_db_path, base=workspace)


def test_null_byte_is_rejected(workspace: Path) -> None:
    with pytest.raises(PathViolation):
        resolve_path("notlar\x00.txt", base=workspace)


def test_must_exist_flag(workspace: Path) -> None:
    assert is_allowed(workspace / "henuz-yok.txt", base=workspace)
    with pytest.raises(PathViolation):
        resolve_path("henuz-yok.txt", base=workspace, must_exist=True)
