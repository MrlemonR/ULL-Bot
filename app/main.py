import asyncio
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.agent.loop import AgentLoop
from app.db.connection import init_db
from app.quota.models import get_quota_config
from app.quota.probes import probe_all
from app.quota.state import disable, enable, get_state
from app.quota.tracker import snapshot
from app.router.selector import describe_chain, get_routing_config
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


@app.get("/api/quota")
async def quota(probe: bool = False) -> dict[str, Any]:
    """Kota paneli verisi (spec §7.2).

    `?probe=true` ile sağlayıcıdan canlı veri çekilir; varsayılan olarak
    çekilmez ki panel her açılışta ekstra istek harcamasın.
    """
    if probe:
        await probe_all()

    chain = {row["provider"]: row for row in describe_chain()}
    providers: list[dict[str, Any]] = []

    for name, quota_config in get_quota_config().providers.items():
        if name not in chain:
            continue  # routing.yaml'da olmayan sağlayıcı panelde de yok
        state = get_state(name)
        windows = [
            {
                "window": usage.window,
                "requests": usage.requests,
                "tokens": usage.tokens,
                "max_requests": usage.max_requests,
                "max_tokens": usage.max_tokens,
                "remaining_requests": usage.remaining_requests,
                "remaining_tokens": usage.remaining_tokens,
                "free_ratio": round(usage.free_ratio(), 4),
                "known": usage.known,
                # Kullanıcı hangisinin güvenilir olduğunu bilsin (spec §7.2).
                "source": usage.source,
                "resets_at": usage.resets_at.isoformat() if usage.resets_at else None,
            }
            for usage in snapshot(name, state=state)
        ]
        providers.append(
            {
                "provider": name,
                "model": chain[name]["model"],
                "available": chain[name]["available"],
                "reason": chain[name]["reason"],
                "health": state.health,
                "note": state.note,
                "cooldown_seconds": state.cooldown_seconds_left(),
                "last_probe": state.last_probe_ts.isoformat() if state.last_probe_ts else None,
                "probe_kind": quota_config.probe,
                "reset_policy": quota_config.reset,
                "windows": windows,
                "configured": bool(settings.api_key_for(name)),
            }
        )

    return {
        "providers": providers,
        "profile": settings.profile,
        "reserve_ratio": settings.reserve_ratio or get_quota_config().reserve_ratio,
        "fallback_behaviour": get_routing_config().fallback_behaviour,
    }


@app.post("/api/quota/{provider}/{action}")
def quota_control(provider: str, action: str) -> dict[str, Any]:
    """Manuel 'sağlayıcıyı devre dışı bırak / geri aç' düğmesi (spec §7.2)."""
    if action == "disable":
        disable(provider)
    elif action == "enable":
        enable(provider)
    else:
        return {"ok": False, "error": f"bilinmeyen işlem: {action}"}
    state = get_state(provider)
    return {"ok": True, "provider": provider, "health": state.health}


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
