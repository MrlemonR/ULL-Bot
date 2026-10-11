import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app import catalog, hardware
from app.db import DEFAULT_MODEL
from app.main import build_messages, clean_title, create_app


def ndjson(*chunks: dict) -> bytes:
    return "".join(json.dumps(c) + "\n" for c in chunks).encode()


def reply(*parts: str, done_extra: dict | None = None) -> httpx.Response:
    chunks = [{"message": {"role": "assistant", "content": p}, "done": False} for p in parts]
    chunks.append({"model": DEFAULT_MODEL, "message": {"content": ""}, "done": True,
                   "eval_count": len(parts), "eval_duration": 1_000_000_000, **(done_extra or {})})
    return httpx.Response(200, content=ndjson(*chunks))


class FakeOllama:
    """Ollama'nın kullandığımız uçlarını taklit eder ve gelen istekleri kaydeder."""

    def __init__(self):
        self.chat_requests: list[dict] = []
        self.replies: list[httpx.Response] = []
        self.title = "Matrix Sohbeti"
        self.tags = [
            {"name": "qwen2.5:7b", "size": 4_700_000_000, "details": {"parameter_size": "7.6B", "quantization_level": "Q4_K_M", "family": "qwen2"}},
            {"name": "phi4-mini:latest", "size": 2_500_000_000, "details": {}},
        ]
        self.fail_connect = False
        self.deleted: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if self.fail_connect:
            raise httpx.ConnectError("bağlantı reddedildi", request=request)
        path = request.url.path
        if path == "/api/tags":
            return httpx.Response(200, json={"models": self.tags})
        if path == "/api/chat":
            body = json.loads(request.content)
            if not body.get("stream", True):
                return httpx.Response(200, json={"message": {"role": "assistant", "content": f'"{self.title}".'}})
            self.chat_requests.append(body)
            return self.replies.pop(0) if self.replies else reply("Mer", "haba")
        if path == "/api/pull":
            return httpx.Response(200, content=ndjson(
                {"status": "pulling manifest"},
                {"status": "pulling abc", "total": 100, "completed": 50},
                {"status": "success"},
            ))
        if path == "/api/delete":
            self.deleted.append(json.loads(request.content)["model"])
            return httpx.Response(200)
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
    assert resp.status_code == 200, resp.text
    return [json.loads(line) for line in resp.text.splitlines() if line.strip()]


def send(client, **body) -> list[dict]:
    return events(client.post("/api/send", json=body))


def tokens(evs) -> str:
    return "".join(e["content"] for e in evs if e["type"] == "token")


# --- açılış / ayarlar ----------------------------------------------------------------


def test_bootstrap_defaults(client):
    b = client.get("/api/bootstrap").json()
    assert b["settings"]["model"] == DEFAULT_MODEL
    assert b["settings"]["num_ctx"] == 8192
    assert b["settings"]["persona_reminder"] is True
    assert b["settings"]["appearance"]["rain_enabled"] is True
    assert len(b["personas"]) == 1
    assert b["settings"]["active_persona_id"] == b["personas"][0]["id"]
    assert b["projects"] == [] and b["chats"] == []


def test_index_and_modules_served(client):
    assert "ULL-Bot" in client.get("/").text
    assert client.get("/js/main.js").status_code == 200


def test_legacy_state_json_is_imported(tmp_path, ollama):
    (tmp_path / "state.json").write_text(json.dumps({
        "model": "llama3:8b",
        "active_persona_id": "neo1",
        "personas": [{"id": "def1", "name": "Varsayılan", "prompt": "a"}, {"id": "neo1", "name": "Neo", "prompt": "Uyan."}],
    }), "utf-8")
    with TestClient(create_app(data_dir=tmp_path, transport=httpx.MockTransport(ollama))) as c:
        b = c.get("/api/bootstrap").json()
    assert [p["name"] for p in b["personas"]] == ["Varsayılan", "Neo"]
    assert b["settings"]["model"] == "llama3:8b"
    assert b["settings"]["active_persona_id"] == "neo1"


def test_settings_patch_and_validation(client):
    s = client.put("/api/settings", json={"temperature": 0.3, "num_ctx": 16384}).json()
    assert s["temperature"] == 0.3 and s["num_ctx"] == 16384
    a = client.get("/api/settings").json()["appearance"]
    a.update(rain_enabled=False, font_size=18, accent="#00ff00")
    s = client.put("/api/settings", json={"appearance": a}).json()
    assert s["appearance"]["rain_enabled"] is False and s["appearance"]["font_size"] == 18
    assert client.put("/api/settings", json={"appearance": {**a, "font_size": 99}}).status_code == 422
    assert client.put("/api/settings", json={"appearance": {**a, "accent": "red"}}).status_code == 422


def test_settings_persist_across_restarts(tmp_path, ollama):
    transport = httpx.MockTransport(ollama)
    with TestClient(create_app(data_dir=tmp_path, transport=transport)) as c:
        c.put("/api/settings", json={"model": "gemma3:12b"})
        c.post("/api/projects", json={"name": "Kalıcı"})
    with TestClient(create_app(data_dir=tmp_path, transport=transport)) as c:
        b = c.get("/api/bootstrap").json()
    assert b["settings"]["model"] == "gemma3:12b"
    assert [p["name"] for p in b["projects"]] == ["Kalıcı"]


# --- kişilikler ----------------------------------------------------------------------


def test_persona_crud_and_active_fallback(client):
    first = client.get("/api/personas").json()[0]
    p = client.post("/api/personas", json={"name": "Neo", "prompt": "Uyan."}).json()
    assert client.put(f"/api/personas/{p['id']}", json={"name": "Neo2", "prompt": "x"}).json()["name"] == "Neo2"
    assert client.put("/api/active", json={"persona_id": p["id"]}).json()["active_persona_id"] == p["id"]
    s = client.delete(f"/api/personas/{p['id']}").json()
    assert s["active_persona_id"] == first["id"]
    assert client.put("/api/active", json={"persona_id": "yok"}).status_code == 404
    assert client.post("/api/personas", json={"name": "", "prompt": "x"}).status_code == 422


# --- sohbet akışı ve kişiliğin korunması ------------------------------------------------


def test_send_creates_chat_and_stores_both_messages(client):
    evs = send(client, content="Selam, kimsin?")
    assert evs[0]["type"] == "start"
    chat = evs[0]["chat"]
    assert chat["title"] == "Selam, kimsin?"
    assert tokens(evs) == "Merhaba"
    assert evs[-1]["type"] == "done" and evs[-1]["first_answer"] is True

    full = client.get(f"/api/chats/{chat['id']}").json()
    assert [(m["role"], m["content"]) for m in full["messages"]] == [("user", "Selam, kimsin?"), ("assistant", "Merhaba")]
    assert full["messages"][1]["meta"]["persona_name"] == "Varsayılan"
    assert full["messages"][1]["model"] == DEFAULT_MODEL
    assert client.get("/api/chats").json()[0]["id"] == chat["id"]


def test_persona_and_history_sent_on_every_turn(client, ollama):
    """Kullanıcının bildirdiği hata: kişilik yalnızca ilk mesajda çalışıyordu."""
    neo = client.post("/api/personas", json={"name": "Neo", "prompt": "Sen Neo'sun, gizemli konuş."}).json()
    chat_id = send(client, content="selam", persona_id=neo["id"])[0]["chat"]["id"]
    send(client, chat_id=chat_id, content="adın ne?", persona_id=neo["id"])
    send(client, chat_id=chat_id, content="az önce ne sordum?", persona_id=neo["id"])

    third = ollama.chat_requests[-1]
    msgs = third["messages"]
    assert msgs[0]["role"] == "system"
    assert "Sen Neo'sun, gizemli konuş." in msgs[0]["content"]
    assert "TAMAMI boyunca koru" in msgs[0]["content"]
    # Önceki turlar eksiksiz gidiyor.
    assert [m["role"] for m in msgs[1:]] == ["user", "assistant", "user", "assistant", "user"]
    assert msgs[1]["content"] == "selam" and msgs[3]["content"] == "adın ne?"
    # Son mesajın önünde kişilik hatırlatması var; asıl metin sonda.
    assert msgs[-1]["content"].startswith('[Hatırlatma')
    assert '"Neo" kişiliğinde kal' in msgs[-1]["content"]
    assert msgs[-1]["content"].endswith("az önce ne sordum?")
    # Hatırlatma veritabanına yazılmıyor.
    stored = client.get(f"/api/chats/{chat_id}").json()["messages"]
    assert stored[-2]["content"] == "az önce ne sordum?"
    # Bağlam penceresi ve sıcaklık her istekte gidiyor.
    assert third["options"] == {"temperature": 0.7, "num_ctx": 8192}


def test_reminder_can_be_disabled(client, ollama):
    client.put("/api/settings", json={"persona_reminder": False})
    send(client, content="hey")
    assert ollama.chat_requests[-1]["messages"][-1]["content"] == "hey"


def test_project_context_in_system_prompt(client, ollama):
    p = client.post("/api/projects", json={"name": "Freaks Hospital", "context": "Unity 6 ile FPS korku oyunu."}).json()
    evs = send(client, content="silah scripti yaz", project_id=p["id"])
    assert evs[0]["chat"]["project_id"] == p["id"]
    msgs = ollama.chat_requests[-1]["messages"]
    assert "## Proje: Freaks Hospital" in msgs[0]["content"]
    assert "Unity 6 ile FPS korku oyunu." in msgs[0]["content"]
    assert '"Freaks Hospital" projesinin bağlamına uy' in msgs[-1]["content"]
    # Bağlam güncellenince bir sonraki mesajda yenisi gidiyor.
    client.patch(f"/api/projects/{p['id']}", json={"context": "Artık Godot."})
    send(client, chat_id=evs[0]["chat"]["id"], content="devam")
    assert "Artık Godot." in ollama.chat_requests[-1]["messages"][0]["content"]


def test_no_persona_no_project_sends_plain_history(client, ollama):
    pid = client.get("/api/personas").json()[0]["id"]
    client.delete(f"/api/personas/{pid}")
    send(client, content="hey")
    assert ollama.chat_requests[-1]["messages"] == [{"role": "user", "content": "hey"}]


def test_think_tags_are_not_sent_back(client, ollama):
    ollama.replies = [reply("<think>uzun düşünce</think>", "Cevap.")]
    chat_id = send(client, content="soru")[0]["chat"]["id"]
    send(client, chat_id=chat_id, content="devam")
    assistant = [m for m in ollama.chat_requests[-1]["messages"] if m["role"] == "assistant"]
    assert assistant == [{"role": "assistant", "content": "Cevap."}]


def test_thinking_field_streamed_and_stored(client, ollama):
    ollama.replies = [httpx.Response(200, content=ndjson(
        {"message": {"thinking": "hmm "}, "done": False},
        {"message": {"content": "Tamam"}, "done": False},
        {"done": True},
    ))]
    evs = send(client, content="x")
    assert [e["type"] for e in evs][1:3] == ["thinking", "token"]
    msg = client.get(f"/api/chats/{evs[0]['chat']['id']}").json()["messages"][-1]
    assert msg["thinking"] == "hmm " and msg["content"] == "Tamam"


def test_regenerate_replaces_last_answer(client, ollama):
    chat_id = send(client, content="fıkra anlat")[0]["chat"]["id"]
    ollama.replies = [reply("Yeni", " fıkra")]
    evs = send(client, chat_id=chat_id, regenerate=True)
    assert tokens(evs) == "Yeni fıkra"
    assert evs[-1]["first_answer"] is True
    msgs = client.get(f"/api/chats/{chat_id}").json()["messages"]
    assert [(m["role"], m["content"]) for m in msgs] == [("user", "fıkra anlat"), ("assistant", "Yeni fıkra")]
    # Modele eski cevap gitmedi.
    assert [m["role"] for m in ollama.chat_requests[-1]["messages"][1:]] == ["user"]


def test_regenerate_without_user_message_is_rejected(client):
    chat_id = send(client, content="x")[0]["chat"]["id"]
    client.post("/api/send", json={"chat_id": chat_id, "regenerate": False})  # içerik yok, sonda asistan var
    assert client.post("/api/send", json={"chat_id": chat_id}).status_code == 422


def test_ollama_error_keeps_user_message_for_retry(client, ollama):
    ollama.replies = [httpx.Response(404, json={"error": 'model "qwen2.5:7b" not found, try pulling it first'})]
    evs = send(client, content="selam")
    assert evs[-1]["type"] == "error"
    assert "Model bulunamadı: qwen2.5:7b" in evs[-1]["message"]
    chat_id = evs[0]["chat"]["id"]
    assert [m["role"] for m in client.get(f"/api/chats/{chat_id}").json()["messages"]] == ["user"]
    # "Tekrar dene" = içerik olmadan aynı sohbete gönder.
    assert tokens(send(client, chat_id=chat_id, regenerate=True)) == "Merhaba"


def test_ollama_down(client, ollama):
    ollama.fail_connect = True
    evs = send(client, content="x")
    assert evs[-1]["type"] == "error" and "bağlanılamadı" in evs[-1]["message"]


def test_send_validation(client):
    assert client.post("/api/send", json={"content": "  "}).status_code == 422
    assert client.post("/api/send", json={"content": "x", "persona_id": "yok"}).status_code == 404
    assert client.post("/api/send", json={"content": "x", "project_id": "yok"}).status_code == 404
    assert client.post("/api/send", json={"chat_id": "yok", "content": "x"}).status_code == 404
    # Doğrulama hatası boş sohbet bırakmamalı.
    assert client.get("/api/chats").json() == []


# --- sohbet yönetimi ---------------------------------------------------------------------


def test_chat_rename_pin_move_delete(client):
    chat_id = send(client, content="ilk")[0]["chat"]["id"]
    p = client.post("/api/projects", json={"name": "P"}).json()

    c = client.patch(f"/api/chats/{chat_id}", json={"title": "Yeni ad", "pinned": True, "project_id": p["id"]}).json()
    assert (c["title"], c["pinned"], c["project_id"], c["title_locked"]) == ("Yeni ad", True, p["id"], True)
    # Sadece sabitlemeyi değiştirmek projeyi bozmamalı.
    c = client.patch(f"/api/chats/{chat_id}", json={"pinned": False}).json()
    assert c["project_id"] == p["id"] and c["pinned"] is False
    assert client.patch(f"/api/chats/{chat_id}", json={"project_id": None}).json()["project_id"] is None
    assert client.patch(f"/api/chats/{chat_id}", json={"project_id": "yok"}).status_code == 404

    assert client.delete(f"/api/chats/{chat_id}").json() == {"ok": True}
    assert client.get(f"/api/chats/{chat_id}").status_code == 404


def test_deleting_project_moves_chats_to_other(client):
    p = client.post("/api/projects", json={"name": "P", "context": "c"}).json()
    chat_id = send(client, content="x", project_id=p["id"])[0]["chat"]["id"]
    assert client.patch(f"/api/projects/{p['id']}", json={"pinned": True}).json()["pinned"] is True
    client.delete(f"/api/projects/{p['id']}")
    assert client.get(f"/api/chats/{chat_id}").json()["project_id"] is None
    assert client.get("/api/projects").json() == []


def test_search_titles_and_messages_turkish(client):
    a = send(client, content="Ghost of Tsushima ışık sorunu")[0]["chat"]["id"]
    b = send(client, content="Unity sahne dosyaları")[0]["chat"]["id"]
    assert [c["id"] for c in client.get("/api/chats", params={"q": "TSUSHIMA"}).json()] == [a]
    assert [c["id"] for c in client.get("/api/chats", params={"q": "ışık"}).json()] == [a]
    assert [c["id"] for c in client.get("/api/chats", params={"q": "merhaba"}).json()] == [b, a]  # cevap metninde
    assert client.get("/api/chats", params={"q": "100%_"}).json() == []


def test_auto_title(client, ollama):
    chat_id = send(client, content="bana matrix filmini anlat lütfen")[0]["chat"]["id"]
    assert client.post(f"/api/chats/{chat_id}/title").json()["title"] == "Matrix Sohbeti"
    # Elle verilen başlık ezilmez.
    client.patch(f"/api/chats/{chat_id}", json={"title": "Benim başlığım"})
    ollama.title = "Başka"
    assert client.post(f"/api/chats/{chat_id}/title").json()["title"] == "Benim başlığım"


def test_clean_title():
    assert clean_title('<think>x</think>\nBaşlık: "Unity Silah Sistemi".') == "Unity Silah Sistemi"
    assert clean_title("**Repo erişim kontrolü**\nfazlası") == "Repo erişim kontrolü"


def test_build_messages_skips_empty_assistant():
    hist = [{"role": "user", "content": "a"}, {"role": "assistant", "content": "<think>x</think>"}, {"role": "user", "content": "b"}]
    msgs = build_messages(hist, None, None, True)
    assert msgs == [{"role": "user", "content": "a"}, {"role": "user", "content": "b"}]


# --- modeller ----------------------------------------------------------------------------


def test_models_list_with_catalog_info(client, ollama):
    r = client.get("/api/models").json()
    assert r["ok"] is True
    by = {m["name"]: m for m in r["models"]}
    assert by["qwen2.5:7b"]["parameter_size"] == "7.6B"
    assert "Türkçe" in by["qwen2.5:7b"]["desc"]
    assert by["phi4-mini:latest"]["tags"] == ["hafif", "mantik"]
    ollama.fail_connect = True
    assert client.get("/api/models").json()["ok"] is False


def test_recommend_uses_hardware(client, monkeypatch):
    hw = {"os": "Windows 11", "cpu": "x", "cores": 8, "threads": 16, "ram_gb": 32.0, "ram_free_gb": 20.0,
          "gpus": [{"name": "RTX 3060", "vendor": "NVIDIA", "vram_gb": 12.0}], "vram_gb": 12.0, "unified": False, "disk_free_gb": 100.0}
    monkeypatch.setattr(hardware, "scan", lambda: hw)
    r = client.get("/api/models/recommend").json()
    assert r["hardware"]["vram_gb"] == 12.0
    by = {m["name"]: m for m in r["models"]}
    assert by["qwen2.5:7b"]["fit"] == "gpu" and by["qwen2.5:7b"]["installed"] is True
    assert by["phi4-mini"]["installed"] is True  # Ollama ":latest" ekliyor
    assert by["qwen2.5:32b"]["fit"] == "partial"
    # 12 GB'a tamamen sığan en büyük kodcu (9 GB × 1.2 + 1 = 11.8 GB).
    assert "kod" in by["qwen2.5-coder:14b"]["best_for"]
    assert by["qwen3-coder:30b"]["best_for"] == []


def test_pull_and_delete(client, ollama):
    evs = events(client.post("/api/models/pull", json={"name": "gemma3:4b"}))
    assert [e["type"] for e in evs] == ["progress", "progress", "progress", "done"]
    assert evs[1]["completed"] == 50
    assert client.delete("/api/models/gemma3:4b").json() == {"ok": True}
    assert ollama.deleted == ["gemma3:4b"]


def test_assess_levels():
    gpu8 = {"vram_gb": 8, "ram_gb": 16}
    assert catalog.assess(4.7, gpu8) == "gpu"
    assert catalog.assess(9.0, gpu8) == "partial"
    assert catalog.assess(20.0, gpu8) == "no"
    cpu = {"vram_gb": 0, "ram_gb": 16}
    assert catalog.assess(4.7, cpu) == "cpu"
    mac = {"vram_gb": 11.2, "ram_gb": 16, "unified": True}
    assert catalog.assess(4.7, mac) == "gpu" and catalog.assess(9.0, mac) == "no"


def test_cpu_only_recommends_small_models():
    hw = {"vram_gb": 0, "ram_gb": 32}
    best = {c: m["name"] for m in catalog.recommend(hw, set()) for c in m["best_for"]}
    assert all(catalog.BY_NAME[n]["size_gb"] <= catalog.CPU_MAX_GB for n in best.values())
    assert best["kod"] == "qwen2.5-coder:7b"


def test_hardware_scan_runs_here():
    hw = hardware.scan()
    assert hw["ram_gb"] > 0 and hw["threads"] > 0
    assert isinstance(hw["gpus"], list)


def test_js_served_as_javascript(client):
    # ES modülleri yanlış MIME türüyle gelirse tarayıcı yüklemez (Windows tuzağı).
    assert client.get("/js/main.js").headers["content-type"].startswith("text/javascript")
