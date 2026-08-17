"""Faz 7 REST uçları: oturum geçmişi, arama, kullanım grafiği, kalıcı hafıza.

`/ws/chat` ve `/api/quota` gibi mevcut uçların TestClient testi yok (proje
şimdiye kadar bunları canlı/manuel doğruladı) — burada sadece Faz 7'de
eklenen, UI'sı olmayan yeni uçlar test ediliyor.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app
from app.memory.store import ensure_session, save_message, set_note
from app.quota.tracker import record_usage


def test_sessions_empty_by_default(workspace: Path) -> None:
    client = TestClient(app)
    resp = client.get("/api/sessions")
    assert resp.status_code == 200
    assert resp.json() == {"sessions": []}


def test_sessions_lists_with_derived_title(workspace: Path) -> None:
    ensure_session("s1")
    save_message("s1", "user", "ULL-Bot'a merhaba de")
    save_message("s1", "assistant", "Merhaba!")

    client = TestClient(app)
    resp = client.get("/api/sessions")
    rows = resp.json()["sessions"]
    assert len(rows) == 1
    assert rows[0]["id"] == "s1"
    assert rows[0]["title"] == "ULL-Bot'a merhaba de"
    assert rows[0]["message_count"] == 2


def test_session_messages_returns_full_record(workspace: Path) -> None:
    ensure_session("s1")
    save_message("s1", "user", "merhaba")
    save_message("s1", "tool", "dir listing", tool_name="list_dir")

    client = TestClient(app)
    resp = client.get("/api/sessions/s1/messages")
    body = resp.json()
    assert body["session_id"] == "s1"
    roles = [m["role"] for m in body["messages"]]
    assert roles == ["user", "tool"]


def test_search_finds_matching_message(workspace: Path) -> None:
    ensure_session("s1")
    save_message("s1", "user", "pdf dosyalarını listele")
    ensure_session("s2")
    save_message("s2", "user", "hava durumu nasıl")

    client = TestClient(app)
    resp = client.get("/api/search", params={"q": "pdf"})
    results = resp.json()["results"]
    assert len(results) == 1
    assert results[0]["session_id"] == "s1"


def test_search_empty_query_returns_nothing(workspace: Path) -> None:
    client = TestClient(app)
    resp = client.get("/api/search", params={"q": ""})
    assert resp.json()["results"] == []


def test_usage_graph_groups_by_day_and_provider(workspace: Path) -> None:
    record_usage(provider="groq", model="m", prompt_tokens=10, completion_tokens=5)
    record_usage(provider="groq", model="m", prompt_tokens=1, completion_tokens=1)
    record_usage(provider="openrouter", model="m", prompt_tokens=2, completion_tokens=2)

    client = TestClient(app)
    resp = client.get("/api/usage/graph")
    points = resp.json()["points"]
    by_provider = {p["provider"]: p for p in points}
    assert by_provider["groq"]["requests"] == 2
    assert by_provider["groq"]["tokens"] == 17
    assert by_provider["openrouter"]["requests"] == 1


def test_memory_notes_roundtrip(workspace: Path) -> None:
    set_note("preferred_shell", "fish")
    client = TestClient(app)

    resp = client.get("/api/memory")
    assert resp.json()["notes"] == [
        {"key": "preferred_shell", "value": "fish", "updated_at": resp.json()["notes"][0]["updated_at"]}
    ]

    resp = client.delete("/api/memory/preferred_shell")
    assert resp.json() == {"ok": True, "key": "preferred_shell"}
    assert client.get("/api/memory").json()["notes"] == []


def test_memory_delete_missing_key_reports_false(workspace: Path) -> None:
    client = TestClient(app)
    resp = client.delete("/api/memory/nope")
    assert resp.json() == {"ok": False, "key": "nope"}
