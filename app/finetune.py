"""İnce ayar (fine-tune): soru-cevap veri setiyle bir modele LoRA eğitimi.

Akış:
  1. Veri seti (JSON / JSONL) okunur, sohbet biçimine çevrilir.
  2. Ayrı bir Python ortamında (.train-venv: PyTorch + Unsloth / PEFT) eğitim
     betiği (train_worker.py) çalışır: temel modelin Hugging Face ağırlıklarını
     indirir, LoRA adaptörünü eğitir ve kaydeder.
  3. Adaptör Ollama'ya yeni bir model olarak eklenir:
         FROM llama3.1:8b
         ADAPTER ./adapter
     ve ULL-Bot o modele geçer.

Ollama'nın GGUF dosyaları doğrudan eğitilemiyor; bu yüzden eğitim orijinal
ağırlıklarla yapılıp sonuç adaptör olarak Ollama'daki aynı temel modelin üstüne
takılıyor. Ollama adaptörleri Llama ve Mistral mimarileri için destekliyor.
"""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Any, Callable

WORKER = Path(__file__).with_name("train_worker.py")
EVENT_PREFIX = "@@ULL "

# Ollama adı → Hugging Face ağırlıkları. unsloth/* kopyaları erişim izni istemiyor.
BASES: dict[str, dict] = {
    "llama3.2:1b": {"label": "Llama 3.2 1B", "hf": "unsloth/Llama-3.2-1B-Instruct",
                    "hf_4bit": "unsloth/Llama-3.2-1B-Instruct-bnb-4bit", "params_b": 1.2},
    "llama3.2:3b": {"label": "Llama 3.2 3B", "hf": "unsloth/Llama-3.2-3B-Instruct",
                    "hf_4bit": "unsloth/Llama-3.2-3B-Instruct-bnb-4bit", "params_b": 3.2},
    "llama3.1:8b": {"label": "Llama 3.1 8B", "hf": "unsloth/Meta-Llama-3.1-8B-Instruct",
                    "hf_4bit": "unsloth/Meta-Llama-3.1-8B-Instruct-bnb-4bit", "params_b": 8.0},
    "mistral": {"label": "Mistral 7B v0.3", "hf": "unsloth/mistral-7b-instruct-v0.3",
                "hf_4bit": "unsloth/mistral-7b-instruct-v0.3-bnb-4bit", "params_b": 7.2},
}
ALIASES = {"llama3.2": "llama3.2:3b", "llama3.2:latest": "llama3.2:3b", "llama3.1": "llama3.1:8b",
           "llama3.1:latest": "llama3.1:8b", "mistral:latest": "mistral", "mistral:7b": "mistral"}


def base_info(name: str) -> dict | None:
    key = ALIASES.get(name, name)
    return {"name": key, **BASES[key]} if key in BASES else None


def vram_need(params_b: float, four_bit: bool) -> float:
    """Eğitim için kabaca gereken VRAM (GB)."""
    return round(params_b * (0.75 if four_bit else 2.2) + 2.5, 1)


# --- veri seti ------------------------------------------------------------------------

USER_KEYS = ("soru", "question", "prompt", "instruction", "user", "girdi", "q", "input")
ANSWER_KEYS = ("cevap", "yanıt", "yanit", "answer", "response", "output", "completion", "assistant", "çıktı", "cikti", "a")
SYSTEM_KEYS = ("system", "sistem")


def _pick(obj: dict, keys: tuple) -> tuple[str | None, str | None]:
    lower = {str(k).lower(): k for k in obj}
    for k in keys:
        if k in lower and isinstance(obj[lower[k]], str) and obj[lower[k]].strip():
            return lower[k], obj[lower[k]].strip()
    return None, None


def normalize_example(obj: Any) -> dict:
    """Tek bir örneği {"messages": [...]} biçimine çevirir; olmazsa ValueError."""
    if not isinstance(obj, dict):
        raise ValueError("nesne değil")
    if isinstance(obj.get("messages"), list):
        msgs = []
        for m in obj["messages"]:
            if not isinstance(m, dict) or m.get("role") not in ("system", "user", "assistant") or not isinstance(m.get("content"), str):
                raise ValueError("messages içinde geçersiz mesaj (role: system/user/assistant, content: metin)")
            msgs.append({"role": m["role"], "content": m["content"]})
        if not msgs or msgs[-1]["role"] != "assistant":
            raise ValueError("messages bir assistant cevabıyla bitmeli")
        if not any(m["role"] == "user" for m in msgs):
            raise ValueError("messages içinde user mesajı yok")
        return {"messages": msgs}

    ukey, user = _pick(obj, USER_KEYS)
    akey, answer = _pick(obj, ANSWER_KEYS)
    if ukey == "instruction":
        _, extra = _pick({k: v for k, v in obj.items() if k != ukey}, ("input", "girdi"))
        if extra and extra != answer:
            user = f"{user}\n\n{extra}"
    if not user or not answer:
        keys = ", ".join(map(str, obj.keys())) or "boş"
        raise ValueError(f"soru/cevap alanları bulunamadı (anahtarlar: {keys})")
    msgs = []
    _, system = _pick(obj, SYSTEM_KEYS)
    if system:
        msgs.append({"role": "system", "content": system})
    msgs += [{"role": "user", "content": user}, {"role": "assistant", "content": answer}]
    return {"messages": msgs}


def parse_dataset(text: str) -> dict:
    """JSON dizisi, {"data": [...]} ya da JSONL kabul eder."""
    text = text.lstrip("﻿").strip()
    items: list[tuple[int, Any]] = []
    fmt = "json"
    try:
        data = json.loads(text) if text else []
        if isinstance(data, dict):
            data = next((v for v in data.values() if isinstance(v, list)), [data])
        if not isinstance(data, list):
            raise ValueError
        items = [(i + 1, x) for i, x in enumerate(data)]
    except ValueError:
        fmt = "jsonl"
        for n, line in enumerate(text.splitlines(), 1):
            if line.strip():
                try:
                    items.append((n, json.loads(line)))
                except ValueError as e:
                    items.append((n, ValueError(f"JSON hatası: {e.msg}")))

    examples, errors = [], []
    for n, obj in items:
        try:
            if isinstance(obj, ValueError):
                raise obj
            examples.append(normalize_example(obj))
        except ValueError as e:
            errors.append(f"{'satır' if fmt == 'jsonl' else 'örnek'} {n}: {e}")

    warnings = []
    if examples and len(examples) < 20:
        warnings.append(f"Sadece {len(examples)} örnek var — model bir şey öğrenir ama genelleme zayıf olur; 50-200+ örnek önerilir.")
    if errors:
        warnings.append(f"{len(errors)} örnek okunamadı ve atlanacak.")
    pairs = {(e["messages"][-2]["content"], e["messages"][-1]["content"]) for e in examples}
    if len(pairs) < len(examples):
        warnings.append(f"{len(examples) - len(pairs)} tekrar eden örnek var.")
    return {"format": fmt, "examples": examples, "errors": errors[:20], "error_count": len(errors), "warnings": warnings}


def dataset_summary(path: Path) -> dict:
    try:
        parsed = parse_dataset(path.read_text("utf-8"))
    except (OSError, UnicodeDecodeError) as e:
        return {"file": path.name, "valid": False, "count": 0, "error": f"okunamadı: {e}"}
    count = len(parsed["examples"])
    preview = [
        {"soru": e["messages"][-2]["content"][:300], "cevap": e["messages"][-1]["content"][:300]}
        for e in parsed["examples"][:3]
    ]
    return {
        "file": path.name,
        "valid": count > 0,
        "count": count,
        "format": parsed["format"],
        "error": "" if count else (parsed["errors"][0] if parsed["errors"] else "örnek yok"),
        "errors": parsed["errors"],
        "error_count": parsed["error_count"],
        "warnings": parsed["warnings"],
        "preview": preview,
        "size": path.stat().st_size,
    }


def safe_name(name: str, suffix: str = "") -> str:
    base = re.sub(r"[^\w\- .]", "_", name.strip(), flags=re.UNICODE).strip(" .")[:60] or "veri"
    return base + suffix


def ollama_model_name(raw: str) -> str:
    """Ollama'nın kabul ettiği ada çevir: küçük harf, rakam, - _ . ve tek ':' etiketi."""
    raw = raw.strip().lower()
    name, _, tag = raw.partition(":")
    clean = lambda s: re.sub(r"[^a-z0-9._-]+", "-", s).strip("-._")
    name, tag = clean(name), clean(tag)
    if not name:
        raise ValueError("Model adı boş olamaz")
    return f"{name}:{tag}" if tag else name


def modelfile(base: str, system: str = "") -> str:
    lines = [f"FROM {base}", "ADAPTER ./adapter"]
    if system.strip():
        lines.append('SYSTEM """' + system.strip().replace('"""', '\\"\\"\\"') + '"""')
    return "\n".join(lines) + "\n"


# --- eğitim ortamı ----------------------------------------------------------------------

def venv_python(venv: Path) -> Path:
    return venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


PROBE = r"""
import json, sys, importlib
out = {"python": sys.version.split()[0]}
for mod in ("torch", "transformers", "peft", "accelerate", "bitsandbytes", "huggingface_hub"):
    try:
        out[mod] = getattr(importlib.import_module(mod), "__version__", "?")
    except Exception:
        out[mod] = None
out["cuda"] = False
try:
    import torch
    out["cuda"] = bool(torch.cuda.is_available())
    if out["cuda"]:
        p = torch.cuda.get_device_properties(0)
        out["gpu"] = p.name
        out["vram_gb"] = round(p.total_memory / 1024**3, 1)
        out["bf16"] = bool(torch.cuda.is_bf16_supported())
except Exception as e:
    out["torch_error"] = str(e)[:200]
out["unsloth"] = None
if out["cuda"]:
    try:
        import unsloth
        out["unsloth"] = getattr(unsloth, "__version__", "?")
    except Exception as e:
        out["unsloth_error"] = str(e)[:200]
print("@@ULL " + json.dumps(out))
"""


def _no_window() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def check_env(venv: Path) -> dict:
    py = venv_python(venv)
    result: dict[str, Any] = {"venv": str(venv), "exists": py.exists(), "nvidia": nvidia_info()}
    if not py.exists():
        result["ready"] = False
        return result
    try:
        out = subprocess.run(
            [str(py), "-c", PROBE], capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=180, creationflags=_no_window(),
        )
        line = next((ln for ln in out.stdout.splitlines() if ln.startswith(EVENT_PREFIX)), None)
        result.update(json.loads(line[len(EVENT_PREFIX):]) if line else {"probe_error": (out.stderr or out.stdout)[-500:]})
    except (OSError, subprocess.SubprocessError, ValueError) as e:
        result["probe_error"] = str(e)
    result["ready"] = bool(result.get("torch") and result.get("transformers") and result.get("peft"))
    return result


def nvidia_info() -> dict | None:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    try:
        out = subprocess.run([exe], capture_output=True, text=True, timeout=10, creationflags=_no_window()).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r"CUDA Version:\s*(\d+)\.(\d+)", out)
    return {"cuda_driver": f"{m.group(1)}.{m.group(2)}" if m else None}


def torch_index(nvidia: dict | None) -> str:
    """Sürücünün desteklediği CUDA sürümüne uygun PyTorch paketi."""
    if not nvidia or not nvidia.get("cuda_driver"):
        return "https://download.pytorch.org/whl/cpu"
    major, minor = map(int, nvidia["cuda_driver"].split("."))
    v = major * 10 + minor
    for need, tag in ((128, "cu128"), (126, "cu126"), (124, "cu124"), (121, "cu121"), (118, "cu118")):
        if v >= need:
            return f"https://download.pytorch.org/whl/{tag}"
    return "https://download.pytorch.org/whl/cpu"


def install_steps(venv: Path) -> list[dict]:
    nvidia = nvidia_info()
    py = venv_python(venv)
    uv = shutil.which("uv")
    if uv:
        steps = [{"label": "Python ortamı oluşturuluyor", "cmd": [uv, "venv", str(venv), "--python", "3.11", "--seed"]}]
        pip = [uv, "pip", "install", "--python", str(py)]
    else:
        steps = [{"label": "Python ortamı oluşturuluyor", "cmd": [sys.executable, "-m", "venv", str(venv)]}]
        pip = [str(py), "-m", "pip", "install"]
    steps.append({"label": "PyTorch indiriliyor (büyük, birkaç GB)", "cmd": pip + ["torch", "--index-url", torch_index(nvidia)]})
    if nvidia:
        # Unsloth NVIDIA'da 2 kat hızlı ve yarı bellek; kurulamazsa PEFT ile devam edilir.
        steps.append({"label": "Unsloth kuruluyor", "cmd": pip + ["unsloth"], "optional": True})
    steps.append({
        "label": "Eğitim kütüphaneleri kuruluyor",
        "cmd": pip + ["transformers", "peft", "accelerate", "huggingface_hub", "safetensors", "sentencepiece", "protobuf"]
        + (["bitsandbytes"] if nvidia else []),
    })
    return steps


# --- arka plan işi ------------------------------------------------------------------------

class Job:
    def __init__(self, kind: str, title: str, job_dir: Path | None = None, meta: dict | None = None):
        self.id = uuid.uuid4().hex[:10]
        self.kind = kind
        self.title = title
        self.dir = job_dir
        self.meta = meta or {}
        self.state = "running"   # running | done | error | cancelled
        self.stage = "başlıyor"
        self.message = ""
        self.step = 0
        self.total = 0
        self.loss: float | None = None
        self.progress: float | None = None  # 0-1 (indirme vb.)
        self.started = time.time()
        self.finished: float | None = None
        self.train_started: float | None = None
        self.result: dict = {}
        self.log: deque[str] = deque(maxlen=400)
        self.proc: subprocess.Popen | None = None
        self.cancelled = False

    def snapshot(self) -> dict:
        eta = None
        if self.train_started and self.step and self.total and self.state == "running":
            per = (time.time() - self.train_started) / self.step
            eta = round(per * (self.total - self.step))
        return {
            "id": self.id, "kind": self.kind, "title": self.title, "state": self.state, "stage": self.stage,
            "message": self.message, "step": self.step, "total": self.total, "loss": self.loss,
            "progress": self.progress, "eta": eta, "started": self.started, "finished": self.finished,
            "elapsed": round((self.finished or time.time()) - self.started), "result": self.result,
            "meta": self.meta, "log": list(self.log)[-60:],
        }


class Trainer:
    """Aynı anda tek iş (kurulum ya da eğitim) çalıştırır."""

    def __init__(self, root: Path, data_dir: Path, ollama_url: Callable[[], str], on_model_ready: Callable[[str], None],
                 ollama_cmd: list[str] | None = None, train_python: Path | None = None,
                 dataset_dir: Path | None = None):
        self.venv = Path(os.environ.get("ULL_TRAIN_VENV") or root / ".train-venv")
        self.datasets = Path(dataset_dir or os.environ.get("ULL_DATASET_DIR") or root / "datasets").resolve()
        self.jobs_dir = data_dir / "finetune"
        self.ollama_url = ollama_url
        self.on_model_ready = on_model_ready
        self.ollama_cmd = ollama_cmd
        self.train_python = train_python
        self.job: Job | None = None
        self.lock = threading.Lock()
        self._env_cache: dict | None = None

    # --- ortam ---
    def python(self) -> Path:
        return self.train_python or venv_python(self.venv)

    def env(self, refresh: bool = False) -> dict:
        if refresh or self._env_cache is None:
            if self.train_python:
                env = check_env(self.train_python.parent.parent)
                env["exists"] = True
            else:
                env = check_env(self.venv)
            self._env_cache = env
        return self._env_cache

    def busy(self) -> bool:
        return self.job is not None and self.job.state == "running"

    def start_install(self) -> Job:
        with self.lock:
            if self.busy():
                raise RuntimeError("Başka bir iş çalışıyor")
            job = Job("install", "Eğitim ortamı kurulumu")
            self.job = job
        steps = install_steps(self.venv)
        threading.Thread(target=self._run_install, args=(job, steps), daemon=True).start()
        return job

    def _run_install(self, job: Job, steps: list[dict]) -> None:
        try:
            for i, step in enumerate(steps, 1):
                job.stage = f"{i}/{len(steps)} · {step['label']}"
                job.progress = (i - 1) / len(steps)
                code = self._run(job, step["cmd"])
                if job.cancelled:
                    return self._finish(job, "cancelled", "İptal edildi")
                if code != 0 and not step.get("optional"):
                    return self._finish(job, "error", f"Komut başarısız ({code}): {' '.join(step['cmd'][:4])} …")
                if code != 0:
                    job.log.append(f"(isteğe bağlı adım atlandı: {step['label']})")
            job.progress = 1
            self._env_cache = None
            env = self.env(refresh=True)
            msg = "Ortam hazır" + (f" · GPU: {env.get('gpu')}" if env.get("cuda") else " · GPU kullanılamıyor (CPU)")
            self._finish(job, "done" if env.get("ready") else "error", msg if env.get("ready") else "Kurulum bitti ama kütüphaneler yüklenemedi")
        except Exception as e:  # beklenmeyen her şey arayüzde görünsün
            self._finish(job, "error", f"Hata: {e}")

    # --- eğitim ---
    def start_training(self, *, base: str, dataset: str, name: str, system: str, epochs: int,
                       lr: float, rank: int, max_len: int, installed: set[str]) -> Job:
        info = base_info(base)
        if info is None:
            raise ValueError("Bu temel model desteklenmiyor")
        path = self.dataset_path(dataset)
        parsed = parse_dataset(path.read_text("utf-8"))
        examples = parsed["examples"]
        if not examples:
            raise ValueError("Veri setinde kullanılabilir örnek yok")
        model_name = ollama_model_name(name)
        if system.strip():
            examples = [
                e if e["messages"][0]["role"] == "system"
                else {"messages": [{"role": "system", "content": system.strip()}, *e["messages"]]}
                for e in examples
            ]
        with self.lock:
            if self.busy():
                raise RuntimeError("Başka bir iş çalışıyor")
            job_dir = self.jobs_dir / time.strftime("%Y%m%d-%H%M%S")
            job_dir.mkdir(parents=True, exist_ok=True)
            job = Job("train", f"{info['name']} → {model_name}", job_dir, meta={
                "base": info["name"], "dataset": dataset, "model": model_name, "examples": len(examples),
                "epochs": epochs, "lr": lr, "rank": rank, "max_len": max_len,
            })
            self.job = job
        with open(job_dir / "data.jsonl", "w", encoding="utf-8") as f:
            for e in examples:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")
        config = {
            "hf": info["hf"], "hf_4bit": info["hf_4bit"], "params_b": info["params_b"],
            "data": str(job_dir / "data.jsonl"), "out": str(job_dir / "adapter"),
            "epochs": epochs, "lr": lr, "rank": rank, "max_len": max_len,
        }
        (job_dir / "config.json").write_text(json.dumps(config, ensure_ascii=False, indent=2), "utf-8")
        (job_dir / "Modelfile").write_text(modelfile(info["name"], system), "utf-8")
        need_pull = not ({info["name"], f"{info['name']}:latest"} & installed)
        threading.Thread(target=self._run_training, args=(job, need_pull), daemon=True).start()
        return job

    def _run_training(self, job: Job, need_pull: bool) -> None:
        try:
            job.stage = "eğitim betiği başlıyor"
            worker = os.environ.get("ULL_TRAIN_WORKER") or str(WORKER)  # geliştirme/test için değiştirilebilir
            code = self._run(job, [str(self.python()), "-u", worker, str(job.dir / "config.json")])
            if job.cancelled:
                return self._finish(job, "cancelled", "İptal edildi")
            if code != 0 or not (job.dir / "adapter").exists():
                last = next((ln for ln in reversed(job.log) if ln.strip()), "")
                return self._finish(job, "error", job.message or f"Eğitim başarısız (çıkış kodu {code}). {last[:200]}")

            ollama = self.ollama_cmd or ([shutil.which("ollama")] if shutil.which("ollama") else None)
            if not ollama:
                return self._finish(job, "error", "Adaptör hazır ama `ollama` komutu bulunamadı. "
                                    f"Elle ekle: {job.dir} klasöründe `ollama create {job.meta['model']} -f Modelfile`")
            env = {**os.environ, "OLLAMA_HOST": self.ollama_url()}
            if need_pull:
                job.stage = f"temel model Ollama'ya indiriliyor ({job.meta['base']})"
                if self._run(job, [*ollama, "pull", job.meta["base"]], env=env) != 0:
                    return self._finish(job, "error", f"Temel model indirilemedi: {job.meta['base']}")
            job.stage = "Ollama'ya yeni model olarak ekleniyor"
            job.progress = None
            code = self._run(job, [*ollama, "create", job.meta["model"], "-f", "Modelfile"], env=env, cwd=job.dir)
            if code != 0:
                return self._finish(job, "error", "`ollama create` başarısız — günlüğe bak")
            self.on_model_ready(job.meta["model"])
            job.result = {"model": job.meta["model"]}
            self._finish(job, "done", f"{job.meta['model']} hazır ve seçildi")
        except Exception as e:
            self._finish(job, "error", f"Hata: {e}")

    # --- ortak ---
    def _run(self, job: Job, cmd: list[str], env: dict | None = None, cwd: Path | None = None) -> int:
        job.log.append("$ " + " ".join(cmd))
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
            env={**(env or os.environ), "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"},
            cwd=str(cwd) if cwd else None, creationflags=_no_window(),
        )
        job.proc = proc
        assert proc.stdout is not None
        for raw in proc.stdout:
            # İlerleme çubukları satır sonu yerine \r kullanıyor; son parçayı al.
            line = raw.rstrip("\n").split("\r")[-1].rstrip()
            if line.startswith(EVENT_PREFIX):
                try:
                    self._event(job, json.loads(line[len(EVENT_PREFIX):]))
                except ValueError:
                    pass
            elif line:
                job.log.append(line[:500])
        code = proc.wait()
        job.proc = None
        return code

    def _event(self, job: Job, ev: dict) -> None:
        if ev.get("stage"):
            job.stage = ev["stage"]
        if "message" in ev:
            job.message = ev["message"]
            if ev["message"]:
                job.log.append("» " + ev["message"])
        if ev.get("total"):
            job.total = int(ev["total"])
        if ev.get("step") is not None:
            if job.train_started is None:
                job.train_started = time.time() - 1e-3
            job.step = int(ev["step"])
            job.progress = job.step / job.total if job.total else None
        if ev.get("loss") is not None:
            job.loss = round(float(ev["loss"]), 4)
        if ev.get("completed") is not None and ev.get("of"):
            job.progress = ev["completed"] / ev["of"]
        if ev.get("error"):
            job.message = ev["error"]

    def _finish(self, job: Job, state: str, message: str) -> None:
        job.state = state
        job.stage = {"done": "tamamlandı", "error": "hata", "cancelled": "iptal edildi"}.get(state, state)
        job.message = message
        job.finished = time.time()
        job.log.append(("✓ " if state == "done" else "✖ ") + message)
        if job.dir:
            snap = job.snapshot()
            snap["log"] = list(job.log)
            try:
                (job.dir / "status.json").write_text(json.dumps(snap, ensure_ascii=False, indent=2), "utf-8")
            except OSError:
                pass

    def cancel(self) -> bool:
        job = self.job
        if not job or job.state != "running":
            return False
        job.cancelled = True
        proc = job.proc
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                proc.kill()
        return True

    def status(self) -> dict | None:
        return self.job.snapshot() if self.job else None

    def history(self) -> list[dict]:
        out = []
        if self.jobs_dir.is_dir():
            for d in sorted(self.jobs_dir.iterdir(), reverse=True):
                f = d / "status.json"
                if f.is_file():
                    try:
                        s = json.loads(f.read_text("utf-8"))
                        out.append({k: s.get(k) for k in ("title", "state", "message", "meta", "started", "elapsed")})
                    except (OSError, ValueError):
                        continue
        return out[:30]

    # --- veri setleri ---
    def dataset_path(self, file: str) -> Path:
        if file != Path(file).name or file.startswith(".") or not file.lower().endswith((".json", ".jsonl")):
            raise ValueError("Geçersiz dosya adı")
        path = self.datasets / file
        if not path.is_file():
            raise FileNotFoundError(file)
        return path

    def list_datasets(self) -> list[dict]:
        if not self.datasets.is_dir():
            return []
        files = [f for f in self.datasets.iterdir() if f.suffix.lower() in (".json", ".jsonl") and f.is_file()]
        out = []
        for f in sorted(files, key=lambda p: p.stat().st_mtime, reverse=True):
            s = dataset_summary(f)
            out.append({k: s[k] for k in ("file", "valid", "count", "error", "size")})
        return out

    def save_dataset(self, name: str, content: str) -> str:
        parsed = parse_dataset(content)
        if not parsed["examples"]:
            first = parsed["errors"][0] if parsed["errors"] else "örnek yok"
            raise ValueError(f"Dosyada kullanılabilir soru-cevap yok ({first})")
        stem = Path(name).stem
        ext = ".jsonl" if Path(name).suffix.lower() == ".jsonl" else ".json"
        self.datasets.mkdir(parents=True, exist_ok=True)
        path = self.datasets / safe_name(stem, ext)
        path.write_text(content, "utf-8")
        return path.name


def estimate_steps(n_examples: int, epochs: int, batch: int = 2, accum: int = 4) -> int:
    return max(1, math.ceil(n_examples / (batch * accum))) * epochs
