"""Araç testleri: dosya araçları + run_shell'in politika/dry-run davranışı."""

from __future__ import annotations

from pathlib import Path

from app.agent.tools import get_tool
from app.agent.tools.base import ToolContext, truncate_middle


def make_ctx(workspace: Path, **kwargs) -> ToolContext:
    return ToolContext(cwd=workspace, session_id="test", dry_run=False, **kwargs)


# --- read_file ------------------------------------------------------------


def test_read_file_reads_workspace_file(workspace: Path) -> None:
    (workspace / "notlar.txt").write_text("bir\niki\nüç\n", encoding="utf-8")
    result = get_tool("read_file").run(make_ctx(workspace), path="notlar.txt")
    assert result.ok
    assert "bir" in result.output and "üç" in result.output
    assert result.untrusted is True  # dosya içeriği dış veri sayılır


def test_read_file_line_range(workspace: Path) -> None:
    (workspace / "uzun.txt").write_text("\n".join(f"satır{i}" for i in range(1, 21)), encoding="utf-8")
    result = get_tool("read_file").run(make_ctx(workspace), path="uzun.txt", start_line=5, end_line=7)
    assert "satır5" in result.output and "satır7" in result.output
    assert "satır8" not in result.output


def test_read_file_outside_workspace_is_refused(workspace: Path) -> None:
    result = get_tool("read_file").run(make_ctx(workspace), path="/etc/passwd")
    assert not result.ok
    assert "Erişim reddedildi" in result.output


def test_read_file_denied_pattern_is_refused(workspace: Path) -> None:
    (workspace / ".env").write_text("OPENROUTER_API_KEY=sk-gizli", encoding="utf-8")
    result = get_tool("read_file").run(make_ctx(workspace), path=".env")
    assert not result.ok
    assert "sk-gizli" not in result.output


def test_read_file_rejects_binary(workspace: Path) -> None:
    (workspace / "resim.bin").write_bytes(b"\x89PNG\x00\x00\x01binary")
    result = get_tool("read_file").run(make_ctx(workspace), path="resim.bin")
    assert not result.ok
    assert "ikili" in result.output


def test_read_file_assess_blocks_bad_path(workspace: Path) -> None:
    decision = get_tool("read_file").assess(make_ctx(workspace), path="../../../etc/passwd")
    assert decision.risk == "blocked"


# --- list_dir -------------------------------------------------------------


def test_list_dir_lists_entries(workspace: Path) -> None:
    (workspace / "a.pdf").write_text("x", encoding="utf-8")
    (workspace / "b.txt").write_text("x", encoding="utf-8")
    (workspace / "altdizin").mkdir()
    result = get_tool("list_dir").run(make_ctx(workspace), path=str(workspace))
    assert result.ok
    assert "a.pdf" in result.output and "b.txt" in result.output and "altdizin" in result.output


def test_list_dir_pattern_filter(workspace: Path) -> None:
    (workspace / "a.pdf").write_text("x", encoding="utf-8")
    (workspace / "b.txt").write_text("x", encoding="utf-8")
    result = get_tool("list_dir").run(make_ctx(workspace), path=str(workspace), pattern="*.pdf")
    assert "a.pdf" in result.output and "b.txt" not in result.output


def test_list_dir_hides_dotfiles_by_default(workspace: Path) -> None:
    (workspace / ".gizli").write_text("x", encoding="utf-8")
    result = get_tool("list_dir").run(make_ctx(workspace), path=str(workspace))
    assert ".gizli" not in result.output
    assert "gizli giriş atlandı" in result.output


def test_list_dir_outside_workspace_is_refused(workspace: Path) -> None:
    result = get_tool("list_dir").run(make_ctx(workspace), path="/etc")
    assert not result.ok


# --- search_files ---------------------------------------------------------


def test_search_files_finds_match(workspace: Path) -> None:
    (workspace / "kod.py").write_text("def merhaba():\n    return 42\n", encoding="utf-8")
    result = get_tool("search_files").run(make_ctx(workspace), query="merhaba")
    assert result.ok
    assert "kod.py" in result.output


def test_search_files_skips_denied_files(workspace: Path) -> None:
    (workspace / ".env").write_text("SECRET=parolam123\n", encoding="utf-8")
    (workspace / "temiz.txt").write_text("parolam123 burada da var\n", encoding="utf-8")
    result = get_tool("search_files").run(make_ctx(workspace), query="parolam123")
    assert ".env" not in result.output
    assert "temiz.txt" in result.output


def test_search_files_outside_workspace_is_refused(workspace: Path) -> None:
    result = get_tool("search_files").run(make_ctx(workspace), query="root", path="/etc")
    assert not result.ok


# --- run_shell ------------------------------------------------------------


def test_run_shell_executes_safe_command(workspace: Path) -> None:
    (workspace / "veri.txt").write_text("merhaba\n", encoding="utf-8")
    result = get_tool("run_shell").run(make_ctx(workspace), command="cat veri.txt")
    assert result.ok
    assert "merhaba" in result.output


def test_run_shell_refuses_blocked_command_even_if_called_directly(workspace: Path) -> None:
    """Döngü atlansa bile araç kendi kontrolünü yapar (2. savunma katmanı)."""
    canary = workspace / "kurban.txt"
    canary.write_text("duruyorum", encoding="utf-8")
    result = get_tool("run_shell").run(make_ctx(workspace), command=f"sudo rm -rf {canary}")
    assert not result.ok
    assert "reddedildi" in result.output.lower()
    assert canary.exists()


def test_run_shell_dry_run_does_not_execute(workspace: Path) -> None:
    target = workspace / "silinecek.txt"
    target.write_text("x", encoding="utf-8")
    ctx = ToolContext(cwd=workspace, session_id="test", dry_run=True)
    result = get_tool("run_shell").run(ctx, command=f"rm {target}")
    assert result.ok
    assert "[dry-run]" in result.output
    assert target.exists(), "dry-run modunda dosya silinmemeliydi"


def test_run_shell_dry_run_still_runs_readonly(workspace: Path) -> None:
    ctx = ToolContext(cwd=workspace, session_id="test", dry_run=True)
    result = get_tool("run_shell").run(ctx, command="echo merhaba")
    assert result.ok and "merhaba" in result.output


def test_run_shell_strips_secrets_from_environment(workspace: Path, monkeypatch) -> None:
    """API anahtarları alt sürece geçmez — komut onları okuyamaz."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-cok-gizli")
    monkeypatch.setenv("HARMLESS_VAR", "gorunebilir")

    from app.agent.tools.shell import _child_environment

    env = _child_environment()
    assert "OPENROUTER_API_KEY" not in env
    assert env.get("HARMLESS_VAR") == "gorunebilir"

    result = get_tool("run_shell").run(make_ctx(workspace), command="echo $OPENROUTER_API_KEY")
    assert "sk-cok-gizli" not in result.output


def test_run_shell_cwd_must_be_in_workspace(workspace: Path) -> None:
    result = get_tool("run_shell").run(make_ctx(workspace), command="ls", cwd="/etc")
    assert not result.ok
    assert "Erişim reddedildi" in result.output


def test_run_shell_timeout(workspace: Path) -> None:
    result = get_tool("run_shell").run(make_ctx(workspace), command="sleep 5", timeout=1)
    assert not result.ok
    assert "zaman aşımı" in result.output.lower() or "bitmedi" in result.output


def test_run_shell_is_disabled_on_windows(workspace: Path, monkeypatch) -> None:
    """Kabuk politikası POSIX'e göre yazıldı; Windows'ta tahmin yürütmek yerine kapanır."""
    monkeypatch.setattr("app.agent.tools.shell.sys.platform", "win32")
    decision = get_tool("run_shell").assess(make_ctx(workspace), command="dir")
    assert decision.risk == "blocked"
    assert decision.rule == "windows-unsupported"


def test_file_tools_still_work_on_windows(workspace: Path, monkeypatch) -> None:
    """Dosya araçları pathlib üzerinden çalışıyor, platformdan bağımsız."""
    monkeypatch.setattr("app.agent.tools.shell.sys.platform", "win32")
    (workspace / "notlar.txt").write_text("veri", encoding="utf-8")
    assert get_tool("read_file").run(make_ctx(workspace), path="notlar.txt").ok


def test_run_shell_taint_escalates_safe_to_confirm(workspace: Path) -> None:
    ctx = make_ctx(workspace, tainted=True)
    decision = get_tool("run_shell").assess(ctx, command="ls")
    assert decision.risk == "confirm"
    assert decision.rule == "taint-escalation"


# --- yardımcılar ----------------------------------------------------------


def test_truncate_middle_keeps_both_ends() -> None:
    text = "BAS" + ("x" * 5000) + "SON"
    result = truncate_middle(text, 500)
    assert len(result) <= 500 + 120
    assert result.startswith("BAS")
    assert result.endswith("SON")
    assert "kırpıldı" in result


def test_truncate_middle_leaves_short_text_alone() -> None:
    assert truncate_middle("kısa", 4000) == "kısa"
