import json
import uuid
from pathlib import Path
from typing import AsyncIterator

import httpx
from fastapi import FastAPI
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.db.connection import get_connection, init_db
from app.settings import settings

WEB_DIR = Path(__file__).resolve().parent.parent / "web"

app = FastAPI(title="ULL-Bot Orchestrator")
app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None


@app.on_event("startup")
def _startup() -> None:
    init_db()


@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


@app.post("/chat")
async def chat(req: ChatRequest) -> StreamingResponse:
    session_id = req.session_id or str(uuid.uuid4())
    _ensure_session(session_id)
    _save_message(session_id, "user", req.message)

    async def stream() -> AsyncIterator[str]:
        chunks: list[str] = []
        async with httpx.AsyncClient(timeout=60) as client:
            async with client.stream(
                "POST",
                f"{settings.litellm_base_url}/chat/completions",
                headers={"Authorization": f"Bearer {settings.litellm_master_key}"},
                json={
                    "model": settings.litellm_model,
                    "messages": [{"role": "user", "content": req.message}],
                    "stream": True,
                },
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    payload = line[len("data: ") :]
                    if payload.strip() == "[DONE]":
                        break
                    delta = json.loads(payload)["choices"][0]["delta"].get("content")
                    if delta:
                        chunks.append(delta)
                        yield delta
        _save_message(session_id, "assistant", "".join(chunks), model=settings.litellm_model)

    return StreamingResponse(
        stream(), media_type="text/plain", headers={"X-Session-Id": session_id}
    )


def _ensure_session(session_id: str) -> None:
    with get_connection() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO sessions (id, created_at, title) VALUES (?, datetime('now'), NULL)",
            (session_id,),
        )
        conn.commit()


def _save_message(session_id: str, role: str, content: str, model: str | None = None) -> None:
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO messages (session_id, role, content, model, ts) VALUES (?, ?, ?, ?, datetime('now'))",
            (session_id, role, content, model),
        )
        conn.commit()
