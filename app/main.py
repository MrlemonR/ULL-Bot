"""ULL-Bot sunucusu: arayüzü sunar, sohbeti lokal Ollama'ya aktarır.

Ortam değişkenleri (hepsi isteğe bağlı):
    OLLAMA_URL      Ollama adresi (varsayılan http://127.0.0.1:11434)
    ULL_DATA_DIR    ayarların tutulduğu klasör (varsayılan ./data)
    ULL_HOST        dinlenecek adres (varsayılan 127.0.0.1)
    ULL_PORT        port (varsayılan 8765)
    ULL_NO_BROWSER  "1" ise tarayıcı otomatik açılmaz
"""

from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.store import Store

WEB_DIR = Path(__file__).resolve().parent.parent / "web"


class PersonaIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    prompt: str = Field(default="", max_length=20000)


class SettingsIn(BaseModel):
    model: str = Field(min_length=1, max_length=200)


class ActiveIn(BaseModel):
    persona_id: str | None


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class ChatIn(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1)
    # Boş bırakılırsa ayarlardaki aktif kişilik kullanılır.
    persona_id: str | None = None


def _line(**event) -> str:
    return json.dumps(event, ensure_ascii=False) + "\n"


def _ollama_error(status: int, body: str, model: str) -> str:
    try:
        message = json.loads(body).get("error") or body
    except (ValueError, AttributeError):
        message = body
    message = (message or f"HTTP {status}").strip()
    if status == 404 and "not found" in message:
        return f"Model bulunamadı: {model}. Kurmak için: ollama pull {model}"
    return f"Ollama hatası ({status}): {message}"


def create_app(
    data_dir: str | Path | None = None,
    ollama_url: str | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> FastAPI:
    data_dir = Path(data_dir or os.environ.get("ULL_DATA_DIR", "data"))
    ollama_url = (ollama_url or os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")).rstrip("/")
    store = Store(data_dir / "state.json")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # İlk token, model belleğe yüklenirken dakikalar sürebilir; okuma
        # zaman aşımı bu yüzden cömert. Kullanıcı arayüzden her an durdurabilir.
        timeout = httpx.Timeout(10.0, read=600.0)
        async with httpx.AsyncClient(base_url=ollama_url, timeout=timeout, transport=transport) as client:
            app.state.ollama = client
            yield

    app = FastAPI(title="ULL-Bot", lifespan=lifespan)

    # --- ayarlar ve kişilikler ----------------------------------------------

    @app.get("/api/state")
    def get_state():
        return {**store.snapshot(), "ollama_url": ollama_url}

    @app.put("/api/settings")
    def put_settings(body: SettingsIn):
        store.set_model(body.model.strip())
        return store.snapshot()

    @app.post("/api/personas", status_code=201)
    def add_persona(body: PersonaIn):
        return store.add_persona(body.name.strip(), body.prompt)

    @app.put("/api/personas/{persona_id}")
    def update_persona(persona_id: str, body: PersonaIn):
        persona = store.update_persona(persona_id, body.name.strip(), body.prompt)
        if persona is None:
            raise HTTPException(404, "Kişilik bulunamadı")
        return persona

    @app.delete("/api/personas/{persona_id}")
    def delete_persona(persona_id: str):
        if not store.delete_persona(persona_id):
            raise HTTPException(404, "Kişilik bulunamadı")
        return store.snapshot()

    @app.put("/api/active")
    def set_active(body: ActiveIn):
        if not store.set_active(body.persona_id):
            raise HTTPException(404, "Kişilik bulunamadı")
        return store.snapshot()

    # --- Ollama ---------------------------------------------------------------

    @app.get("/api/models")
    async def list_models():
        try:
            r = await app.state.ollama.get("/api/tags")
            r.raise_for_status()
            names = sorted(m["name"] for m in r.json().get("models", []))
            return {"ok": True, "models": names}
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            return {"ok": False, "models": [], "error": f"Ollama'ya ulaşılamadı ({ollama_url})"}

    @app.post("/api/chat")
    async def chat(body: ChatIn):
        state = store.snapshot()
        persona_id = body.persona_id or state["active_persona_id"]
        persona = store.get_persona(persona_id) if persona_id else None
        if body.persona_id and persona is None:
            raise HTTPException(404, "Kişilik bulunamadı")

        # Master prompt her isteğin başına sistem mesajı olarak eklenir:
        # Ollama durumsuz, yani "konuşmanın başı" her istekte yeniden kuruluyor.
        messages = []
        if persona and persona["prompt"].strip():
            messages.append({"role": "system", "content": persona["prompt"]})
        messages += [m.model_dump() for m in body.messages]
        model = state["model"]
        payload = {"model": model, "messages": messages, "stream": True}

        async def events():
            try:
                async with app.state.ollama.stream("POST", "/api/chat", json=payload) as r:
                    if r.status_code != 200:
                        text = (await r.aread()).decode("utf-8", "replace")
                        yield _line(type="error", message=_ollama_error(r.status_code, text, model))
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
                        content = (chunk.get("message") or {}).get("content")
                        if content:
                            yield _line(type="token", content=content)
                        if chunk.get("done"):
                            yield _line(
                                type="done",
                                model=chunk.get("model", model),
                                eval_count=chunk.get("eval_count"),
                                eval_duration=chunk.get("eval_duration"),
                            )
                            return
                yield _line(type="done", model=model)
            except httpx.ConnectError:
                yield _line(
                    type="error",
                    message=f"Ollama'ya bağlanılamadı ({ollama_url}). Ollama çalışıyor mu? (ollama serve)",
                )
            except httpx.HTTPError as e:
                yield _line(type="error", message=f"Ollama bağlantı hatası: {e!s}")

        return StreamingResponse(events(), media_type="application/x-ndjson")

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
