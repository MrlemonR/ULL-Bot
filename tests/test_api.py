import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.store import DEFAULT_MODEL, Store


def ndjson(*chunks: dict) -> bytes:
    return "".join(json.dumps(c) + "\n" for c in chunks).encode()


class FakeOllama:
    """Ollama'nın /api/chat ve /api/tags uçlarını taklit eder."""

    def __init__(self):
        self.requests: list[dict] = []
        self.chat_response = httpx.Response(
            200,
            content=ndjson(
                {"message": {"role": "assistant", "content": "Mer"}, "done": False},
                {"message": {"role": "assistant", "content": "haba"}, "done": False},
                {"model": DEFAULT_MODEL, "message": {"content": ""}, "done": True,
                 "eval_count": 2, "eval_duration": 1_000_000_000},
            ),
        )
        self.tags_response = httpx.Response(200, json={"models": [{"name": "qwen2.5:7b"}, {"name": "llama3:8b"}]})
        self.fail_connect = False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if self.fail_connect:
            raise httpx.ConnectError("bağlantı reddedildi", request=request)
        if request.url.path == "/api/tags":
            return self.tags_response
        if request.url.path == "/api/chat":
            self.requests.append(json.loads(request.content))
            return self.chat_response
        return httpx.Response(404)


@pytest.fixture
def ollama():
    return FakeOllama()


@pytest.fixture
def client(tmp_path, ollama):
    app = create_app(data_dir=tmp_path, ollama_url="http://ollama.test", transport=httpx.MockTransport(ollama))
    with TestClient(app) as c:
        yield c


def events(resp) -> list[dict]:
    return [json.loads(line) for line in resp.text.splitlines() if line.strip()]


def test_default_state_has_one_active_persona(client):
    s = client.get("/api/state").json()
    assert s["model"] == DEFAULT_MODEL
    assert len(s["personas"]) == 1
    assert s["active_persona_id"] == s["personas"][0]["id"]


def test_index_is_served(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "ULL-Bot" in r.text


def test_persona_crud_and_active_fallback(client):
    first = client.get("/api/state").json()["personas"][0]
    p = client.post("/api/personas", json={"name": "Hacker", "prompt": "Kısa konuş."}).json()
    assert p["name"] == "Hacker"

    upd = client.put(f"/api/personas/{p['id']}", json={"name": "Neo", "prompt": "Uyan."}).json()
    assert upd == {"id": p["id"], "name": "Neo", "prompt": "Uyan."}

    s = client.put("/api/active", json={"persona_id": p["id"]}).json()
    assert s["active_persona_id"] == p["id"]

    # Aktif kişilik silinince ilk kalan kişiliğe düşülür.
    s = client.delete(f"/api/personas/{p['id']}").json()
    assert s["active_persona_id"] == first["id"]
    assert client.delete(f"/api/personas/{p['id']}").status_code == 404
    assert client.put("/api/active", json={"persona_id": "yok"}).status_code == 404


def test_empty_persona_name_rejected(client):
    assert client.post("/api/personas", json={"name": "", "prompt": "x"}).status_code == 422


def test_state_persists_to_disk(tmp_path, ollama):
    transport = httpx.MockTransport(ollama)
    with TestClient(create_app(data_dir=tmp_path, transport=transport)) as c:
        c.post("/api/personas", json={"name": "Kalıcı", "prompt": "p"})
        c.put("/api/settings", json={"model": "llama3:8b"})
    with TestClient(create_app(data_dir=tmp_path, transport=transport)) as c:
        s = c.get("/api/state").json()
    assert s["model"] == "llama3:8b"
    assert [p["name"] for p in s["personas"]][-1] == "Kalıcı"


def test_chat_streams_tokens_with_master_prompt(client, ollama):
    p = client.post("/api/personas", json={"name": "Neo", "prompt": "Sen Neo'sun."}).json()
    r = client.post("/api/chat", json={"messages": [{"role": "user", "content": "selam"}], "persona_id": p["id"]})
    assert r.status_code == 200
    evs = events(r)
    assert "".join(e["content"] for e in evs if e["type"] == "token") == "Merhaba"
    assert evs[-1]["type"] == "done" and evs[-1]["eval_count"] == 2

    sent = ollama.requests[-1]
    assert sent["model"] == DEFAULT_MODEL
    assert sent["stream"] is True
    assert sent["messages"][0] == {"role": "system", "content": "Sen Neo'sun."}
    assert sent["messages"][1] == {"role": "user", "content": "selam"}


def test_chat_uses_active_persona_by_default(client, ollama):
    active = client.get("/api/state").json()["personas"][0]
    client.post("/api/chat", json={"messages": [{"role": "user", "content": "hey"}]})
    assert ollama.requests[-1]["messages"][0]["content"] == active["prompt"]


def test_chat_without_persona_sends_no_system_message(client, ollama):
    pid = client.get("/api/state").json()["personas"][0]["id"]
    client.delete(f"/api/personas/{pid}")
    client.post("/api/chat", json={"messages": [{"role": "user", "content": "hey"}]})
    assert [m["role"] for m in ollama.requests[-1]["messages"]] == ["user"]


def test_chat_rejects_system_role_from_client(client):
    r = client.post("/api/chat", json={"messages": [{"role": "system", "content": "x"}]})
    assert r.status_code == 422


def test_chat_unknown_persona_is_404(client):
    r = client.post("/api/chat", json={"messages": [{"role": "user", "content": "x"}], "persona_id": "yok"})
    assert r.status_code == 404


def test_chat_missing_model_gives_pull_hint(client, ollama):
    ollama.chat_response = httpx.Response(404, json={"error": 'model "qwen2.5:7b" not found, try pulling it first'})
    evs = events(client.post("/api/chat", json={"messages": [{"role": "user", "content": "x"}]}))
    assert evs == [{"type": "error", "message": "Model bulunamadı: qwen2.5:7b. Kurmak için: ollama pull qwen2.5:7b"}]


def test_chat_when_ollama_down(client, ollama):
    ollama.fail_connect = True
    evs = events(client.post("/api/chat", json={"messages": [{"role": "user", "content": "x"}]}))
    assert evs[0]["type"] == "error"
    assert "bağlanılamadı" in evs[0]["message"]


def test_models_ok_and_down(client, ollama):
    assert client.get("/api/models").json() == {"ok": True, "models": ["llama3:8b", "qwen2.5:7b"]}
    ollama.fail_connect = True
    r = client.get("/api/models").json()
    assert r["ok"] is False and r["models"] == []


def test_corrupt_state_file_is_backed_up(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{bozuk", "utf-8")
    store = Store(path)
    assert store.snapshot()["model"] == DEFAULT_MODEL
    assert list(tmp_path.glob("state.bozuk-*.json"))
