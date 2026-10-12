"""ULL-Bot sunucusu: arayüzü sunar, sohbetleri saklar ve lokal Ollama'ya aktarır.

Ortam değişkenleri (hepsi isteğe bağlı):
    OLLAMA_URL      Ollama adresi (varsayılan http://127.0.0.1:11434)
    ULL_DATA_DIR    verilerin tutulduğu klasör (varsayılan ./data)
    ULL_HOST        dinlenecek adres (varsayılan 127.0.0.1)
    ULL_PORT        port (varsayılan 8765)
    ULL_NO_BROWSER  "1" ise tarayıcı otomatik açılmaz
"""

from __future__ import annotations

import json
import mimetypes
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import __version__, catalog, coder, finetune, hardware, hf
from app.db import DB

ROOT_DIR = Path(__file__).resolve().parent.parent
WEB_DIR = ROOT_DIR / "web"

# Windows'ta MIME türleri kayıt defterinden okunuyor ve bazı makinelerde .js
# "text/plain" kayıtlı. Tarayıcı o zaman ES modüllerini yüklemeyi reddeder
# ve arayüz boş kalır; doğru türleri açıkça veriyoruz.
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("text/css", ".css")

DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"


class NoCacheStaticFiles(StaticFiles):
    """Tarayıcı arayüzü her açılışta sunucuya sorsun (değişmediyse 304 döner).

    Önbellek başlığı olmayınca tarayıcı eski index.html'i ve JS'i tahmini bir
    süre önbellekten açıyor; güncellemeden sonra "hâlâ eski sürüm" görünüyordu.
    """

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


def normalize_url(raw: str) -> str:
    """'0.0.0.0', '127.0.0.1:11435', 'http://pc:11434/' gibi yazımları tam adrese çevir."""
    raw = (raw or "").strip().rstrip("/")
    if not raw:
        return ""
    if "://" not in raw:
        raw = "http://" + raw
    scheme, rest = raw.split("://", 1)
    host, _, port = rest.partition(":")
    if host in ("", "0.0.0.0", "[::]", "::"):
        host = "127.0.0.1"  # sunucunun "her yerden dinle" adresi; bağlanmak için yerel
    port = port or "11434"
    return f"{scheme}://{host}:{port}"


def resolve_ollama_url(setting: str = "") -> str:
    """Ayarlardaki adres > OLLAMA_URL > OLLAMA_HOST (Ollama'nın kendi değişkeni) > varsayılan."""
    for candidate in (setting, os.environ.get("OLLAMA_URL", ""), os.environ.get("OLLAMA_HOST", "")):
        url = normalize_url(candidate)
        if url:
            return url
    return DEFAULT_OLLAMA_URL


def describe_ollama_error(err: Exception, url: str) -> str:
    if isinstance(err, httpx.ConnectError):
        return (
            f"{url} adresine bağlanılamadı (bağlantı reddedildi). Ollama çalışmıyor olabilir: "
            "Windows'ta Ollama uygulamasını aç ya da terminalde `ollama serve` çalıştır."
        )
    if isinstance(err, httpx.TimeoutException):
        return f"{url} cevap vermedi (zaman aşımı). Ollama donmuş ya da adres yanlış olabilir."
    if isinstance(err, httpx.HTTPStatusError):
        return f"{url} beklenmeyen bir cevap verdi (HTTP {err.response.status_code}). Bu adreste Ollama mı çalışıyor?"
    if isinstance(err, ValueError):
        return f"{url} adresindeki sunucu Ollama gibi görünmüyor (anlaşılmayan cevap)."
    return f"{url}: {err!s}"


# --- istek şemaları ----------------------------------------------------------------


class Appearance(BaseModel):
    rain_enabled: bool = True
    rain_speed: float = Field(1.0, ge=0.1, le=4)
    rain_density: float = Field(1.0, ge=0.1, le=1)
    rain_opacity: float = Field(1.0, ge=0.05, le=1)
    rain_red_ratio: float = Field(0.06, ge=0, le=1)
    rain_size: int = Field(18, ge=10, le=36)
    font_size: int = Field(14, ge=11, le=22)
    font_bold: bool = False
    font_family: Literal["sans", "mono"] = "sans"
    accent: str = Field("#ff2a3d", pattern=r"^#[0-9a-fA-F]{6}$")
    panel_opacity: float = Field(0.88, ge=0.3, le=1)
    chat_width: int = Field(860, ge=600, le=1600)
    spinner_words: bool = True


class SettingsPatch(BaseModel):
    model: str | None = Field(None, min_length=1, max_length=200)
    # Boş metin: ayarı sıfırla (ortam değişkeni / varsayılan adres kullanılır).
    ollama_url: str | None = Field(None, max_length=300)
    code_model: str | None = Field(None, max_length=200)
    code_num_ctx: int | None = Field(None, ge=2048, le=262144)
    temperature: float | None = Field(None, ge=0, le=2)
    num_ctx: int | None = Field(None, ge=512, le=262144)
    persona_reminder: bool | None = None
    auto_title: bool | None = None
    appearance: Appearance | None = None


class PersonaIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    prompt: str = Field(default="", max_length=20000)


class ActiveIn(BaseModel):
    persona_id: str | None


class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    context: str = Field(default="", max_length=100_000)


class ProjectPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=80)
    context: str | None = Field(None, max_length=100_000)
    pinned: bool | None = None


class ChatPatch(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=120)
    project_id: str | None = None
    pinned: bool | None = None


class SendIn(BaseModel):
    chat_id: str | None = None
    # Yalnızca yeni sohbet açılırken kullanılır.
    project_id: str | None = None
    content: str | None = Field(None, max_length=100_000)
    # Boşsa ayarlardaki aktif kişilik.
    persona_id: str | None = None
    # İçerik yoksa: sondaki asistan cevabını silip son kullanıcı mesajını yeniden cevapla.
    regenerate: bool = False


class OllamaTestIn(BaseModel):
    url: str = Field(default="", max_length=300)


class PrefabSaveIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    overwrite: bool = False


class PrefabFileIn(BaseModel):
    file: str = Field(min_length=1, max_length=200)


class TrainStartIn(BaseModel):
    base: str = Field(min_length=1, max_length=100)
    dataset: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=80)
    system: str = Field(default="", max_length=20000)
    epochs: int = Field(3, ge=1, le=20)
    lr: float = Field(2e-4, ge=1e-6, le=1e-2)
    rank: Literal[4, 8, 16, 32, 64] = 16
    max_len: int = Field(1024, ge=128, le=8192)


class DatasetUploadIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=50_000_000)


class CodeSessionIn(BaseModel):
    cwd: str = Field(min_length=1, max_length=2000)


class CodeSessionPatch(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=120)
    mode: Literal["ask", "edits", "all"] | None = None


class CodeSendIn(BaseModel):
    content: str = Field(min_length=1, max_length=100_000)


class PermissionIn(BaseModel):
    id: str = Field(min_length=1, max_length=64)
    decision: Literal["allow", "deny", "always"]


class ModelCopyIn(BaseModel):
    source: str = Field(min_length=1, max_length=300)
    destination: str = Field(min_length=1, max_length=120)
    delete_source: bool = False


class PullIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)


# --- ayar dosyaları (prefab) ------------------------------------------------------------

PREFAB_KEYS = ("model", "temperature", "num_ctx", "persona_reminder", "auto_title", "appearance")


def prefab_filename(name: str) -> str:
    """Kullanıcının verdiği addan güvenli bir dosya adı (Türkçe harfler kalır)."""
    base = re.sub(r"[^\w\- .]", "_", name.strip(), flags=re.UNICODE).strip(" .")[:60]
    return (base or "ayarlar") + ".json"


def prefab_path(folder: Path, file: str) -> Path:
    if file != Path(file).name or not file.lower().endswith(".json") or file.startswith("."):
        raise HTTPException(400, "Geçersiz dosya adı")
    path = folder / file
    if not path.is_file():
        raise HTTPException(404, f"Dosya bulunamadı: {file}")
    return path


def summarize_prefab(path: Path) -> dict:
    saved = time.strftime("%Y-%m-%d %H:%M", time.localtime(path.stat().st_mtime))
    try:
        data = json.loads(path.read_text("utf-8"))
        if not isinstance(data, dict):
            raise ValueError
        settings_ = data.get("settings") if isinstance(data.get("settings"), dict) else {}
        personas = [p for p in data.get("personas") or [] if isinstance(p, dict) and p.get("name")]
    except (OSError, ValueError):
        return {"file": path.name, "name": path.stem, "saved_at": saved, "valid": False,
                "error": "Okunamadı (geçerli bir JSON değil)"}
    active = next((p["name"] for p in personas if p.get("id") == data.get("active_persona_id")), None)
    return {
        "file": path.name,
        "name": str(data.get("name") or path.stem),
        "saved_at": str(data.get("saved_at") or saved),
        "model": str(settings_.get("model") or ""),
        "personas": [str(p["name"]) for p in personas],
        "active_persona": active,
        "has_appearance": isinstance(settings_.get("appearance"), dict),
        "valid": True,
    }


def open_folder(path: Path) -> None:
    if sys.platform.startswith("win"):
        os.startfile(str(path))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# --- prompt kurulumu ------------------------------------------------------------------

THINK_RE = re.compile(r"<think>.*?(</think>|$)\s*", re.S)


def strip_think(text: str) -> str:
    return THINK_RE.sub("", text).strip()


def build_system_prompt(persona: dict | None, project: dict | None) -> str:
    parts = []
    if persona and persona["prompt"].strip():
        parts.append(persona["prompt"].strip())
        parts.append(
            "Bu kişiliği konuşmanın TAMAMI boyunca koru: her cevabında aynı karakter ve "
            "üslupla konuş, kişilikten çıkma. Konuşmada daha önce söylenenleri hatırla ve "
            "onlarla tutarlı şekilde devam et."
        )
    if project and project["context"].strip():
        parts.append(
            f"## Proje: {project['name']}\n"
            "Aşağıdaki proje bağlamını her cevabında dikkate al ve ona göre cevap ver.\n\n"
            f"{project['context'].strip()}"
        )
    return "\n\n".join(parts)


def build_messages(history: list[dict], persona: dict | None, project: dict | None, reminder: bool) -> list[dict]:
    msgs: list[dict] = []
    system = build_system_prompt(persona, project)
    if system:
        msgs.append({"role": "system", "content": system})
    for m in history:
        if m["role"] == "assistant":
            content = strip_think(m["content"])
            if content:
                msgs.append({"role": "assistant", "content": content})
        elif m["role"] == "user":
            msgs.append({"role": "user", "content": m["content"]})

    # Sistem mesajı en başta kalıyor ve küçük modeller birkaç turdan sonra onu
    # "unutuyor". Son kullanıcı mesajının hemen önüne kısa bir hatırlatma
    # koymak kişiliği ve bağlamı her turda taze tutuyor. Veritabanına yazılmıyor.
    if reminder and msgs and msgs[-1]["role"] == "user" and (persona or project):
        bits = []
        if persona and persona["prompt"].strip():
            bits.append(f'"{persona["name"]}" kişiliğinde kal')
        if project and project["context"].strip():
            bits.append(f'"{project["name"]}" projesinin bağlamına uy')
        if bits:
            bits.append("konuşmanın önceki kısmını dikkate alarak devam et")
            note = "[Hatırlatma — cevabında bu nottan bahsetme: " + ", ".join(bits) + ".]"
            msgs[-1] = {"role": "user", "content": f"{note}\n\n{msgs[-1]['content']}"}
    return msgs


def auto_title(text: str) -> str:
    line = " ".join(text.split())
    return (line[:57] + "…") if len(line) > 58 else (line or "Yeni sohbet")


def clean_title(raw: str) -> str:
    text = strip_think(raw)
    line = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    line = re.sub(r"^(başlık|title)\s*:\s*", "", line, flags=re.I)
    line = line.strip(" \"'`*#.:-–—«»“”")
    return line[:60]


def _line(**event) -> str:
    return json.dumps(event, ensure_ascii=False) + "\n"


def _ollama_error(status: int, body: str, model: str) -> str:
    try:
        message = json.loads(body).get("error") or body
    except (ValueError, AttributeError):
        message = body
    message = (message or f"HTTP {status}").strip()
    if status == 404 and "not found" in message:
        return f"Model bulunamadı: {model}. Ayarlar → Modeller'den indir ya da: ollama pull {model}"
    return f"Ollama hatası ({status}): {message}"


# --- uygulama ---------------------------------------------------------------------------


def create_app(
    data_dir: str | Path | None = None,
    ollama_url: str | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    prefab_dir: str | Path | None = None,
    train_python: str | Path | None = None,
    ollama_cmd: list[str] | None = None,
    dataset_dir: str | Path | None = None,
    hf_transport: httpx.AsyncBaseTransport | None = None,
) -> FastAPI:
    data_dir = Path(data_dir or os.environ.get("ULL_DATA_DIR", "data"))
    # Ayar dosyaları uygulamanın klasöründe: <proje>/prefab
    prefab_dir = Path(prefab_dir or os.environ.get("ULL_PREFAB_DIR") or ROOT_DIR / "prefab").resolve()
    db = DB(data_dir / "ull.db", legacy_state=data_dir / "state.json")
    forced_url = normalize_url(ollama_url or "")  # testler için
    retired: list[httpx.AsyncClient] = []

    def effective_url() -> str:
        return forced_url or resolve_ollama_url(db.get_settings().get("ollama_url", ""))

    def make_client(url: str) -> httpx.AsyncClient:
        # İlk token, model belleğe yüklenirken dakikalar sürebilir; okuma
        # zaman aşımı bu yüzden cömert. Kullanıcı arayüzden her an durdurabilir.
        # trust_env=False: Windows'taki sistem proxy'si / VPN ayarları yerel
        # Ollama isteklerini başka yere yönlendirmesin.
        return httpx.AsyncClient(
            base_url=url, timeout=httpx.Timeout(10.0, read=600.0), transport=transport, trust_env=False,
        )

    def switch_ollama(url: str) -> None:
        if url == app.state.ollama_url:
            return
        # Eski istemci kapatılmıyor: o anda akan bir cevap onu kullanıyor olabilir.
        retired.append(app.state.ollama)
        app.state.ollama = make_client(url)
        app.state.ollama_url = url

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.ollama_url = effective_url()
        app.state.ollama = make_client(app.state.ollama_url)
        # Hugging Face internette: sistem proxy'si gerekebilir, o yüzden trust_env açık.
        app.state.hf = httpx.AsyncClient(base_url=hf.HF_HOST, timeout=20, transport=hf_transport, follow_redirects=True)
        try:
            yield
        finally:
            for client in [app.state.ollama, app.state.hf, *retired]:
                await client.aclose()
            db.close()

    app = FastAPI(title="ULL-Bot", version=__version__, lifespan=lifespan)
    app.state.effective_ollama_url = effective_url
    trainer = finetune.Trainer(
        ROOT_DIR, data_dir,
        ollama_url=lambda: app.state.ollama_url,
        # Eğitim bitince ULL-Bot yeni modele geçer.
        on_model_ready=lambda name: db.update_settings({"model": name}),
        ollama_cmd=ollama_cmd,
        train_python=Path(train_python) if train_python else None,
        dataset_dir=Path(dataset_dir) if dataset_dir else None,
    )

    def settings() -> dict:
        s = db.get_settings()
        try:
            s["appearance"] = Appearance(**(s.get("appearance") or {})).model_dump()
        except ValueError:
            s["appearance"] = Appearance().model_dump()
        return s

    def need_chat(chat_id: str) -> dict:
        chat = db.get_chat(chat_id)
        if chat is None:
            raise HTTPException(404, "Sohbet bulunamadı")
        return chat

    def need_project(project_id: str) -> dict:
        project = db.get_project(project_id)
        if project is None:
            raise HTTPException(404, "Proje bulunamadı")
        return project

    # --- genel durum / ayarlar ----------------------------------------------------------

    @app.get("/api/bootstrap")
    def bootstrap():
        return {
            "settings": settings(),
            "personas": db.list_personas(),
            "projects": db.list_projects(),
            "chats": db.list_chats(),
            "code_sessions": db.list_code_sessions(),
            "ollama_url": app.state.ollama_url,
            "ollama_url_default": resolve_ollama_url(""),
            "version": __version__,
        }

    @app.get("/api/health")
    def health():
        return {"app": "ull-bot", "version": __version__}

    @app.get("/api/settings")
    def get_settings():
        return settings()

    @app.put("/api/settings")
    def put_settings(body: SettingsPatch):
        patch = body.model_dump(exclude_unset=True, exclude_none=True)
        if "model" in patch:
            patch["model"] = patch["model"].strip()
        if "ollama_url" in patch:
            patch["ollama_url"] = normalize_url(patch["ollama_url"])
            if patch["ollama_url"] and not re.match(r"^https?://[^/\s]+$", patch["ollama_url"]):
                raise HTTPException(422, "Geçersiz adres. Örnek: http://127.0.0.1:11434")
        db.update_settings(patch)
        if "ollama_url" in patch:
            switch_ollama(effective_url())
        return settings()

    # --- ayar dosyaları (prefab) -------------------------------------------------------------

    @app.get("/api/prefabs")
    def list_prefabs():
        files = sorted(prefab_dir.glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True) if prefab_dir.is_dir() else []
        return {"dir": str(prefab_dir), "items": [summarize_prefab(f) for f in files]}

    @app.post("/api/prefabs", status_code=201)
    def save_prefab(body: PrefabSaveIn):
        s = settings()
        path = prefab_dir / prefab_filename(body.name)
        if path.exists() and not body.overwrite:
            raise HTTPException(409, f"{path.name} zaten var")
        data = {
            "ull_bot_prefab": 1,
            "name": body.name.strip(),
            "saved_at": time.strftime("%Y-%m-%d %H:%M"),
            "app_version": __version__,
            "settings": {k: s[k] for k in PREFAB_KEYS},
            "active_persona_id": s["active_persona_id"],
            "personas": db.list_personas(),
        }
        prefab_dir.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
        os.replace(tmp, path)
        return {"file": path.name, "path": str(path)}

    @app.post("/api/prefabs/apply")
    def apply_prefab(body: PrefabFileIn):
        path = prefab_path(prefab_dir, body.file)
        try:
            data = json.loads(path.read_text("utf-8"))
            raw = data.get("settings") or {}
            patch = SettingsPatch(**{k: raw[k] for k in PREFAB_KEYS if k in raw})
            personas = [PersonaIn(name=p.get("name", ""), prompt=p.get("prompt", "")) for p in data.get("personas") or []]
            ids = [str(p.get("id") or "") or None for p in data.get("personas") or []]
        except (OSError, ValueError, AttributeError, TypeError) as e:
            # pydantic'in ValidationError'ı da ValueError.
            raise HTTPException(422, f"Dosyadaki ayarlar geçersiz: {str(e).splitlines()[0]}")
        db.update_settings(patch.model_dump(exclude_unset=True, exclude_none=True))
        loaded = [db.upsert_persona(pid, p.name.strip(), p.prompt) for pid, p in zip(ids, personas)]
        # Dosyada aktif olan kişiliği seç (yeni kimliğiyle).
        active = data.get("active_persona_id")
        for pid, p in zip(ids, loaded):
            if pid and pid == active:
                db.update_settings({"active_persona_id": p["id"]})
        return {"settings": settings(), "personas": db.list_personas(), "loaded_personas": len(loaded)}

    @app.delete("/api/prefabs/{file}")
    def delete_prefab(file: str):
        prefab_path(prefab_dir, file).unlink()
        return {"ok": True}

    @app.post("/api/prefabs/open-folder")
    def open_prefab_folder():
        prefab_dir.mkdir(parents=True, exist_ok=True)
        try:
            open_folder(prefab_dir)
        except OSError as e:
            raise HTTPException(500, f"Klasör açılamadı: {e}")
        return {"ok": True, "dir": str(prefab_dir)}

    # --- kişilikler ----------------------------------------------------------------------

    @app.get("/api/personas")
    def list_personas():
        return db.list_personas()

    @app.post("/api/personas", status_code=201)
    def add_persona(body: PersonaIn):
        return db.add_persona(body.name.strip(), body.prompt)

    @app.put("/api/personas/{persona_id}")
    def update_persona(persona_id: str, body: PersonaIn):
        persona = db.update_persona(persona_id, body.name.strip(), body.prompt)
        if persona is None:
            raise HTTPException(404, "Kişilik bulunamadı")
        return persona

    @app.delete("/api/personas/{persona_id}")
    def delete_persona(persona_id: str):
        if not db.delete_persona(persona_id):
            raise HTTPException(404, "Kişilik bulunamadı")
        return settings()

    @app.put("/api/active")
    def set_active(body: ActiveIn):
        if body.persona_id is not None and db.get_persona(body.persona_id) is None:
            raise HTTPException(404, "Kişilik bulunamadı")
        db.update_settings({"active_persona_id": body.persona_id})
        return settings()

    # --- projeler ---------------------------------------------------------------------------

    @app.get("/api/projects")
    def list_projects():
        return db.list_projects()

    @app.post("/api/projects", status_code=201)
    def add_project(body: ProjectIn):
        return db.add_project(body.name.strip(), body.context)

    @app.patch("/api/projects/{project_id}")
    def update_project(project_id: str, body: ProjectPatch):
        need_project(project_id)
        fields = body.model_dump(exclude_unset=True, exclude_none=True)
        if "name" in fields:
            fields["name"] = fields["name"].strip()
        return db.update_project(project_id, fields)

    @app.delete("/api/projects/{project_id}")
    def delete_project(project_id: str):
        if not db.delete_project(project_id):
            raise HTTPException(404, "Proje bulunamadı")
        return {"ok": True}

    # --- sohbetler ---------------------------------------------------------------------------

    @app.get("/api/chats")
    def list_chats(q: str | None = None):
        return db.list_chats(q)

    @app.get("/api/chats/{chat_id}")
    def get_chat(chat_id: str):
        return {**need_chat(chat_id), "messages": db.messages(chat_id)}

    @app.patch("/api/chats/{chat_id}")
    def update_chat(chat_id: str, body: ChatPatch):
        need_chat(chat_id)
        fields = body.model_dump(exclude_unset=True)
        if "title" in fields:
            if fields["title"] is None:
                del fields["title"]
            else:
                fields["title"] = fields["title"].strip()
                fields["title_locked"] = True  # elle verilen başlığı otomatik başlık ezmesin
        if "pinned" in fields and fields["pinned"] is None:
            del fields["pinned"]
        if fields.get("project_id") is not None:
            need_project(fields["project_id"])
        return db.update_chat(chat_id, fields)

    @app.delete("/api/chats/{chat_id}")
    def delete_chat(chat_id: str):
        if not db.delete_chat(chat_id):
            raise HTTPException(404, "Sohbet bulunamadı")
        return {"ok": True}

    @app.post("/api/chats/{chat_id}/title")
    async def make_title(chat_id: str):
        """İlk cevaptan sonra modele kısa bir başlık yazdırır."""
        chat = need_chat(chat_id)
        s = settings()
        if chat["title_locked"] or not s["auto_title"]:
            return chat
        msgs = db.messages(chat_id)
        user = next((m["content"] for m in msgs if m["role"] == "user"), "")
        answer = next((strip_think(m["content"]) for m in msgs if m["role"] == "assistant"), "")
        if not user:
            return chat
        prompt = (
            "Aşağıdaki konuşma için en fazla 5 kelimelik kısa bir başlık yaz. "
            "Konuşmanın dilinde yaz. Yalnızca başlığı yaz; tırnak, nokta ya da açıklama ekleme.\n\n"
            f"Kullanıcı: {user[:600]}\nAsistan: {answer[:600]}"
        )
        payload = {
            "model": s["model"],
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            # num_ctx sohbetle aynı olmalı: farklı olursa Ollama modeli yeniden yükler.
            "options": {"temperature": 0.3, "num_predict": 48, "num_ctx": s["num_ctx"]},
        }
        try:
            r = await app.state.ollama.post("/api/chat", json=payload, timeout=90)
            r.raise_for_status()
            title = clean_title((r.json().get("message") or {}).get("content", ""))
        except (httpx.HTTPError, ValueError):
            return chat
        if title and not db.get_chat(chat_id)["title_locked"]:
            return db.update_chat(chat_id, {"title": title})
        return db.get_chat(chat_id)

    # --- mesaj gönderme (akış) ------------------------------------------------------------

    @app.post("/api/send")
    async def send(body: SendIn):
        text = (body.content or "").strip()
        s = settings()

        persona_id = body.persona_id or s["active_persona_id"]
        persona = db.get_persona(persona_id) if persona_id else None
        if body.persona_id and persona is None:
            raise HTTPException(404, "Kişilik bulunamadı")

        if body.chat_id:
            chat = need_chat(body.chat_id)
        else:
            if not text:
                raise HTTPException(422, "Mesaj boş olamaz")
            if body.project_id:
                need_project(body.project_id)
            chat = db.create_chat(auto_title(text), body.project_id)
        chat_id = chat["id"]
        project = db.get_project(chat["project_id"]) if chat["project_id"] else None

        if text:
            db.add_message(chat_id, "user", text)
        else:
            if body.regenerate:
                db.delete_trailing_assistant(chat_id)
            history = db.messages(chat_id)
            if not history or history[-1]["role"] != "user":
                raise HTTPException(422, "Cevaplanacak bir mesaj yok")

        history = db.messages(chat_id)
        messages = build_messages(history, persona, project, s["persona_reminder"])
        model = s["model"]
        payload = {
            "model": model,
            "messages": messages,
            "stream": True,
            "options": {"temperature": s["temperature"], "num_ctx": s["num_ctx"]},
        }
        persona_meta = {"persona_name": persona["name"]} if persona else {}
        is_first_answer = not any(m["role"] == "assistant" for m in history)

        async def events():
            content: list[str] = []
            thinking: list[str] = []
            saved: dict = {}

            def save(meta: dict) -> int | None:
                # Tek sefer: normal bitişte, hatada ya da kullanıcı durdurunca.
                if saved:
                    return saved.get("id")
                saved["done"] = True
                answer, thought = "".join(content), "".join(thinking)
                if not answer.strip() and not thought.strip():
                    return None
                saved["id"] = db.add_message(
                    chat_id, "assistant", answer, thinking=thought, model=model,
                    persona_id=persona["id"] if persona else None, meta={**persona_meta, **meta},
                )
                return saved["id"]

            yield _line(type="start", chat=db.get_chat(chat_id))
            try:
                async with app.state.ollama.stream("POST", "/api/chat", json=payload) as r:
                    if r.status_code != 200:
                        text_ = (await r.aread()).decode("utf-8", "replace")
                        yield _line(type="error", message=_ollama_error(r.status_code, text_, model))
                        return
                    async for raw in r.aiter_lines():
                        if not raw.strip():
                            continue
                        try:
                            chunk = json.loads(raw)
                        except ValueError:
                            continue
                        if chunk.get("error"):
                            save({"error": True})
                            yield _line(type="error", message=str(chunk["error"]))
                            return
                        msg = chunk.get("message") or {}
                        if msg.get("thinking"):
                            thinking.append(msg["thinking"])
                            yield _line(type="thinking", content=msg["thinking"])
                        if msg.get("content"):
                            content.append(msg["content"])
                            yield _line(type="token", content=msg["content"])
                        if chunk.get("done"):
                            meta = {k: chunk[k] for k in ("eval_count", "eval_duration") if chunk.get(k)}
                            mid = save(meta)
                            yield _line(
                                type="done", message_id=mid, model=chunk.get("model", model),
                                first_answer=is_first_answer, chat=db.get_chat(chat_id), **meta,
                            )
                            return
                mid = save({})
                yield _line(type="done", message_id=mid, model=model, first_answer=is_first_answer, chat=db.get_chat(chat_id))
            except httpx.ConnectError as e:
                yield _line(type="error", message=describe_ollama_error(e, app.state.ollama_url))
            except httpx.HTTPError as e:
                save({"error": True})
                yield _line(type="error", message=describe_ollama_error(e, app.state.ollama_url))
            finally:
                # Kullanıcı "durdur"a bastı (bağlantı koptu): yarım cevap kaybolmasın.
                save({"stopped": True})

        return StreamingResponse(events(), media_type="application/x-ndjson")

    # --- modeller ------------------------------------------------------------------------------

    async def installed_models() -> tuple[list[dict] | None, str]:
        """(modeller, hata). Ollama'ya ulaşılamazsa modeller None, hata açıklamalı."""
        try:
            r = await app.state.ollama.get("/api/tags", timeout=5)
            r.raise_for_status()
            raw = r.json().get("models", [])
        except (httpx.HTTPError, ValueError, AttributeError) as e:
            return None, describe_ollama_error(e, app.state.ollama_url)
        out = []
        for m in raw:
            d = m.get("details") or {}
            info = catalog.lookup(m.get("name", ""))
            out.append({
                "name": m.get("name", ""),
                "size": m.get("size", 0),
                "parameter_size": d.get("parameter_size", ""),
                "quantization": d.get("quantization_level", ""),
                "family": d.get("family", ""),
                "modified_at": m.get("modified_at", ""),
                "desc": info["desc"] if info else "",
                "tags": info["tags"] if info else [],
            })
        return sorted(out, key=lambda m: m["name"]), ""

    @app.get("/api/models")
    async def list_models():
        models, error = await installed_models()
        if models is None:
            return {"ok": False, "models": [], "error": error, "url": app.state.ollama_url}
        return {"ok": True, "models": models, "url": app.state.ollama_url}

    @app.post("/api/ollama/test")
    async def test_ollama(body: OllamaTestIn):
        """Verilen adreste (boşsa geçerli adreste) Ollama var mı? Ayarı değiştirmez."""
        url = normalize_url(body.url) or app.state.ollama_url
        try:
            async with httpx.AsyncClient(base_url=url, timeout=4, transport=transport, trust_env=False) as c:
                r = await c.get("/api/tags")
                r.raise_for_status()
                count = len(r.json().get("models", []))
                try:
                    version = (await c.get("/api/version")).json().get("version", "")
                except (httpx.HTTPError, ValueError):
                    version = ""
        except (httpx.HTTPError, ValueError, AttributeError) as e:
            return {"ok": False, "url": url, "error": describe_ollama_error(e, url)}
        return {"ok": True, "url": url, "models": count, "version": version}

    @app.get("/api/models/recommend")
    async def recommend_models():
        hw = await run_in_threadpool(hardware.scan)
        models, _ = await installed_models()
        names = {m["name"] for m in models or []}
        return {
            "hardware": hw,
            "categories": catalog.CATEGORIES,
            "models": catalog.recommend(hw, names),
            "ollama_ok": models is not None,
        }

    @app.post("/api/models/pull")
    async def pull_model(body: PullIn):
        name = body.name.strip()

        async def events():
            try:
                async with app.state.ollama.stream(
                    "POST", "/api/pull", json={"model": name, "stream": True}, timeout=httpx.Timeout(10.0, read=None)
                ) as r:
                    if r.status_code != 200:
                        text_ = (await r.aread()).decode("utf-8", "replace")
                        yield _line(type="error", message=_ollama_error(r.status_code, text_, name))
                        return
                    async for raw in r.aiter_lines():
                        if not raw.strip():
                            continue
                        try:
                            chunk = json.loads(raw)
                        except ValueError:
                            continue
                        if chunk.get("error"):
                            yield _line(type="error", message=str(chunk["error"]))
                            return
                        yield _line(
                            type="progress", status=chunk.get("status", ""),
                            total=chunk.get("total"), completed=chunk.get("completed"),
                        )
                        if chunk.get("status") == "success":
                            yield _line(type="done", name=name)
                            return
                yield _line(type="done", name=name)
            except httpx.HTTPError as e:
                yield _line(type="error", message=describe_ollama_error(e, app.state.ollama_url))

        return StreamingResponse(events(), media_type="application/x-ndjson")

    @app.get("/api/hf/inspect")
    async def hf_inspect(ref: str):
        hw = await run_in_threadpool(hardware.scan)
        try:
            return await hf.inspect(app.state.hf, ref, hw)
        except ValueError as e:
            raise HTTPException(422, str(e))
        except LookupError as e:
            raise HTTPException(404, str(e))
        except ConnectionError as e:
            raise HTTPException(502, str(e))

    @app.post("/api/models/copy")
    async def copy_model(body: ModelCopyIn):
        """Ollama'da kısa ad ver (hf.co/... adları çok uzun). Dosyalar kopyalanmaz, aynı veriyi gösterir."""
        try:
            dest = finetune.ollama_model_name(body.destination)
        except ValueError as e:
            raise HTTPException(422, str(e))
        try:
            r = await app.state.ollama.post("/api/copy", json={"source": body.source, "destination": dest})
            if r.status_code != 200:
                raise HTTPException(r.status_code, _ollama_error(r.status_code, r.text, body.source))
            if body.delete_source and body.source != dest:
                await app.state.ollama.request("DELETE", "/api/delete", json={"model": body.source})
        except httpx.HTTPError as e:
            raise HTTPException(502, describe_ollama_error(e, app.state.ollama_url))
        return {"ok": True, "name": dest}

    @app.delete("/api/models/{name:path}")
    async def delete_model(name: str):
        try:
            r = await app.state.ollama.request("DELETE", "/api/delete", json={"model": name})
        except httpx.HTTPError as e:
            raise HTTPException(502, describe_ollama_error(e, app.state.ollama_url))
        if r.status_code != 200:
            raise HTTPException(r.status_code, _ollama_error(r.status_code, r.text, name))
        return {"ok": True}

    # --- ince ayar (fine-tune) -------------------------------------------------------------

    @app.get("/api/train/env")
    async def train_env(refresh: bool = False):
        env = await run_in_threadpool(trainer.env, refresh)
        models, _ = await installed_models()
        names = {m["name"] for m in models or []}
        bases = []
        for key, info in finetune.BASES.items():
            bases.append({
                "name": key, "label": info["label"], "params_b": info["params_b"],
                "installed": bool({key, f"{key}:latest"} & names),
                "vram_4bit": finetune.vram_need(info["params_b"], True),
                "vram_full": finetune.vram_need(info["params_b"], False),
            })
        return {"env": env, "bases": bases, "busy": trainer.busy()}

    @app.post("/api/train/env/install")
    def train_install():
        try:
            return trainer.start_install().snapshot()
        except RuntimeError as e:
            raise HTTPException(409, str(e))

    @app.post("/api/train/env/repair")
    def train_repair():
        try:
            return trainer.start_install(repair_only=True).snapshot()
        except RuntimeError as e:
            raise HTTPException(409, str(e))

    @app.get("/api/train/status")
    def train_status():
        return {"job": trainer.status(), "history": trainer.history()}

    @app.post("/api/train/cancel")
    def train_cancel():
        return {"ok": trainer.cancel()}

    @app.post("/api/train/start")
    async def train_start(body: TrainStartIn):
        models, _ = await installed_models()
        try:
            job = trainer.start_training(
                base=body.base, dataset=body.dataset, name=body.name, system=body.system, epochs=body.epochs,
                lr=body.lr, rank=body.rank, max_len=body.max_len, installed={m["name"] for m in models or []},
            )
        except RuntimeError as e:
            raise HTTPException(409, str(e))
        except FileNotFoundError:
            raise HTTPException(404, "Veri seti bulunamadı")
        except ValueError as e:
            raise HTTPException(422, str(e))
        return job.snapshot()

    @app.get("/api/train/datasets")
    def train_datasets():
        return {"dir": str(trainer.datasets), "items": trainer.list_datasets()}

    @app.get("/api/train/datasets/{file}")
    def train_dataset(file: str):
        try:
            return finetune.dataset_summary(trainer.dataset_path(file))
        except ValueError as e:
            raise HTTPException(400, str(e))
        except FileNotFoundError:
            raise HTTPException(404, "Veri seti bulunamadı")

    @app.post("/api/train/datasets", status_code=201)
    def train_dataset_upload(body: DatasetUploadIn):
        try:
            return {"file": trainer.save_dataset(body.name, body.content)}
        except ValueError as e:
            raise HTTPException(422, str(e))

    @app.delete("/api/train/datasets/{file}")
    def train_dataset_delete(file: str):
        try:
            trainer.dataset_path(file).unlink()
        except ValueError as e:
            raise HTTPException(400, str(e))
        except FileNotFoundError:
            raise HTTPException(404, "Veri seti bulunamadı")
        return {"ok": True}

    @app.post("/api/train/datasets/open-folder")
    def train_dataset_folder():
        trainer.datasets.mkdir(parents=True, exist_ok=True)
        try:
            open_folder(trainer.datasets)
        except OSError as e:
            raise HTTPException(500, f"Klasör açılamadı: {e}")
        return {"ok": True}

    # --- code modu ------------------------------------------------------------------------------

    broker = coder.PermissionBroker()
    code_always: dict[str, set[str]] = {}
    code_running: set[str] = set()

    def need_code(sid: str) -> dict:
        sess = db.get_code_session(sid)
        if sess is None:
            raise HTTPException(404, "Oturum bulunamadı")
        return sess

    def code_model() -> str:
        s = settings()
        return (s.get("code_model") or "").strip() or s["model"]

    @app.get("/api/code/sessions")
    def code_sessions():
        return db.list_code_sessions()

    @app.post("/api/code/sessions", status_code=201)
    def code_create(body: CodeSessionIn):
        path = Path(body.cwd.strip()).expanduser()
        try:
            path = path.resolve()
        except OSError:
            raise HTTPException(422, "Geçersiz klasör")
        if not path.is_dir():
            raise HTTPException(422, f"Klasör bulunamadı: {path}")
        return db.create_code_session(str(path), "Yeni oturum")

    @app.get("/api/code/sessions/{sid}")
    def code_get(sid: str):
        sess = need_code(sid)
        return {**sess, "messages": db.code_messages(sid), "running": sid in code_running,
                "exists": Path(sess["cwd"]).is_dir()}

    @app.patch("/api/code/sessions/{sid}")
    def code_patch(sid: str, body: CodeSessionPatch):
        need_code(sid)
        return db.update_code_session(sid, body.model_dump(exclude_none=True))

    @app.delete("/api/code/sessions/{sid}")
    def code_delete(sid: str):
        if not db.delete_code_session(sid):
            raise HTTPException(404, "Oturum bulunamadı")
        code_always.pop(sid, None)
        return {"ok": True}

    @app.get("/api/code/browse")
    def code_browse(path: str | None = None):
        return coder.browse(path)

    @app.post("/api/code/permission")
    def code_permission(body: PermissionIn):
        if not broker.answer(body.id, body.decision):
            raise HTTPException(404, "Bekleyen izin isteği yok")
        return {"ok": True}

    @app.post("/api/code/sessions/{sid}/send")
    async def code_send(sid: str, body: CodeSendIn):
        sess = need_code(sid)
        ws = coder.Workspace(sess["cwd"])
        if not ws.root.is_dir():
            raise HTTPException(422, f"Klasör bulunamadı: {sess['cwd']}")
        if sid in code_running:
            raise HTTPException(409, "Bu oturumda zaten bir iş çalışıyor")
        s = settings()
        model = code_model()
        stored = db.code_messages(sid)
        text = body.content.strip()
        user = {"role": "user", "content": text}
        db.add_code_message(sid, user)
        stored.append(user)
        if sess["title"] == "Yeni oturum":
            db.update_code_session(sid, {"title": auto_title(text)})
        options = {"temperature": 0.2, "num_ctx": max(int(s.get("code_num_ctx") or 16384), 2048)}

        async def stream_chat(messages: list[dict]):
            payload = {"model": model, "messages": messages, "tools": coder.TOOLS, "stream": True, "options": options}
            async with app.state.ollama.stream("POST", "/api/chat", json=payload) as r:
                if r.status_code != 200:
                    raw = (await r.aread()).decode("utf-8", "replace")
                    msg = _ollama_error(r.status_code, raw, model)
                    if "does not support tools" in raw:
                        msg = (f"{model} araç çağırmayı (tools) desteklemiyor, bu yüzden Code modunda çalışamaz. "
                               "Sağ üstten araç destekli bir kod modeli seç: qwen2.5-coder, qwen3, qwen3-coder, llama3.1, gpt-oss…")
                    yield {"error": msg}
                    return
                async for line in r.aiter_lines():
                    if line.strip():
                        try:
                            yield json.loads(line)
                        except ValueError:
                            continue

        async def events():
            code_running.add(sid)
            try:
                yield _line(type="start", session=db.get_code_session(sid), model=model)
                async for ev in coder.run_agent(
                    stream_chat=stream_chat, stored=stored, ws=ws,
                    mode=lambda: (db.get_code_session(sid) or {}).get("mode", "ask"),
                    broker=broker, save=lambda m: db.add_code_message(sid, m),
                    always=code_always.setdefault(sid, set()),
                ):
                    yield _line(**ev)
                yield _line(type="done", session=db.get_code_session(sid))
            except httpx.HTTPError as e:
                yield _line(type="error", message=describe_ollama_error(e, app.state.ollama_url))
            finally:
                code_running.discard(sid)

        return StreamingResponse(events(), media_type="application/x-ndjson")

    @app.get("/api/code/claude")
    async def claude_info():
        version = ""
        try:
            version = (await app.state.ollama.get("/api/version", timeout=3)).json().get("version", "")
        except (httpx.HTTPError, ValueError, AttributeError):
            pass
        return {"found": bool(shutil.which("claude")), "ollama_version": version, "install": coder.INSTALL_HINT}

    @app.post("/api/code/sessions/{sid}/claude")
    def claude_launch(sid: str):
        sess = need_code(sid)
        return coder.launch_claude(sess["cwd"], code_model(), app.state.ollama_url)

    # Arayüz en sonda: "/" altındaki her şeyi yakalar, API rotaları önce eşleşir.
    app.mount("/", NoCacheStaticFiles(directory=WEB_DIR, html=True), name="web")
    return app


def _port_busy(host: str, port: int) -> bool:
    target = "127.0.0.1" if host in ("0.0.0.0", "") else host
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((target, port)) == 0


def _running_version(url: str) -> str | None:
    """Portta çalışan şey ULL-Bot ise sürümü; eski sürüm ya da başka bir program ise None."""
    try:
        r = httpx.get(f"{url}/api/health", timeout=1.5, trust_env=False)
        data = r.json()
        return data.get("version") if data.get("app") == "ull-bot" else None
    except (httpx.HTTPError, ValueError, AttributeError):
        return None


def _check_ollama(url: str) -> str:
    try:
        r = httpx.get(f"{url}/api/tags", timeout=3, trust_env=False)
        r.raise_for_status()
        n = len(r.json().get("models", []))
        return f"bağlı ✓ ({n} model kurulu)" if n else "bağlı ✓ ama hiç model yok — Ayarlar → Modeller'den indir"
    except (httpx.HTTPError, ValueError, AttributeError) as e:
        return "BAĞLANILAMADI ✗ — " + describe_ollama_error(e, url)


def run() -> None:
    import threading
    import webbrowser

    import uvicorn

    host = os.environ.get("ULL_HOST", "127.0.0.1")
    port = int(os.environ.get("ULL_PORT", "8765"))
    url = f"http://{'127.0.0.1' if host == '0.0.0.0' else host}:{port}"
    open_browser = os.environ.get("ULL_NO_BROWSER") != "1"

    if _port_busy(host, port):
        running = _running_version(url)
        if running == __version__:
            print(f"ULL-Bot v{__version__} zaten çalışıyor → {url}")
            if open_browser:
                webbrowser.open(url)
            return
        print(
            f"\nHATA: {port} portu kullanımda"
            + (f" — ULL-Bot v{running} çalışıyor." if running else " — büyük ihtimalle ESKİ bir ULL-Bot hâlâ açık.")
            + "\nÖnce onu kapat: açık kalan terminal penceresinde Ctrl+C"
            " (Windows'ta bulamazsan Görev Yöneticisi → python.exe → Görevi sonlandır)."
            f"\nYa da başka port kullan: ULL_PORT=8766\n",
            file=sys.stderr,
        )
        sys.exit(1)

    app = create_app()
    ollama_url = app.state.effective_ollama_url()
    print(f"ULL-Bot v{__version__} → {url}")
    print(f"Ollama ({ollama_url}): {_check_ollama(ollama_url)}")
    if open_browser:
        threading.Timer(1.2, webbrowser.open, args=[url]).start()
    uvicorn.run(app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    run()
