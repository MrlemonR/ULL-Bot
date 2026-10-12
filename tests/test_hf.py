import httpx
import pytest
from fastapi.testclient import TestClient

from app import hardware, hf
from app.main import create_app

URL = "https://huggingface.co/Richard944/Qwen3-4B-Gemini-TripleX-High-Reasoning-Thinking-Heretic-Uncensored-GGUF?not-for-all-audiences=true"
REPO = "Richard944/Qwen3-4B-Gemini-TripleX-High-Reasoning-Thinking-Heretic-Uncensored-GGUF"
GB = 1024**3


def test_parse_ref_variants():
    assert hf.parse_ref(URL) == {"repo": REPO, "quant": None, "file": None}
    assert hf.parse_ref("https://huggingface.co/u/r-GGUF/blob/main/sub/r-Q5_K_M.gguf") == {"repo": "u/r-GGUF", "quant": "Q5_K_M", "file": "sub/r-Q5_K_M.gguf"}
    assert hf.parse_ref("hf.co/u/r:q8_0") == {"repo": "u/r", "quant": "Q8_0", "file": None}
    assert hf.parse_ref("u/r")["repo"] == "u/r"
    for bad in ("", "https://github.com/u/r", "https://huggingface.co/u", "sadece-isim"):
        with pytest.raises(ValueError):
            hf.parse_ref(bad)


def test_gguf_files_groups_and_skips():
    sib = [
        {"rfilename": "README.md", "size": 10},
        {"rfilename": "m-Q4_K_M.gguf", "size": 2 * GB},
        {"rfilename": "m-Q8_0.gguf", "lfs": {"size": 4 * GB}},
        {"rfilename": "mmproj-m-f16.gguf", "size": 1 * GB},
        {"rfilename": "big/m-F16-00001-of-00002.gguf", "size": 4 * GB},
        {"rfilename": "big/m-F16-00002-of-00002.gguf", "size": 4 * GB},
    ]
    files = hf.gguf_files(sib)
    assert [f["quant"] for f in files] == ["Q8_0", "Q4_K_M", "F16"]  # kalite sırası; bilinmeyen sonda
    f16 = files[-1]
    assert f16["size"] == 8 * GB and f16["parts"] == 2 and f16["split"]


def test_recommend_by_hardware():
    files = lambda: hf.gguf_files([{"rfilename": f"m-{q}.gguf", "size": int(s * GB)} for q, s in
                                   (("Q8_0", 4.3), ("Q6_K", 3.3), ("Q4_K_M", 2.5))])
    assert hf.recommend(files(), {"vram_gb": 12, "ram_gb": 32}) == "Q8_0"  # hepsi sığar → en kaliteli
    assert hf.recommend(files(), {"vram_gb": 4.5, "ram_gb": 16}) == "Q4_K_M"  # 2.5×1.2+1=4.0 sığar
    assert hf.recommend(files(), {"vram_gb": 0, "ram_gb": 16}) == "Q4_K_M"  # GPU yok → CPU'da Q4_K_M


class FakeHF:
    def __init__(self):
        self.repos = {
            REPO: {"author": "Richard944", "downloads": 1234, "likes": 5, "gated": False,
                   "cardData": {"license": "apache-2.0", "base_model": "Qwen/Qwen3-4B"},
                   "siblings": [{"rfilename": "Q-Q4_K_M.gguf", "size": int(2.5 * GB)},
                                {"rfilename": "Q-Q8_0.gguf", "size": int(4.3 * GB)}]},
            "Qwen/Qwen3-4B": {"author": "Qwen", "siblings": [{"rfilename": "model.safetensors", "size": 8 * GB}]},
        }

    def __call__(self, request: httpx.Request):
        path = request.url.path
        if path.startswith("/api/models/"):
            repo = path[len("/api/models/"):]
            return httpx.Response(200, json=self.repos[repo]) if repo in self.repos else httpx.Response(404)
        if path == "/api/models":
            assert request.url.params["filter"] == "gguf"
            return httpx.Response(200, json=[{"id": "bartowski/Qwen_Qwen3-4B-GGUF", "downloads": 99}])
        return httpx.Response(404)


class FakeOllama:
    def __init__(self):
        self.calls = []

    def __call__(self, request: httpx.Request):
        self.calls.append((request.method, request.url.path, request.content.decode()))
        if request.url.path in ("/api/copy", "/api/delete"):
            return httpx.Response(200)
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": []})
        return httpx.Response(404)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(hardware, "scan", lambda: {"vram_gb": 8.0, "ram_gb": 32.0, "unified": False})
    ollama = FakeOllama()
    app = create_app(data_dir=tmp_path / "d", ollama_url="http://o.test", transport=httpx.MockTransport(ollama),
                     prefab_dir=tmp_path / "p", dataset_dir=tmp_path / "ds", hf_transport=httpx.MockTransport(FakeHF()))
    with TestClient(app) as c:
        yield c, ollama


def test_inspect_gguf_repo(client):
    c, _ = client
    r = c.get("/api/hf/inspect", params={"ref": URL}).json()
    assert r["repo"] == REPO and r["license"] == "apache-2.0" and r["base_model"] == "Qwen/Qwen3-4B"
    assert [f["quant"] for f in r["files"]] == ["Q8_0", "Q4_K_M"]
    assert r["files"][0]["fit"] == "gpu"  # 4.3×1.2+1 = 6.2 GB ≤ 8 GB
    assert r["recommended"] == r["selected"] == "Q8_0"
    assert r["suggested_name"] == "qwen3-4b-gemini-triplex-high-reasoning-thinking"
    # Bağlantıda belirli bir sürüm varsa o seçili gelir.
    r = c.get("/api/hf/inspect", params={"ref": f"hf.co/{REPO}:Q4_K_M"}).json()
    assert r["selected"] == "Q4_K_M" and r["recommended"] == "Q8_0"


def test_inspect_non_gguf_suggests_alternatives(client):
    c, _ = client
    r = c.get("/api/hf/inspect", params={"ref": "https://huggingface.co/Qwen/Qwen3-4B"}).json()
    assert r["files"] == [] and "GGUF dosyası yok" in r["error"]
    assert r["alternatives"] == [{"repo": "bartowski/Qwen_Qwen3-4B-GGUF", "downloads": 99}]


def test_inspect_errors(client):
    c, _ = client
    assert c.get("/api/hf/inspect", params={"ref": "https://huggingface.co/yok/yok"}).status_code == 404
    assert c.get("/api/hf/inspect", params={"ref": "https://github.com/a/b"}).status_code == 422


def test_copy_to_short_name(client):
    c, ollama = client
    src = f"hf.co/{REPO}:Q8_0"
    r = c.post("/api/models/copy", json={"source": src, "destination": "Qwen3 4B Heretic:Q8_0", "delete_source": True}).json()
    assert r == {"ok": True, "name": "qwen3-4b-heretic:q8_0"}
    paths = [(m, p) for m, p, _ in ollama.calls if p != "/api/tags"]
    assert paths == [("POST", "/api/copy"), ("DELETE", "/api/delete")]
    assert '"destination": "qwen3-4b-heretic:q8_0"' in ollama.calls[-2][2] or "qwen3-4b-heretic:q8_0" in ollama.calls[-2][2]
    assert hf.ollama_ref(REPO, "Q8_0") == src
