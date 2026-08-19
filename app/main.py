import asyncio
import shutil
import subprocess
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from fastapi import Body, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from app.agent.loop import AgentLoop
from app.background import background
from app.calendar import service as calendar_service
from app.calendar import store as calendar_store
from app.calendar.store import local_tz
from app.db.connection import init_db
from app.mail import secrets as mail_secrets
from app.mail import service as mail_service
from app.browser import browser
from app.browser import actions as browser_actions
from app.browser import agent as browser_agent
from app.browser import session as browser_session
from app.browser import store as automation_store
from app.mail import store as mail_store
from app.mail.classify import CATEGORIES, find_code
from app.mail.imap_client import MailError
from app.mail.parser import now_utc_iso
from app.memory.store import (
    delete_note,
    get_session_messages,
    list_notes,
    list_sessions,
    search_messages,
)
from app.notify import notifier
from app.quota.models import get_quota_config
from app.quota.probes import probe_all
from app.quota.state import disable, enable, get_state
from app.quota.tracker import snapshot, usage_by_day
from app.router.selector import describe_chain, get_routing_config
from app.settings import settings

WEB_DIR = Path(__file__).resolve().parent.parent / "web"


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Açılış: şema + arka plan döngüleri. Kapanış: döngüleri düzgün durdur.

    Faz 8'de `on_event("startup")`tan buraya taşındı — hatırlatıcı ve mail
    senkron task'larının kapanışta İPTAL EDİLMESİ gerekiyor, `on_event`in
    shutdown tarafı bunu garanti etmiyor.
    """
    init_db()
    background.start()
    try:
        yield
    finally:
        await background.stop()


app = FastAPI(title="ULL-Bot Orchestrator", lifespan=lifespan)
class _NoCacheStatic(StaticFiles):
    """Arayüz dosyalarını her açılışta doğrula.

    Neden gerekli: WebKit (ve Chromium) `Cache-Control` yokken "heuristic
    freshness" uyguluyor — dosyanın yaşına bakıp sunucuya HİÇ sormadan
    diskten servis ediyor. Sahada şu yaşandı: CSS/JS değiştirildi, uygulama
    yeniden başlatıldı, ekranda ESKİ sürüm çıktı ve sorun kodda sanıldı.

    `no-cache` "önbelleğe alma" demek değil; "kullanmadan önce SOR" demek.
    Dosya değişmediyse 304 dönüyor, yani maliyeti yok.
    """

    def is_not_modified(self, response_headers, request_headers) -> bool:  # type: ignore[override]
        return super().is_not_modified(response_headers, request_headers)

    def file_response(self, *args, **kwargs):  # type: ignore[override]
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


app.mount("/static", _NoCacheStatic(directory=WEB_DIR), name="static")


@app.get("/")
def index() -> FileResponse:
    """Uygulama kabuğu.

    `no-cache` ŞART: bu dosya `/static` altında değil, yani mount'taki
    başlık buraya uygulanmıyor. Başlıksız kaldığında tarayıcı "heuristic
    freshness" ile eski `index.html`i diskten servis ediyor ve kullanıcı,
    sunucu güncel olsa bile ESKİ arayüzü görüyor — sahada tam olarak bu
    yaşandı: yeni kategoriler ve yeni düzen ekrana hiç gelmedi.
    """
    return FileResponse(WEB_DIR / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/theme.css")
def user_theme() -> PlainTextResponse:
    """Kullanıcının kendi teması: `~/.config/ull-bot/theme.css`.

    `index.html` bunu `style.css`ten SONRA yüklüyor, yani buradaki her kural
    varsayılanı ezer. Dosya yoksa boş CSS dönüyoruz — 404 tarayıcı konsolunu
    kirletir ve "bir şey bozuldu" izlenimi verir.

    Neden ayrı bir dosya: uygulamanın kendi `style.css`i güncellemelerde
    değişiyor; kullanıcının değişiklikleri orada durursa her güncellemede
    kaybolur. Ayrıntı: docs/TEMA.md
    """
    path = settings.user_theme_path
    css = ""
    if path.is_file():
        try:
            css = path.read_text(encoding="utf-8")
        except OSError as exc:
            css = f"/* tema okunamadı: {exc} */"
    return PlainTextResponse(css, media_type="text/css", headers={"Cache-Control": "no-cache"})


@app.get("/api/config")
def config() -> dict[str, Any]:
    """UI'ın başlıkta gösterdiği çalışma ayarları."""
    return {
        "profile": settings.profile,
        "model": settings.litellm_model,
        "dry_run": settings.dry_run,
        "workspace_root": str(settings.resolved_workspace_root),
        "max_agent_steps": settings.max_agent_steps,
        # Faz 8: UI hangi özelliklerin kurulu olduğunu buradan öğreniyor.
        "mail_accounts": len(mail_store.list_accounts()),
        "mail_sync_interval": settings.mail_sync_interval_seconds,
        "notifications": {
            "enabled": settings.notifications_enabled,
            "backend": notifier.backend_name(),
            "available": notifier.is_available(),
        },
        "categories": CATEGORIES,
        "default_reminder_minutes": settings.default_reminder_minutes,
        # Ayarlar panelindeki [ TEMA ] bölümü — kullanıcı dosyayı nereye
        # koyacağını uygulamadan görebilsin (bkz. docs/TEMA.md).
        "user_theme_path": str(settings.user_theme_path),
        "user_theme_exists": settings.user_theme_path.is_file(),
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


# --- Faz 7: oturum geçmişi, arama, kullanım grafiği, kalıcı hafıza --------
# UI'sı yok (bilinçli — bkz. NEXT_PHASE.md); bu uçlar sadece veriyi verir.


@app.get("/api/sessions")
def sessions(limit: int = 50) -> dict[str, Any]:
    """Oturum listesi, en yeni önce. `title` boşsa ilk kullanıcı mesajından."""
    return {"sessions": list_sessions(limit=limit)}


@app.get("/api/sessions/{session_id}/messages")
def session_messages(session_id: str) -> dict[str, Any]:
    """Bir oturumun tam kaydı (tool mesajları dahil)."""
    return {"session_id": session_id, "messages": get_session_messages(session_id)}


@app.get("/api/search")
def search(q: str = "", limit: int = 50) -> dict[str, Any]:
    """Tüm oturumlardaki mesajlarda basit metin araması (spec §9 Faz 7)."""
    return {"query": q, "results": search_messages(q, limit=limit)}


@app.get("/api/usage/graph")
def usage_graph(days: int = 14) -> dict[str, Any]:
    """Gün × sağlayıcı kırılımında istek/token sayıları (spec §9 Faz 7

    "kullanım grafiği"). Görselleştirme yapmıyor, veriyi veriyor.
    """
    return {"days": days, "points": usage_by_day(days=days)}


@app.get("/api/memory")
def memory_notes() -> dict[str, Any]:
    """`remember` aracının yazdığı kalıcı notlar (spec §6.2)."""
    return {"notes": list_notes()}


@app.delete("/api/memory/{key}")
def memory_note_delete(key: str) -> dict[str, Any]:
    """Yanlış/eskimiş bir notu silmek için — `remember`in kendisi bir

    "forget" aracı olarak tanımlanmadı (spec §6.2), bu yüzden silme burada,
    bir API/yönetim işlemi olarak duruyor.
    """
    return {"ok": delete_note(key), "key": key}


# --- Faz 8: mail (IMAP) -----------------------------------------------------
# Okuma uçları hep yerel önbellekten döner (hızlı, ağ beklemez); IMAP'e giden
# tek uçlar `sync`, `mark`, `move` ve hesap kurulumu.


def _mail_error(exc: MailError) -> HTTPException:
    return HTTPException(status_code=400, detail=str(exc))


@app.get("/api/mail/accounts")
def mail_accounts() -> dict[str, Any]:
    """Kayıtlı IMAP hesapları. Parola DÖNMEZ — DB'de zaten yok."""
    return {
        "accounts": mail_store.list_accounts(),
        "secret_backend": mail_secrets.available_backend(),
    }


@app.post("/api/mail/accounts/test")
async def mail_account_test(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Bilgileri kaydetmeden dene — 'Bağlantıyı sına' düğmesi."""
    try:
        folders = await mail_service.test_account(
            host=str(payload.get("host") or ""),
            port=int(payload.get("port") or 993),
            username=str(payload.get("username") or ""),
            password=str(payload.get("password") or ""),
            use_ssl=bool(payload.get("use_ssl", True)),
        )
    except MailError as exc:
        raise _mail_error(exc) from exc
    return {"ok": True, "folders": folders}


@app.post("/api/mail/accounts")
async def mail_account_add(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    try:
        account = await mail_service.add_account(
            email=str(payload.get("email") or ""),
            host=str(payload.get("host") or ""),
            port=int(payload.get("port") or 993),
            username=str(payload.get("username") or payload.get("email") or ""),
            password=str(payload.get("password") or ""),
            name=str(payload.get("name") or ""),
            use_ssl=bool(payload.get("use_ssl", True)),
            inbox_folder=str(payload.get("inbox_folder") or "INBOX"),
        )
    except MailError as exc:
        raise _mail_error(exc) from exc
    return {"ok": True, "account": account}


@app.delete("/api/mail/accounts/{account_id}")
async def mail_account_delete(account_id: int) -> dict[str, Any]:
    return {"ok": await mail_service.remove_account(account_id), "account_id": account_id}


@app.get("/api/mail/accounts/{account_id}/folders")
async def mail_account_folders(account_id: int) -> dict[str, Any]:
    try:
        return {"folders": await mail_service.list_folders(account_id)}
    except MailError as exc:
        raise _mail_error(exc) from exc


@app.post("/api/mail/sync")
async def mail_sync(account_id: int | None = None, folder: str | None = None) -> dict[str, Any]:
    """Sunucudan yeni mailleri çek. `account_id` yoksa tüm hesaplar."""
    if account_id is None:
        reports = await mail_service.sync_all()
    else:
        reports = await mail_service.sync_account(account_id, folder)
    return {
        "reports": [report.as_dict() for report in reports],
        "new": sum(report.new for report in reports),
        "counts": mail_store.counts(),
    }


@app.get("/api/mail/messages")
def mail_messages(
    account_id: int | None = None,
    folder: str | None = None,
    category: str | None = None,
    unread: bool = False,
    flagged: bool = False,
    q: str = "",
    limit: int = 100,
    offset: int = 0,
    exclude: str = "",
) -> dict[str, Any]:
    """`exclude`: virgülle ayrılmış kategoriler — "Öncelikli" görünümü bunu
    kullanıyor (reklam ve diğer dışarıda kalsın diye)."""
    excluded = tuple(part.strip() for part in exclude.split(",") if part.strip())
    return {
        "messages": mail_store.list_messages(
            account_id=account_id, folder=folder, category=category,
            unread_only=unread, flagged_only=flagged, query=q,
            limit=max(1, min(limit, 300)), offset=max(0, offset),
            exclude_categories=excluded,
        ),
        "counts": mail_store.counts(account_id),
    }


@app.post("/api/mail/bulk")
async def mail_bulk(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Seçili maillere toplu işlem (tek IMAP bağlantısıyla)."""
    ids = [int(value) for value in (payload.get("ids") or [])]
    if not ids:
        raise HTTPException(status_code=400, detail="Seçili mail yok.")
    try:
        return await mail_service.bulk(
            ids,
            action=str(payload.get("action") or ""),
            category=str(payload.get("category") or ""),
        )
    except MailError as exc:
        raise _mail_error(exc) from exc


@app.get("/api/mail/messages/{message_id}")
def mail_message(message_id: int) -> dict[str, Any]:
    message = mail_store.get_message(message_id)
    if message is None:
        raise HTTPException(status_code=404, detail=f"Mail bulunamadı: {message_id}")
    # Doğrulama kodunu burada çıkarıyoruz, saklamıyoruz: kod tek kullanımlık
    # ve mailin gövdesi zaten elimizde. UI "Kodu kopyala" düğmesini bu alan
    # doluysa gösteriyor.
    message["code"] = find_code(message.get("body_text") or "")
    return message


@app.get("/api/mail/rules")
def mail_rules() -> dict[str, Any]:
    """Kullanıcının özet kuralları (Ayarlar → Mail kuralları)."""
    return {"rules": mail_store.list_rules()}


@app.post("/api/mail/rules")
def mail_rule_add(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    text = str(payload.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Kural metni boş olamaz.")
    if len(text) > 400:
        raise HTTPException(status_code=400, detail="Kural 400 karakterden kısa olmalı.")
    return mail_store.add_rule(text)


@app.patch("/api/mail/rules/{rule_id}")
def mail_rule_toggle(rule_id: int, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    mail_store.set_rule_enabled(rule_id, bool(payload.get("enabled")))
    return {"ok": True, "id": rule_id, "enabled": bool(payload.get("enabled"))}


@app.delete("/api/mail/rules/{rule_id}")
def mail_rule_delete(rule_id: int) -> dict[str, Any]:
    mail_store.delete_rule(rule_id)
    return {"ok": True, "id": rule_id}


@app.post("/api/mail/reclassify")
def mail_reclassify(account_id: int | None = None) -> dict[str, Any]:
    """Önbellekteki mailleri kural motorundan yeniden geçir (LLM yok, bedava).

    Kategori kuralları değiştiğinde gerekiyor; elle seçilmiş kategorilere
    dokunmuyor.
    """
    return mail_service.reclassify_cached(account_id=account_id)


@app.post("/api/mail/messages/{message_id}/mark")
async def mail_mark(message_id: int, payload: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    try:
        return await mail_service.mark(
            message_id, seen=payload.get("seen"), flagged=payload.get("flagged")
        )
    except MailError as exc:
        raise _mail_error(exc) from exc


@app.post("/api/mail/messages/{message_id}/move")
async def mail_move(message_id: int, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    try:
        return await mail_service.move(message_id, str(payload.get("destination") or "__trash__"))
    except MailError as exc:
        raise _mail_error(exc) from exc


@app.post("/api/mail/messages/{message_id}/category")
def mail_category(message_id: int, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    try:
        return mail_service.set_category(message_id, str(payload.get("category") or ""))
    except MailError as exc:
        raise _mail_error(exc) from exc


@app.post("/api/mail/messages/{message_id}/summarize")
async def mail_summarize(message_id: int, force: bool = False) -> dict[str, Any]:
    try:
        return await mail_service.summarize(message_id, force=force)
    except MailError as exc:
        raise _mail_error(exc) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Özet üretilemedi: {exc}") from exc


@app.post("/api/mail/categorize")
async def mail_categorize_batch(limit: int = 15, account_id: int | None = None) -> dict[str, Any]:
    """Kuralın kararsız kaldığı ('diger') mailleri modele sor."""
    return await mail_service.categorize_with_llm(limit=limit, account_id=account_id)


# --- Faz 11: otomasyon ------------------------------------------------------


def _clean_allowlist(raw: Any) -> list[str]:
    """Girilenleri alan adına indir, tekrarları at, sırayı koru."""
    seen: list[str] = []
    for item in raw or []:
        host = browser_actions.normalize_host(str(item))
        if host and host not in seen:
            seen.append(host)
    return seen


@app.get("/api/automations")
def automations() -> dict[str, Any]:
    return {"automations": automation_store.list_automations(),
            "browser": {"running": browser.running, "streaming": browser.streaming}}


@app.post("/api/automations")
def automation_create(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    name = str(payload.get("name") or "").strip() or "Yeni otomasyon"
    return automation_store.create_automation(
        name,
        goal=str(payload.get("goal") or ""),
        start_url=str(payload.get("start_url") or ""),
        allowlist=_clean_allowlist(payload.get("allowlist")),
    )


@app.get("/api/automations/{automation_id}")
def automation_detail(automation_id: int) -> dict[str, Any]:
    automation = automation_store.get_automation(automation_id)
    if automation is None:
        raise HTTPException(status_code=404, detail="Otomasyon bulunamadı.")
    automation["steps"] = automation_store.list_steps(automation_id)
    return automation


@app.patch("/api/automations/{automation_id}")
def automation_update(automation_id: int, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    fields = dict(payload)
    if "allowlist" in fields:
        cleaned = _clean_allowlist(fields["allowlist"])
        if not cleaned:
            # Boş liste "her yer serbest" DEĞİL, "hiçbir yer" demek: kaydetmek
            # otomasyonu sessizce çalışamaz hâle getirir. Kullanıcı satırları
            # silip yeniden yazarken bunu farkında olmadan yapabiliyor, sonra
            # da "izinler silindi" diye karşılaşıyor. Sessizce kabul etmek
            # yerine reddediyoruz.
            raise HTTPException(
                status_code=400,
                detail="En az bir izinli site gerekli — boş liste otomasyonu çalışamaz hâle getirir.",
            )
        fields["allowlist"] = cleaned
    updated = automation_store.update_automation(automation_id, **fields)
    if updated is None:
        raise HTTPException(status_code=404, detail="Otomasyon bulunamadı.")
    return updated


@app.delete("/api/automations/{automation_id}")
def automation_delete(automation_id: int) -> dict[str, Any]:
    automation_store.delete_automation(automation_id)
    return {"ok": True, "id": automation_id}


@app.post("/api/automations/{automation_id}/steps")
def automation_step_add(automation_id: int, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Elle adım ekle (planlayıcı dışında)."""
    intent = str(payload.get("intent") or "").strip()
    if not intent:
        raise HTTPException(status_code=400, detail="Adım metni boş olamaz.")
    return automation_store.add_step(
        automation_id, intent,
        kind=str(payload.get("kind") or "islem"),
        position=payload.get("position"),
    )


@app.post("/api/automations/steps/{step_id}/move")
def automation_step_move(step_id: int, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Adımı bir yukarı (-1) ya da aşağı (+1) taşı."""
    return {"steps": automation_store.move_step(step_id, int(payload.get("delta") or 0))}


@app.patch("/api/automations/steps/{step_id}")
def automation_step_update(step_id: int, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Adımı düzenle.

    `intent` değişirse kayıtlı `action` DÜŞÜRÜLÜR: cümle artık başka bir şey
    anlatıyor, eski somut eylem ona ait değil. Yoksa kullanıcı adımı
    düzeltiyor ama ajan eskisini yapmaya devam ederdi.
    """
    fields = dict(payload)
    if "intent" in fields:
        fields["action"] = None
        fields["status"] = "bekliyor"
        fields["last_error"] = None
    automation_store.update_step(step_id, **fields)
    return {"ok": True, "id": step_id}


@app.delete("/api/automations/steps/{step_id}")
def automation_step_delete(step_id: int) -> dict[str, Any]:
    automation_store.delete_step(step_id)
    return {"ok": True, "id": step_id}


@app.post("/api/browser/login")
async def browser_login() -> dict[str, Any]:
    """Görünür bir pencere aç: kullanıcı Gmail'e bir kez giriş yapsın.

    Headless Chrome'da Google girişi çoğu zaman engelleniyor ("bu tarayıcı
    güvenli olmayabilir"). Bu yüzden giriş, AYNI profille açılan gerçek bir
    pencerede yapılıyor; sonrasında otomasyon o oturumu kullanıyor.
    """
    await browser.stop()
    await browser.start(headless=False)
    await browser.send("Page.navigate", {"url": "https://accounts.google.com/"})
    return {"ok": True, "detail": "Giriş penceresi açıldı. Girişi yapıp pencereyi kapatabilirsin."}


# --- Faz 8: takvim ----------------------------------------------------------


@app.get("/api/calendar/events")
def calendar_events(start: str = "", end: str = "", q: str = "", limit: int = 500) -> dict[str, Any]:
    return {
        "events": calendar_store.list_events(start=start, end=end, query=q, limit=limit),
        "stats": calendar_store.stats(),
    }


@app.get("/api/calendar/upcoming")
def calendar_upcoming(limit: int = 10, days: int = 30) -> dict[str, Any]:
    return {"events": calendar_store.upcoming(limit=limit, within_days=days)}


@app.post("/api/calendar/events")
def calendar_create(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    try:
        return calendar_store.create_event(
            title=str(payload.get("title") or ""),
            starts_at=str(payload.get("starts_at") or ""),
            ends_at=str(payload.get("ends_at") or ""),
            description=str(payload.get("description") or ""),
            location=str(payload.get("location") or ""),
            meeting_url=str(payload.get("meeting_url") or ""),
            attendees=list(payload.get("attendees") or []),
            all_day=bool(payload.get("all_day")),
            reminder_minutes=payload.get("reminder_minutes"),
            color=str(payload.get("color") or ""),
            source=str(payload.get("source") or "manual"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.patch("/api/calendar/events/{event_id}")
def calendar_update(event_id: int, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    event = calendar_store.update_event(event_id, **payload)
    if event is None:
        raise HTTPException(status_code=404, detail=f"Etkinlik bulunamadı: {event_id}")
    return event


@app.delete("/api/calendar/events/{event_id}")
def calendar_delete(event_id: int) -> dict[str, Any]:
    return {"ok": calendar_store.delete_event(event_id), "id": event_id}


@app.get("/api/calendar/export.ics")
def calendar_export(start: str = "", end: str = "") -> PlainTextResponse:
    """Takvimi ICS olarak dışa aktar — başka bir takvim uygulamasına almak için."""
    return PlainTextResponse(
        calendar_service.export_ics(start=start, end=end),
        media_type="text/calendar; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="ull-bot.ics"'},
    )


@app.post("/api/calendar/import")
def calendar_import(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    events = calendar_service.import_ics(str(payload.get("ics") or ""))
    return {"imported": len(events), "events": events}


@app.get("/api/calendar/pending-meetings")
def calendar_pending(limit: int = 30) -> dict[str, Any]:
    """`toplanti` kategorisinde olup henüz takvime alınmamış mailler."""
    return {"pending": calendar_service.scan_meeting_mails(limit=limit)}


@app.get("/api/calendar/draft-from-mail/{message_id}")
def calendar_draft(message_id: int) -> dict[str, Any]:
    """Maildeki toplantıyı KAYDETMEDEN çıkar — onay ekranı için."""
    try:
        return calendar_service.draft_from_mail(message_id).as_dict()
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/calendar/from-mail/{message_id}")
def calendar_from_mail(message_id: int, payload: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    """Maildeki toplantıyı takvime kaydet."""
    try:
        draft = calendar_service.draft_from_mail(message_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    # UI onay ekranında düzelttiyse taslağın üstüne yaz.
    for field in ("title", "starts_at", "ends_at", "location", "description", "meeting_url"):
        if payload.get(field):
            setattr(draft, field, str(payload[field]))
    if not draft.starts_at:
        raise HTTPException(
            status_code=400,
            detail=f"Bu mailde tarih/saat bulunamadı ({draft.reason}). Zamanı elle gir.",
        )
    try:
        return calendar_service.save_draft(draft, reminder_minutes=payload.get("reminder_minutes"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# --- Faz 8: bildirimler -----------------------------------------------------


@app.post("/api/open-external")
def open_external(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Bir adresi sistem tarayıcısında aç.

    Neden gerekli: uygulama native bir WebKit penceresi; içindeki
    `<a target="_blank">` hiçbir şey yapmaz. Google'ın uygulama parolası
    sayfası, maildeki bağlantılar ve takvimdeki toplantı bağlantıları
    dışarıda açılmalı.

    Kapsam bilinçli olarak dar: yalnızca `http`/`https`. Bu uç, uygulamanın
    kendi arayüzünden çağrılıyor ama yine de "her şeyi çalıştır" bir
    kapıya dönüşmemeli — `file://`, `javascript:` ya da rastgele bir komut
    buradan geçemez.
    """
    raw = str(payload.get("url") or "").strip()
    parsed = urlparse(raw)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise HTTPException(
            status_code=400, detail=f"Yalnızca http/https adresleri açılabilir: {raw[:120]!r}"
        )

    opener = shutil.which("xdg-open") or shutil.which("open")
    if opener is None:
        raise HTTPException(
            status_code=501,
            detail="Sistemde `xdg-open` bulunamadı — adresi elle kopyalaman gerekiyor.",
        )
    try:
        subprocess.Popen(
            [opener, raw],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
        )
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Tarayıcı açılamadı: {exc}") from exc
    return {"ok": True, "url": raw}


@app.get("/api/notifications")
def notifications_status() -> dict[str, Any]:
    now = datetime.now(local_tz())
    return {
        "enabled": settings.notifications_enabled,
        "backend": notifier.backend_name(),
        "available": notifier.is_available(),
        "poll_seconds": settings.calendar_poll_seconds,
        "pending": [
            {
                "id": event["id"], "title": event["title"], "starts_at": event["starts_at"],
                "reminder_minutes": event["reminder_minutes"],
            }
            for event in calendar_store.list_events(
                start=now.isoformat(), end=(now + timedelta(days=2)).isoformat(), limit=50
            )
            if event.get("reminded_at") is None and int(event.get("reminder_minutes") or 0) >= 0
        ],
    }


@app.post("/api/notifications/test")
def notifications_test() -> dict[str, Any]:
    result = notifier.notify(
        "ULL-Bot bildirim testi",
        "Bunu gördüysen hatırlatmalar çalışacak demektir.",
        urgency="normal",
        timeout_ms=6000,
        replace_key="ull-bot-test",
    )
    return {"ok": result.ok, "backend": result.backend, "detail": result.detail}


@app.websocket("/ws/browser")
async def ws_browser(websocket: WebSocket) -> None:
    """Otomasyon görünümünün tek kanalı.

    Sunucu → UI: `frame` (canlı ekran), `steps`, `step_update`, `state`,
    `approval_request`, `log`, `error`.
    UI → sunucu: `start`, `plan`, `run`, `stop`, `input`, `approval_response`.

    Girdi iletimi (`input`) kullanıcının canlı görüntü üzerinde kendi eliyle
    tıklayıp yazabilmesi için: giriş yapmak, bir menüyü açmak ya da ajanın
    takıldığı yerden devam ettirmek gerekiyor.
    """
    await websocket.accept()
    send_lock = asyncio.Lock()
    stop_flag = {"value": False}
    pending_approval: dict[str, asyncio.Future[bool]] = {}

    async def emit(event: dict[str, Any]) -> None:
        try:
            async with send_lock:
                await websocket.send_json(event)
        except (WebSocketDisconnect, RuntimeError):
            pass

    async def on_frame(data: str) -> None:
        await emit({"type": "frame", "data": data})

    async def on_closed() -> None:
        # Kullanıcı gerçek pencereyi kapattı: panel son kareyi göstermeye
        # devam etmesin, "kapalı" desin.
        await emit({"type": "state", "running": False, "closed": True})

    browser.on_frame(on_frame)
    browser.on_closed(on_closed)

    async def approve(action_desc: str, step_intent: str) -> bool:
        request_id = uuid.uuid4().hex
        future: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
        pending_approval[request_id] = future
        await emit({"type": "approval_request", "id": request_id,
                    "action": action_desc, "intent": step_intent})
        try:
            return await asyncio.wait_for(future, timeout=settings.approval_timeout_seconds)
        except asyncio.TimeoutError:
            return False
        finally:
            pending_approval.pop(request_id, None)

    async def prepare(automation: dict[str, Any]) -> None:
        """Tarayıcıyı ve doğru sekmeyi otomasyon için hazırla.

        Kullanıcı "sayfa açık değilse bile sayfayı açsın" dedi: eskiden
        planlama, tarayıcının önceden açılmış olmasını bekliyordu ve
        "Önce tarayıcıyı aç" diyordu. Ayrıca birden fazla sekme açık
        olabiliyor; `open_for` aynı siteden bir sekme varsa ona geçiyor,
        yoksa yeni sekme açıyor.
        """
        await browser.start(headless=False)
        # Bayrağa BAKMIYORUZ: `start_stream` zaten "zaten açık mı, döngü
        # canlı mı" diye kontrol ediyor. Dışarıda ayrıca bayrağa bakmak,
        # döngü ölmüşken bayrak açık kaldığında aynanın hiç açılmamasına
        # yol açıyordu.
        await browser.start_stream()
        await browser.open_for(automation.get("start_url") or "")
        await emit({"type": "tabs", "tabs": await browser.tabs()})

    async def run_automation(automation_id: int, step_by_step: bool,
                             *, only: int | None = None, start_at: int = 0) -> None:
        automation = automation_store.get_automation(automation_id)
        if automation is None:
            await emit({"type": "error", "message": "Otomasyon bulunamadı."})
            return
        steps = automation_store.list_steps(automation_id)
        if not steps:
            await emit({"type": "error", "message": "Önce bir plan çıkar."})
            return
        # Kullanıcı bir adımı tek başına deneyebilsin ya da düzelttiği
        # adımdan devam edebilsin: "bir adım geri gidip revize edip devam
        # ettirebilelim" (kullanıcının isteği).
        if only is not None:
            steps = [step for step in steps if step["id"] == int(only)]
        elif start_at:
            steps = [step for step in steps if step["position"] >= start_at]

        allowlist = automation["allowlist"]
        if not allowlist:
            await emit({"type": "error", "message": (
                "Bu otomasyonun izinli sitesi yok. ⚙ ayarlarından en az bir site ekle "
                "(örn. mail.google.com) — ajan yalnızca listedeki sitelerde çalışabilir."
            )})
            await emit({"type": "run_done", "status": "hata"})
            return
        await prepare(automation)
        run_id = automation_store.start_run(automation_id)
        log: list[dict[str, Any]] = []
        status = "tamam"

        for step in steps:
            if stop_flag["value"]:
                status = "yarida"
                break
            automation_store.update_step(step["id"], status="calisiyor", last_error=None)
            await emit({"type": "step_update", "id": step["id"], "status": "calisiyor"})
            try:
                # İlerleme günlüğü: kullanıcı "neler yaptığını, nelerde
                # uğraştığını görebilelim" dedi. Takıldığında hangi aşamada
                # takıldığı da buradan anlaşılıyor.
                await emit({"type": "log", "text": f"Sayfa okunuyor — {step['intent'][:60]}"})
                action, state, asked_model = await browser_agent.decide(
                    step, browser, allowlist=allowlist,
                    session_id=f"otomasyon-{automation_id}",
                    on_progress=lambda text: emit({"type": "log", "text": text}),
                )
                await emit({"type": "log", "text": (
                    f"Eylem: {action.describe()}"
                    + (" (model soruldu)" if asked_model else " (kayıtlı eylem)")
                )})
                # Onay: ilk çalıştırmada her adım, sonrasında yalnızca geri
                # alınamaz eylemler (kullanıcının kararı).
                if step_by_step or action.is_irreversible(state):
                    if not await approve(action.describe(), step["intent"]):
                        automation_store.update_step(step["id"], status="atlandi")
                        await emit({"type": "step_update", "id": step["id"], "status": "atlandi"})
                        log.append({"step": step["intent"], "sonuc": "kullanıcı atladı"})
                        continue

                detail = await browser_actions.run_action(browser, action, allowlist=allowlist)
                await emit({"type": "log", "text": f"Sonuç: {detail[:120]}"})
                automation_store.update_step(
                    step["id"], status="tamam", action=step.get("action"), last_error=None
                )
                await emit({"type": "step_update", "id": step["id"], "status": "tamam",
                            "detail": detail, "model": asked_model})
                log.append({"step": step["intent"], "eylem": action.describe(),
                            "sonuc": detail[:400], "model": asked_model})
            except Exception as exc:
                message = str(exc)
                if isinstance(exc, browser_session.BlockedHost):
                    # Beyaz liste engeli: kullanıcıya tek tıkla izin verme
                    # yolu sun. Ayarları açıp elle yazmak akışı kopartıyordu.
                    await emit({"type": "blocked", "url": exc.url,
                                "host": exc.host, "allowlist": exc.allowlist})
                await emit({"type": "log", "text": f"HATA: {message[:160]}", "level": "err"})
                automation_store.update_step(step["id"], status="hata", last_error=message[:400])
                await emit({"type": "step_update", "id": step["id"], "status": "hata",
                            "detail": message})
                log.append({"step": step["intent"], "hata": message[:400]})
                status = "hata"
                break

        automation_store.finish_run(run_id, status, log)
        automation_store.update_automation(
            automation_id, last_run_at=now_utc_iso(), last_status=status
        )
        # Bitişte adımların SON hâli de gidiyor: kullanıcı "bitirdiğini
        # fark etmedim, sayfa yenilenmedi" dedi — liste ekranda eski
        # durumuyla kalıyordu.
        await emit({
            "type": "run_done",
            "status": status,
            "steps": automation_store.list_steps(automation_id),
            "done": sum(1 for row in log if "sonuc" in row or "eylem" in row),
            "total": len(steps),
        })

    task: asyncio.Task | None = None
    try:
        while True:
            data = await websocket.receive_json()
            kind = data.get("type")

            if kind == "approval_response":
                future = pending_approval.get(str(data.get("id")))
                if future is not None and not future.done():
                    future.set_result(bool(data.get("approved")))
                continue

            if kind == "start":
                # Beyaz liste İSTEMCİDEN GELMEZ, kayıttan okunur.
                #
                # Güvenlik kararını istemciye sormak baştan yanlıştı: bozuk
                # bir istemci boş liste gönderip her şeyi kilitliyor (canlı
                # yaşandı — "mail.google.com izinli değil" derken kayıtta
                # izinliydi), kötü niyetli biri ise sonsuz geniş liste
                # gönderip sınırı tamamen kaldırabilirdi.
                automation = automation_store.get_automation(
                    int(data.get("automation_id") or 0)
                ) or {}
                allowlist = automation.get("allowlist") or []
                # Gerçek pencere: kullanıcı "tam çalışan bir tarayıcı" istedi.
                # Görünür pencerede hem kendisi gezinebiliyor hem de Google
                # girişi engellenmiyor; ekran akışı yine panele düşüyor.
                await browser.start(headless=bool(data.get("headless", False)))
                # Başlangıç adresi ve sekme seçimi `prepare` içinde: aynı
                # siteden sekme varsa ona geçiyor, yoksa açıyor.
                await prepare(automation)
                url = str(data.get("url") or "")
                if url and browser_actions.host_allowed(url, allowlist):
                    await browser.send("Page.navigate", {"url": url})
                elif url:
                    from urllib.parse import urlparse as _parse
                    await emit({
                        "type": "blocked",
                        "url": url,
                        "host": (_parse(url).hostname or ""),
                        "allowlist": allowlist,
                    })
                await emit({"type": "state", "running": True})
                continue

            if kind == "stop":
                stop_flag["value"] = True
                if task and not task.done():
                    task.cancel()
                await emit({"type": "state", "running": browser.running, "stopped": True})
                continue

            if kind == "tabs":
                await emit({"type": "tabs", "tabs": await browser.tabs()})
                continue

            if kind == "focus_tab":
                await browser.focus_tab(str(data.get("target_id") or ""))
                await emit({"type": "tabs", "tabs": await browser.tabs()})
                continue

            if kind == "allow_host":
                # Engellenen adresi kullanıcı onayıyla listeye ekle.
                #
                # Güvenlik sınırı korunuyor: ekleyen İNSAN, ajan değil.
                # Eskiden kullanıcı her engelde ayarları açıp elle yazmak
                # zorundaydı ve akış kopuyordu.
                automation_id = int(data.get("automation_id") or 0)
                automation = automation_store.get_automation(automation_id) or {}
                host = str(data.get("host") or "").strip().casefold()
                if host:
                    allowlist = list(automation.get("allowlist") or [])
                    if host not in allowlist:
                        allowlist.append(host)
                    automation_store.update_automation(automation_id, allowlist=allowlist)
                    await emit({"type": "allowlist", "allowlist": allowlist})
                continue

            if kind == "input":
                # Kullanıcının canlı görüntü üzerindeki kendi tıklaması/yazması.
                await _forward_input(data)
                continue

            if kind == "plan":
                automation_id = int(data.get("automation_id"))
                automation = automation_store.get_automation(automation_id) or {}
                goal = str(data.get("goal") or automation.get("goal") or "")
                try:
                    await prepare(automation)
                    state = await browser.state()
                    steps = await browser_agent.plan(goal, state,
                                                     session_id=f"otomasyon-{automation_id}")
                    automation_store.update_automation(automation_id, goal=goal)
                    saved = automation_store.replace_steps(automation_id, steps)
                    await emit({"type": "steps", "steps": saved})
                except Exception as exc:
                    await emit({"type": "error", "message": str(exc)})
                continue

            if kind == "run":
                stop_flag["value"] = False
                automation_id = int(data.get("automation_id"))
                automation_store.reset_steps(automation_id)
                await emit({"type": "steps",
                            "steps": automation_store.list_steps(automation_id)})
                async def guarded(aid: int = automation_id,
                                  step_by_step: bool = bool(data.get("step_by_step", True)),
                                  only: int | None = data.get("only_step"),
                                  start_at: int = int(data.get("from_step") or 0)) -> None:
                    # Çalıştırma bir istisnayla düşerse UI sonsuza kadar
                    # "çalışıyor" görünüyordu; bitiş her hâlükârda bildirilir.
                    try:
                        await run_automation(aid, step_by_step, only=only, start_at=start_at)
                    except asyncio.CancelledError:
                        await emit({"type": "run_done", "status": "durduruldu"})
                    except Exception as exc:
                        await emit({"type": "error", "message": f"Çalıştırma düştü: {exc}"})
                        await emit({"type": "run_done", "status": "hata"})

                task = asyncio.create_task(guarded())
                continue
    except WebSocketDisconnect:
        pass
    finally:
        browser.off_frame(on_frame)
        browser.off_closed(on_closed)
        if task and not task.done():
            task.cancel()


async def _forward_input(data: dict[str, Any]) -> None:
    """UI'daki canlı görüntüden gelen fare/klavye olayını tarayıcıya ilet."""
    kind = str(data.get("event") or "")
    if kind == "click":
        for phase in ("mousePressed", "mouseReleased"):
            await browser.send("Input.dispatchMouseEvent", {
                "type": phase, "x": int(data.get("x", 0)), "y": int(data.get("y", 0)),
                "button": "left", "clickCount": 1,
            })
    elif kind == "text":
        for char in str(data.get("text") or ""):
            await browser.send("Input.dispatchKeyEvent", {"type": "char", "text": char})
    elif kind == "key":
        await browser_actions._press(browser, str(data.get("key") or ""))
    elif kind == "scroll":
        await browser.evaluate(f"window.scrollBy(0, {int(data.get('dy', 0))})")


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
