"""Arka plan döngüleri: hatırlatıcı ve mail senkronu (Faz 8).

FastAPI açılışında başlar, kapanışında iptal edilir. İkisi de ayrı task,
çünkü periyotları farklı (hatırlatıcı 30 sn, mail 5 dk) ve biri hata alsa
diğeri durmamalı.

Tasarım kuralı: **bu döngüler hiçbir zaman istisna sızdırmaz.** Bir mail
sunucusu erişilemez olduğunda ya da bildirim daemon'ı ölü olduğunda
uvicorn'un event loop'una istisna gitmesi, tüm uygulamayı sessizce
bozabilir. Her tur `try/except` ile sarılı; hata bir kere loglanır ve
döngü devam eder.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from app.calendar import store as calendar_store
from app.calendar.store import local_tz
from app.notify import notify
from app.settings import settings

logger = logging.getLogger("ull-bot.background")

# İlk mail senkronu için kısa bir gecikme: uygulama açılışında UI'ın ilk
# isteklerinin önüne geçmesin.
FIRST_SYNC_DELAY = 8


def _format_when(starts_at: str) -> str:
    """Bildirim gövdesindeki "14 dakika sonra, 15:00" ifadesi."""
    try:
        starts = datetime.fromisoformat(starts_at)
    except (TypeError, ValueError):
        return ""
    now = datetime.now(local_tz())
    minutes = int((starts - now).total_seconds() // 60)
    clock = starts.strftime("%H:%M")
    if minutes < 0:
        return f"{clock} — başladı"
    if minutes == 0:
        return f"{clock} — şimdi"
    if minutes < 60:
        return f"{clock} — {minutes} dakika sonra"
    hours = minutes // 60
    return f"{starts.strftime('%d.%m %H:%M')} — {hours} saat sonra"


def fire_reminder(event: dict) -> bool:
    """Tek bir etkinliğin bildirimini gönder ve işaretle."""
    body_parts = [_format_when(event.get("starts_at", ""))]
    if event.get("location"):
        body_parts.append(f"📍 {event['location']}")
    if event.get("meeting_url"):
        body_parts.append(f"🔗 {event['meeting_url']}")
    if event.get("description"):
        first_line = str(event["description"]).strip().splitlines()[0]
        if first_line and not first_line.startswith("Kaynak mail:"):
            body_parts.append(first_line[:120])

    result = notify(
        event.get("title") or "Takvim hatırlatması",
        "\n".join(part for part in body_parts if part),
        urgency="critical",
        icon="appointment-soon",
        replace_key=f"ull-bot-event-{event.get('id')}",
    )
    # Bildirim gönderilemese bile işaretliyoruz: aksi hâlde bildirim daemon'ı
    # kapalıyken döngü her 30 saniyede aynı etkinliği tekrar tekrar denerdi.
    calendar_store.mark_reminded(int(event["id"]))
    if not result.ok:
        logger.warning("Bildirim gönderilemedi (%s): %s", result.backend, result.detail)
    return result.ok


async def reminder_loop() -> None:
    """Yaklaşan etkinlikler için OS bildirimi gönder."""
    interval = max(10, settings.calendar_poll_seconds)
    logger.info("Hatırlatıcı döngüsü başladı (%s sn aralık).", interval)
    while True:
        try:
            due = await asyncio.to_thread(calendar_store.due_reminders)
            for event in due:
                await asyncio.to_thread(fire_reminder, event)
                if due:
                    logger.info("Hatırlatma gönderildi: %s", event.get("title"))
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Hatırlatıcı döngüsünde hata — döngü devam ediyor.")
        await asyncio.sleep(interval)


async def mail_sync_loop() -> None:
    """Kayıtlı IMAP hesaplarını periyodik olarak senkronla."""
    interval = settings.mail_sync_interval_seconds
    if interval <= 0:
        logger.info("Otomatik mail senkronu kapalı (MAIL_SYNC_INTERVAL_SECONDS=0).")
        return

    # Döngü içi import: `service` modülü LiteLLM istemcisini de çekiyor,
    # açılış sırasını gereksiz yere ağırlaştırmasın.
    from app.mail import service as mail_service
    from app.mail import store as mail_store

    await asyncio.sleep(FIRST_SYNC_DELAY)
    logger.info("Mail senkron döngüsü başladı (%s sn aralık).", interval)

    while True:
        try:
            if mail_store.list_accounts():
                reports = await mail_service.sync_all()
                new_total = sum(report.new for report in reports)
                errors = [report.error for report in reports if report.error]
                if new_total:
                    logger.info("Mail senkronu: %s yeni mesaj.", new_total)
                    await _notify_new_mail(new_total)
                for error in errors:
                    logger.warning("Mail senkron hatası: %s", error)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Mail senkron döngüsünde hata — döngü devam ediyor.")
        await asyncio.sleep(interval)


async def _notify_new_mail(count: int) -> None:
    """Yeni mail bildirimi — düşük aciliyet, hatırlatmaların önüne geçmesin."""
    from app.mail import store as mail_store

    unread = mail_store.counts().get("unread", 0)
    await asyncio.to_thread(
        notify,
        f"{count} yeni mail",
        f"Okunmamış toplam: {unread}",
        urgency="low",
        icon="mail-unread",
        replace_key="ull-bot-new-mail",
    )


class BackgroundTasks:
    """Açılış/kapanışta döngüleri yöneten küçük tutucu."""

    def __init__(self) -> None:
        self._tasks: list[asyncio.Task] = []

    def start(self) -> None:
        if self._tasks:
            return
        self._tasks = [
            asyncio.create_task(reminder_loop(), name="ull-bot-reminders"),
            asyncio.create_task(mail_sync_loop(), name="ull-bot-mail-sync"),
        ]

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        self._tasks = []


background = BackgroundTasks()
