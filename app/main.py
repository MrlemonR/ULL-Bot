import asyncio
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.agent.loop import AgentLoop
from app.db.connection import init_db
from app.settings import settings

WEB_DIR = Path(__file__).resolve().parent.parent / "web"

app = FastAPI(title="ULL-Bot Orchestrator")
app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


@app.on_event("startup")
def _startup() -> None:
    init_db()


@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


@app.get("/api/config")
def config() -> dict[str, Any]:
    """UI'ın başlıkta gösterdiği çalışma ayarları."""
    return {
        "profile": settings.profile,
        "model": settings.litellm_model,
        "dry_run": settings.dry_run,
        "workspace_root": str(settings.resolved_workspace_root),
        "max_agent_steps": settings.max_agent_steps,
    }


@app.websocket("/ws/chat")
async def ws_chat(websocket: WebSocket) -> None:
    """Sohbet + onay diyalogları (spec §6.1/4b).

    Tek soket üzerinden çift yönlü: ajan döngüsü ayrı bir task'ta çalışır,
    bu döngü ise mesaj almaya devam eder — böylece ajan bir onay beklerken
    kullanıcının cevabı gelebilir.
    """
    await websocket.accept()
    pending: dict[str, asyncio.Future[bool]] = {}
    send_lock = asyncio.Lock()
    task: asyncio.Task | None = None

    async def emit(event: dict[str, Any]) -> None:
        # Kullanıcı ajan çalışırken sekmeyi kapatabilir; kapalı sokete yazmaya
        # çalışmak döngüyü düşürmesin.
        try:
            async with send_lock:
                await websocket.send_json(event)
        except (WebSocketDisconnect, RuntimeError):
            pass

    async def approve(request: dict[str, Any]) -> bool:
        request_id = str(request.get("id", uuid.uuid4()))
        future: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
        pending[request_id] = future
        await emit({**request, "id": request_id})
        try:
            return await asyncio.wait_for(future, timeout=settings.approval_timeout_seconds)
        except asyncio.TimeoutError:
            await emit(
                {
                    "type": "approval_timeout",
                    "id": request_id,
                    "message": f"{settings.approval_timeout_seconds} saniyede cevap gelmedi, istek reddedildi.",
                }
            )
            return False
        finally:
            pending.pop(request_id, None)

    try:
        while True:
            data = await websocket.receive_json()
            kind = data.get("type")

            if kind in {"approval_response", "continue_response"}:
                future = pending.get(str(data.get("id")))
                if future is not None and not future.done():
                    future.set_result(bool(data.get("approved")))
                continue

            if kind != "user_message":
                await emit({"type": "error", "message": f"Bilinmeyen mesaj tipi: {kind}"})
                continue

            if task is not None and not task.done():
                await emit({"type": "error", "message": "Önceki istek hâlâ çalışıyor."})
                continue

            session_id = data.get("session_id") or str(uuid.uuid4())
            content = (data.get("content") or "").strip()
            if not content:
                continue

            await emit({"type": "session", "session_id": session_id})
            agent = AgentLoop(session_id=session_id, emit=emit, approve=approve)
            task = asyncio.create_task(agent.run(content))

            def _on_done(finished: asyncio.Task) -> None:
                if finished.cancelled():
                    return
                error = finished.exception()
                if error is not None:
                    asyncio.create_task(
                        emit({"type": "error", "message": f"Ajan hatası: {error!r}"})
                    )

            task.add_done_callback(_on_done)

    except WebSocketDisconnect:
        pass
    finally:
        if task is not None and not task.done():
            task.cancel()
        for future in pending.values():
            if not future.done():
                future.cancel()
