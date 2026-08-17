"""Faz 7: kalıcı hafıza — `memory_notes` store fonksiyonları, `remember`

aracı, ve bunların sistem promptuna gömülmesi.
"""

from __future__ import annotations

from pathlib import Path

from app.agent.prompts import system_prompt
from app.agent.tools import get_tool
from app.agent.tools.base import ToolContext
from app.memory.store import delete_note, get_note, list_notes, set_note


def make_ctx(workspace: Path, **kwargs) -> ToolContext:
    return ToolContext(cwd=workspace, session_id="test", dry_run=False, **kwargs)


# --- store -------------------------------------------------------------


def test_set_and_get_note(workspace: Path) -> None:
    set_note("preferred_shell", "bash")
    assert get_note("preferred_shell") == "bash"


def test_get_missing_note_is_none(workspace: Path) -> None:
    assert get_note("does-not-exist") is None


def test_set_note_overwrites_existing_key(workspace: Path) -> None:
    set_note("k", "first")
    set_note("k", "second")
    assert get_note("k") == "second"
    assert len(list_notes()) == 1


def test_list_notes_is_sorted_by_key(workspace: Path) -> None:
    set_note("zebra", "1")
    set_note("alpha", "2")
    assert [note["key"] for note in list_notes()] == ["alpha", "zebra"]


def test_delete_note_removes_it(workspace: Path) -> None:
    set_note("k", "v")
    assert delete_note("k") is True
    assert get_note("k") is None


def test_delete_missing_note_returns_false(workspace: Path) -> None:
    assert delete_note("nope") is False


# --- remember tool -------------------------------------------------------


def test_remember_tool_writes_note(workspace: Path) -> None:
    result = get_tool("remember").run(make_ctx(workspace), key="fav_editor", value="vim")
    assert result.ok
    assert get_note("fav_editor") == "vim"


def test_remember_tool_is_safe_risk(workspace: Path) -> None:
    decision = get_tool("remember").assess(make_ctx(workspace), key="k", value="v")
    assert decision.risk == "safe"


def test_remember_rejects_empty_key(workspace: Path) -> None:
    result = get_tool("remember").run(make_ctx(workspace), key="  ", value="v")
    assert not result.ok


def test_remember_rejects_empty_value(workspace: Path) -> None:
    result = get_tool("remember").run(make_ctx(workspace), key="k", value="")
    assert not result.ok


def test_remember_truncates_overlong_value(workspace: Path) -> None:
    get_tool("remember").run(make_ctx(workspace), key="k", value="x" * 3000)
    assert len(get_note("k")) == 2000


# --- sistem promptuna gömülme ---------------------------------------------


def test_system_prompt_has_no_memory_section_when_empty(workspace: Path) -> None:
    prompt = system_prompt(str(workspace))
    assert "Remembered notes" not in prompt


def test_system_prompt_includes_notes(workspace: Path) -> None:
    set_note("preferred_shell", "fish")
    prompt = system_prompt(str(workspace))
    assert "Remembered notes" in prompt
    assert "preferred_shell: fish" in prompt
