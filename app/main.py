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
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import catalog, hardware
from app.db import DB

WEB_DIR = Path(__file__).resolve().parent.parent / "web"

# Windows'ta MIME türleri kayıt defterinden okunuyor ve bazı makinelerde .js
# "text/plain" kayıtlı. Tarayıcı o zaman ES modüllerini yüklemeyi reddeder
# ve arayüz boş kalır; doğru türleri açıkça veriyoruz.
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("text/css", ".css")


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


class PullIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)


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
) -> FastAPI:
    data_dir = Path(data_dir or os.environ.get("ULL_DATA_DIR", "data"))
    ollama_url = (ollama_url or os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")).rstrip("/")
    db = DB(data_dir / "ull.db", legacy_state=data_dir / "state.json")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # İlk token, model belleğe yüklenirken dakikalar sürebilir; okuma
        # zaman aşımı bu yüzden cömert. Kullanıcı arayüzden her an durdurabilir.
        timeout = httpx.Timeout(10.0, read=600.0)
        async with httpx.AsyncClient(base_url=ollama_url, timeout=timeout, transport=transport) as client:
            app.state.ollama = client
            yield
        db.close()

    app = FastAPI(title="ULL-Bot", lifespan=lifespan)

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
            "ollama_url": ollama_url,
        }

    @app.get("/api/settings")
    def get_settings():
        return settings()

    @app.put("/api/settings")
    def put_settings(body: SettingsPatch):
        patch = body.model_dump(exclude_unset=True, exclude_none=True)
        if "model" in patch:
            patch["model"] = patch["model"].strip()
        db.update_settings(patch)
        return settings()

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
            except httpx.ConnectError:
                yield _line(
                    type="error",
                    message=f"Ollama'ya bağlanılamadı ({ollama_url}). Ollama çalışıyor mu? (ollama serve)",
                )
            except httpx.HTTPError as e:
                save({"error": True})
                yield _line(type="error", message=f"Ollama bağlantı hatası: {e!s}")
            finally:
                # Kullanıcı "durdur"a bastı (bağlantı koptu): yarım cevap kaybolmasın.
                save({"stopped": True})

        return StreamingResponse(events(), media_type="application/x-ndjson")

    # --- modeller ------------------------------------------------------------------------------

    async def installed_models() -> list[dict] | None:
        try:
            r = await app.state.ollama.get("/api/tags")
            r.raise_for_status()
            raw = r.json().get("models", [])
        except (httpx.HTTPError, ValueError):
            return None
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
        return sorted(out, key=lambda m: m["name"])

    @app.get("/api/models")
    async def list_models():
        models = await installed_models()
        if models is None:
            return {"ok": False, "models": [], "error": f"Ollama'ya ulaşılamadı ({ollama_url})"}
        return {"ok": True, "models": models}

    @app.get("/api/models/recommend")
    async def recommend_models():
        hw = await run_in_threadpool(hardware.scan)
        models = await installed_models()
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
            except httpx.ConnectError:
                yield _line(type="error", message=f"Ollama'ya bağlanılamadı ({ollama_url})")
            except httpx.HTTPError as e:
                yield _line(type="error", message=f"İndirme hatası: {e!s}")

        return StreamingResponse(events(), media_type="application/x-ndjson")

    @app.delete("/api/models/{name:path}")
    async def delete_model(name: str):
        try:
            r = await app.state.ollama.request("DELETE", "/api/delete", json={"model": name})
        except httpx.HTTPError:
            raise HTTPException(502, f"Ollama'ya ulaşılamadı ({ollama_url})")
        if r.status_code != 200:
            raise HTTPException(r.status_code, _ollama_error(r.status_code, r.text, name))
        return {"ok": True}

    # Arayüz en sonda: "/" altındaki her şeyi yakalar, API rotaları önce eşleşir.
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
    return app


def run() -> None:
    import threading
    import webbrowser

    import uvicorn

    host = os.environ.get("ULL_HOST", "127.0.0.1")
    port = int(os.environ.get("ULL_PORT", "8765"))
    url = f"http://{host}:{port}"
    print(f"ULL-Bot → {url}")
    if os.environ.get("ULL_NO_BROWSER") != "1":
        threading.Timer(1.2, webbrowser.open, args=[url]).start()
    uvicorn.run(create_app(), host=host, port=port, log_level="warning")


if __name__ == "__main__":
    run()
