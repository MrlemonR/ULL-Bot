import json
import sys
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from app import finetune
from app.finetune import modelfile, ollama_model_name, parse_dataset
from app.main import create_app

# --- veri seti biçimleri -----------------------------------------------------------------


def roles(ex):
    return [m["role"] for m in ex["messages"]]


def test_parse_soru_cevap_json():
    r = parse_dataset(json.dumps([{"soru": "Adın ne?", "cevap": "ULL-Bot"}, {"Soru": "Kim?", "Cevap": "Ben"}]))
    assert r["format"] == "json" and len(r["examples"]) == 2 and r["errors"] == []
    assert r["examples"][0]["messages"] == [{"role": "user", "content": "Adın ne?"}, {"role": "assistant", "content": "ULL-Bot"}]


def test_parse_other_key_styles():
    rows = [
        {"question": "q1", "answer": "a1"},
        {"instruction": "Çevir", "input": "hello", "output": "merhaba"},
        {"prompt": "p", "completion": "c", "system": "Sen korsansın."},
        {"messages": [{"role": "user", "content": "selam"}, {"role": "assistant", "content": "ahoy"}]},
    ]
    r = parse_dataset(json.dumps({"data": rows}))
    assert len(r["examples"]) == 4
    assert r["examples"][1]["messages"][0]["content"] == "Çevir\n\nhello"
    assert roles(r["examples"][2]) == ["system", "user", "assistant"]


def test_parse_jsonl_with_bad_lines():
    text = '{"soru": "a", "cevap": "b"}\n{bozuk\n\n{"soru": "c"}\n{"soru": "d", "cevap": "e"}\n'
    r = parse_dataset(text)
    assert r["format"] == "jsonl" and len(r["examples"]) == 2
    assert r["error_count"] == 2
    assert r["errors"][0].startswith("satır 2") and "satır 4" in r["errors"][1]
    assert any("Sadece 2 örnek" in w for w in r["warnings"])


def test_parse_messages_must_end_with_assistant():
    r = parse_dataset(json.dumps([{"messages": [{"role": "user", "content": "x"}]}]))
    assert r["examples"] == [] and "assistant" in r["errors"][0]


def test_model_name_and_modelfile():
    assert ollama_model_name("Llama3.1 8B Sorularım!") == "llama3.1-8b-sorular-m"
    assert ollama_model_name("benim:V2") == "benim:v2"
    with pytest.raises(ValueError):
        ollama_model_name("!!!")
    mf = modelfile("llama3.1:8b", 'Sen "korsan"sın.')
    assert mf.splitlines()[:2] == ["FROM llama3.1:8b", "ADAPTER ./adapter"]
    assert 'SYSTEM """Sen "korsan"sın."""' in mf
    assert "SYSTEM" not in modelfile("mistral", "  ")


def test_torch_index_follows_driver():
    ti = finetune.torch_index
    assert ti(None, "Linux").endswith("/cpu")
    assert ti({"cuda_driver": "12.9"}, "Linux").endswith("/cu128")
    assert ti({"cuda_driver": "12.4"}, "Windows").endswith("/cu124")
    assert ti({"cuda_driver": "11.2"}, "Linux").endswith("/cpu")
    # CUDA 13 sürücü: Linux'ta PyPI'deki torch zaten CUDA'lı; Windows'ta PyPI CPU'lu → cu130.
    assert ti({"cuda_driver": "13.0"}, "Linux") is None
    assert ti({"cuda_driver": "13.0"}, "Windows").endswith("/cu130")
    # Kullanıcıdaki durum: kart var, CUDA sürümü okunamadı → CPU'ya DÜŞMEMELİ.
    assert ti({"cuda_driver": None, "driver": None}, "Linux").endswith("/cu126")
    assert ti(None, "Darwin") is None


def test_cuda_from_driver():
    assert finetune.cuda_from_driver("580.82.07") == "13.0"
    assert finetune.cuda_from_driver("572.16") == "12.8"
    assert finetune.cuda_from_driver("535.104.05") == "12.2"
    assert finetune.cuda_from_driver("470.1") is None
    assert finetune.cuda_from_driver(None) is None


def test_reinstall_removes_old_venv_first(tmp_path):
    venv = tmp_path / ".train-venv"
    venv.mkdir()
    steps = finetune.install_steps(venv, {"driver": "580.1", "cuda_driver": "13.0"})
    assert steps[0]["label"] == "eski ortam siliniyor"
    steps[0]["fn"]()
    assert not venv.exists()
    assert "--clear" not in steps[1]["cmd"]  # eski uv sürümleri bu bayrağı tanımıyor
    torch_step = next(st for st in steps if st["label"].startswith("PyTorch"))
    assert "torch" in torch_step["cmd"] and "torchvision" in torch_step["cmd"]
    assert any("unsloth" in st.get("cmd", []) for st in steps)


def test_repair_swaps_cpu_torch_for_cuda(tmp_path, monkeypatch):
    """PyTorch GPU'yu görene kadar CUDA paketleri sırayla denenir."""
    nvidia = {"driver": "570.1", "cuda_driver": "12.8"}
    monkeypatch.setattr(finetune, "nvidia_info", lambda: nvidia)
    tried = []
    monkeypatch.setattr(finetune, "torch_cmd", lambda venv, index, reinstall=False: (tried.append((index, reinstall)), [sys.executable, "-c", "print('kuruldu')"])[1])
    envs = iter([
        {"ready": True, "torch": "2.14.1+cpu", "cuda": False, "exists": True},
        {"ready": True, "torch": "2.9.0+cu128", "cuda": False, "exists": True},
        {"ready": True, "torch": "2.9.0+cu126", "cuda": True, "gpu": "RTX 4070", "vram_gb": 12.0, "exists": True},
    ])
    monkeypatch.setattr(finetune, "check_env", lambda venv: next(envs))
    t = finetune.Trainer(tmp_path, tmp_path / "data", ollama_url=lambda: "", on_model_ready=lambda n: None)
    job = t.start_install(repair_only=True)
    for _ in range(200):
        if job.state != "running":
            break
        time.sleep(0.02)
    assert job.state == "done", job.message
    assert "RTX 4070" in job.message
    assert [i.rsplit("/", 1)[-1] for i, _ in tried] == ["cu128", "cu126"]
    assert all(r for _, r in tried)  # zorla yeniden kurulum



# --- API: veri setleri, ortam, eğitim -------------------------------------------------------

FAKE_WORKER = r'''
import json, os, sys, time
cfg = json.load(open(sys.argv[1], encoding="utf-8"))
def emit(**e): print("@@ULL " + json.dumps(e), flush=True)
rows = [json.loads(l) for l in open(cfg["data"], encoding="utf-8")]
emit(stage="temel model indiriliyor", completed=1, of=2)
print("bir log satırı")
emit(stage="eğitiliyor", step=0, total=3)
for i in range(1, 4):
    time.sleep(float(os.environ.get("FAKE_STEP", "0.01")))
    emit(step=i, total=3, loss=2.0 / i)
os.makedirs(cfg["out"], exist_ok=True)
open(os.path.join(cfg["out"], "adapter_model.safetensors"), "wb").write(b"x")
json.dump({"rows": rows, "cfg": cfg}, open(os.path.join(cfg["out"], "seen.json"), "w", encoding="utf-8"))
emit(stage="eğitim bitti")
'''

FAKE_OLLAMA = r'''
import json, os, sys
log = os.environ["FAKE_OLLAMA_LOG"]
with open(log, "a", encoding="utf-8") as f:
    f.write(json.dumps({"args": sys.argv[1:], "cwd": os.getcwd(), "host": os.environ.get("OLLAMA_HOST"),
                        "modelfile": open("Modelfile", encoding="utf-8").read() if os.path.exists("Modelfile") else None}) + "\n")
print("success")
'''


class Tags:
    def __call__(self, request):
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "llama3.1:8b", "size": 1}]})
        return httpx.Response(404)


@pytest.fixture
def setup(tmp_path, monkeypatch):
    worker = tmp_path / "fake_worker.py"
    worker.write_text(FAKE_WORKER, "utf-8")
    cli = tmp_path / "fake_ollama.py"
    cli.write_text(FAKE_OLLAMA, "utf-8")
    monkeypatch.setattr(finetune, "WORKER", worker)
    monkeypatch.setenv("FAKE_OLLAMA_LOG", str(tmp_path / "ollama.log"))
    ds = tmp_path / "datasets"
    ds.mkdir()
    (ds / "sorular.json").write_text(json.dumps([{"soru": f"s{i}", "cevap": f"c{i}"} for i in range(30)]), "utf-8")
    app = create_app(
        data_dir=tmp_path / "data", ollama_url="http://ollama.test", transport=httpx.MockTransport(Tags()),
        prefab_dir=tmp_path / "prefab", dataset_dir=ds,
        train_python=sys.executable, ollama_cmd=[sys.executable, str(cli)],
    )
    with TestClient(app) as c:
        yield c, tmp_path


def wait_job(c, timeout=15):
    t0 = time.time()
    while time.time() - t0 < timeout:
        job = c.get("/api/train/status").json()["job"]
        if job and job["state"] != "running":
            return job
        time.sleep(0.05)
    raise AssertionError("iş bitmedi")


def test_datasets_list_preview_upload_delete(setup):
    c, tmp = setup
    items = c.get("/api/train/datasets").json()["items"]
    assert items == [{"file": "sorular.json", "valid": True, "count": 30, "error": "", "size": items[0]["size"]}]
    pv = c.get("/api/train/datasets/sorular.json").json()
    assert pv["count"] == 30 and pv["preview"][0] == {"soru": "s0", "cevap": "c0"}

    r = c.post("/api/train/datasets", json={"name": "Yeni Veri.jsonl", "content": '{"q": "a", "a": "b"}\n'})
    assert r.status_code == 201 and r.json()["file"] == "Yeni Veri.jsonl"
    assert c.post("/api/train/datasets", json={"name": "x.json", "content": "[1, 2]"}).status_code == 422
    assert c.get("/api/train/datasets/..%2Fdata").status_code in (400, 404)
    assert c.delete("/api/train/datasets/Yeni Veri.jsonl").json() == {"ok": True}
    assert not (tmp / "datasets" / "Yeni Veri.jsonl").exists()


def test_env_reports_bases(setup):
    c, _ = setup
    r = c.get("/api/train/env").json()
    by = {b["name"]: b for b in r["bases"]}
    assert by["llama3.1:8b"]["installed"] is True and by["mistral"]["installed"] is False
    assert by["llama3.1:8b"]["vram_4bit"] < by["llama3.1:8b"]["vram_full"]
    assert r["env"]["exists"] is True  # test için uygulamanın kendi Python'u veriliyor


def test_training_creates_ollama_model_and_switches(setup):
    c, tmp = setup
    body = {"base": "llama3.1:8b", "dataset": "sorular.json", "name": "Llama3.1 8B Sorular",
            "system": "Sen ULL-Bot'sun.", "epochs": 2, "lr": 0.0002, "rank": 16, "max_len": 1024}
    job = c.post("/api/train/start", json=body).json()
    assert job["state"] == "running" and job["meta"]["model"] == "llama3.1-8b-sorular"
    assert c.post("/api/train/start", json=body).status_code == 409  # aynı anda tek iş

    job = wait_job(c)
    assert job["state"] == "done", job
    assert job["result"] == {"model": "llama3.1-8b-sorular"}
    assert (job["step"], job["total"], job["loss"]) == (3, 3, round(2.0 / 3, 4))
    assert "bir log satırı" in job["log"]

    # Eğitim betiğine giden veri: sistem mesajı eklenmiş, 30 örnek.
    run_dir = next((tmp / "data" / "finetune").iterdir())
    seen = json.loads((run_dir / "adapter" / "seen.json").read_text("utf-8"))
    assert len(seen["rows"]) == 30
    assert seen["rows"][0]["messages"][0] == {"role": "system", "content": "Sen ULL-Bot'sun."}
    assert seen["cfg"]["hf"] == "unsloth/Meta-Llama-3.1-8B-Instruct" and seen["cfg"]["epochs"] == 2

    # Ollama: temel model zaten kurulu → pull yok, sadece create.
    calls = [json.loads(l) for l in (tmp / "ollama.log").read_text("utf-8").splitlines()]
    assert [x["args"] for x in calls] == [["create", "llama3.1-8b-sorular", "-f", "Modelfile"]]
    assert calls[0]["host"] == "http://ollama.test:11434"
    assert "FROM llama3.1:8b\nADAPTER ./adapter" in calls[0]["modelfile"]

    # ULL-Bot yeni modele geçti; geçmişte görünüyor.
    assert c.get("/api/settings").json()["model"] == "llama3.1-8b-sorular"
    hist = c.get("/api/train/status").json()["history"]
    assert hist[0]["state"] == "done" and hist[0]["meta"]["examples"] == 30


def test_training_pulls_missing_base(setup):
    c, tmp = setup
    c.post("/api/train/start", json={"base": "mistral", "dataset": "sorular.json", "name": "m"})
    assert wait_job(c)["state"] == "done"
    calls = [json.loads(l)["args"] for l in (tmp / "ollama.log").read_text("utf-8").splitlines()]
    assert calls == [["pull", "mistral"], ["create", "m", "-f", "Modelfile"]]


def test_real_worker_without_torch_explains(setup, monkeypatch):
    c, _ = setup
    monkeypatch.setattr(finetune, "WORKER", finetune.Path(finetune.__file__).with_name("train_worker.py"))
    c.post("/api/train/start", json={"base": "llama3.2:1b", "dataset": "sorular.json", "name": "x"})
    job = wait_job(c)
    assert job["state"] == "error"
    assert "PyTorch kurulu değil" in job["message"]


def test_cancel_training(setup, monkeypatch):
    c, _ = setup
    monkeypatch.setenv("FAKE_STEP", "5")
    c.post("/api/train/start", json={"base": "llama3.1:8b", "dataset": "sorular.json", "name": "x"})
    time.sleep(0.5)
    assert c.post("/api/train/cancel").json() == {"ok": True}
    assert wait_job(c)["state"] == "cancelled"


def test_start_validation(setup):
    c, _ = setup
    assert c.post("/api/train/start", json={"base": "qwen2.5:7b", "dataset": "sorular.json", "name": "x"}).status_code == 422
    assert c.post("/api/train/start", json={"base": "mistral", "dataset": "yok.json", "name": "x"}).status_code == 404
    assert c.post("/api/train/start", json={"base": "mistral", "dataset": "sorular.json", "name": "x", "rank": 7}).status_code == 422
