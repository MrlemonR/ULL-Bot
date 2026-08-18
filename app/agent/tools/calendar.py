"""Takvim araçları — sohbetten toplantı oluşturmak ve görmek için.

**Risk modeli.** Bu araçların hepsi uygulamanın KENDİ SQLite'ına yazıyor,
kullanıcının dosya sistemine ya da dış bir servise değil. Bu, `remember`
aracıyla birebir aynı kategori (bkz. `tools/memory.py`) — o da `safe` ve o
da dry-run'ın konusu değil. Dry-run'ın koruduğu şey "ajanın makineyi
değiştirmesi"; kendi takvimine bir satır yazmak o değil.

Tek istisna `delete_event`: silinen bir etkinlik geri gelmiyor (çöp kutusu
henüz yok — bkz. NEXT_PHASE.md "sıradaki iş b"), o yüzden `confirm`.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from app.agent.tools.base import Tool, ToolContext, ToolPreview, ToolResult, register
from app.calendar import service as calendar_service
from app.calendar import store as calendar_store
from app.calendar.store import local_tz, normalize_dt
from app.safety.policy import Decision


def _format_event(event: dict[str, Any]) -> str:
    try:
        starts = datetime.fromisoformat(event["starts_at"])
        when = starts.strftime("%d.%m.%Y %a %H:%M")
    except (KeyError, TypeError, ValueError):
        when = str(event.get("starts_at", "?"))
    if event.get("all_day"):
        when = when.split()[0] + " (tüm gün)"

    parts = [f"#{event['id']} {when} — {event.get('title')}"]
    if event.get("location"):
        parts.append(f"    yer: {event['location']}")
    if event.get("meeting_url"):
        parts.append(f"    bağlantı: {event['meeting_url']}")
    if event.get("attendees"):
        parts.append(f"    katılımcı: {', '.join(event['attendees'][:5])}")
    reminder = event.get("reminder_minutes")
    if reminder is not None and int(reminder) >= 0:
        parts.append(f"    hatırlatma: {reminder} dk önce")
    return "\n".join(parts)


class ListEvents(Tool):
    name = "list_events"
    description = (
        "Takvimdeki etkinlikleri listele. Varsayılan: bugünden itibaren 7 gün. "
        "Kullanıcı 'bugün ne var', 'bu hafta toplantım var mı' dediğinde bunu kullan."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "start": {"type": "string", "description": "Başlangıç (YYYY-MM-DD ya da ISO8601). Boşsa bugün."},
            "days": {"type": "integer", "description": "Kaç günlük aralık (varsayılan 7)."},
            "query": {"type": "string", "description": "Başlık/açıklama/yer içinde arama."},
        },
    }
    risk = "safe"

    def assess(self, ctx: ToolContext, **kwargs: Any) -> Decision:
        return Decision("safe", "Takvimi okur.", "calendar-read")

    def preview(self, ctx: ToolContext, **kwargs: Any) -> ToolPreview:
        return ToolPreview(summary="Takvimi listele")

    def run(self, ctx: ToolContext, **kwargs: Any) -> ToolResult:
        now = datetime.now(local_tz())
        start_raw = str(kwargs.get("start") or "")
        start = datetime.fromisoformat(normalize_dt(start_raw)) if normalize_dt(start_raw) else now
        days = max(1, min(int(kwargs.get("days") or 7), 365))
        end = start + timedelta(days=days)

        events = calendar_store.list_events(
            start=start.isoformat(), end=end.isoformat(),
            query=str(kwargs.get("query") or ""), limit=200,
        )
        window = f"{start.strftime('%d.%m.%Y')} – {end.strftime('%d.%m.%Y')}"
        if not events:
            return ToolResult(True, f"{window} arasında etkinlik yok.", untrusted=False, meta={"count": 0})
        return ToolResult(
            True,
            f"{window} arasında {len(events)} etkinlik:\n\n"
            + "\n".join(_format_event(event) for event in events),
            untrusted=False,
            meta={"count": len(events)},
        )


class CreateEvent(Tool):
    name = "create_event"
    description = (
        "Takvime yeni bir etkinlik/toplantı ekle. Zamanı MUTLAKA açık yaz "
        "(YYYY-MM-DDTHH:MM). 'yarın', 'gelecek salı' gibi ifadeleri kendin "
        "bugünün tarihine göre hesapla — sistem promptunda bugünün tarihi var."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "Etkinlik başlığı."},
            "starts_at": {"type": "string", "description": "Başlangıç, ISO8601 (örn. 2026-08-20T15:00)."},
            "ends_at": {"type": "string", "description": "Bitiş. Boşsa +1 saat."},
            "description": {"type": "string", "description": "Notlar."},
            "location": {"type": "string", "description": "Yer."},
            "meeting_url": {"type": "string", "description": "Meet/Zoom/Teams bağlantısı."},
            "attendees": {
                "type": "array", "items": {"type": "string"},
                "description": "Katılımcı e-posta adresleri.",
            },
            "all_day": {"type": "boolean", "description": "Tüm gün süren etkinlik mi."},
            "reminder_minutes": {
                "type": "integer",
                "description": "Kaç dakika önce masaüstü bildirimi gelsin. -1 = hatırlatma yok.",
            },
        },
        "required": ["title", "starts_at"],
    }
    # `remember` ile aynı kategori: uygulamanın kendi verisine yazıyor.
    risk = "safe"

    def assess(self, ctx: ToolContext, **kwargs: Any) -> Decision:
        if not normalize_dt(str(kwargs.get("starts_at") or "")):
            return Decision(
                "blocked",
                f"Başlangıç zamanı anlaşılamadı: {kwargs.get('starts_at')!r}. "
                "ISO8601 bekleniyor (2026-08-20T15:00).",
                "calendar-bad-date",
            )
        return Decision("safe", "Uygulamanın kendi takvimine yazar.", "calendar-write")

    def preview(self, ctx: ToolContext, **kwargs: Any) -> ToolPreview:
        return ToolPreview(
            summary=f"Takvime ekle: {kwargs.get('title')} @ {kwargs.get('starts_at')}"
        )

    def run(self, ctx: ToolContext, **kwargs: Any) -> ToolResult:
        try:
            event = calendar_store.create_event(
                title=str(kwargs.get("title") or ""),
                starts_at=str(kwargs.get("starts_at") or ""),
                ends_at=str(kwargs.get("ends_at") or ""),
                description=str(kwargs.get("description") or ""),
                location=str(kwargs.get("location") or ""),
                meeting_url=str(kwargs.get("meeting_url") or ""),
                attendees=list(kwargs.get("attendees") or []),
                all_day=bool(kwargs.get("all_day")),
                reminder_minutes=kwargs.get("reminder_minutes"),
                source="agent",
            )
        except ValueError as exc:
            return ToolResult(False, str(exc), untrusted=False)
        return ToolResult(
            True, "Takvime eklendi:\n" + _format_event(event), untrusted=False,
            meta={"id": event["id"]},
        )


class UpdateEvent(Tool):
    name = "update_event"
    description = "Var olan bir etkinliği güncelle (id ile). Sadece verilen alanlar değişir."
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "id": {"type": "integer", "description": "Etkinlik id'si."},
            "title": {"type": "string"},
            "starts_at": {"type": "string", "description": "Yeni başlangıç, ISO8601."},
            "ends_at": {"type": "string"},
            "description": {"type": "string"},
            "location": {"type": "string"},
            "meeting_url": {"type": "string"},
            "reminder_minutes": {"type": "integer"},
        },
        "required": ["id"],
    }
    risk = "safe"

    def assess(self, ctx: ToolContext, **kwargs: Any) -> Decision:
        if calendar_store.get_event(int(kwargs.get("id") or 0)) is None:
            return Decision("blocked", f"Etkinlik bulunamadı: #{kwargs.get('id')}", "calendar-missing")
        return Decision("safe", "Uygulamanın kendi takvimini günceller.", "calendar-write")

    def preview(self, ctx: ToolContext, **kwargs: Any) -> ToolPreview:
        return ToolPreview(summary=f"Etkinlik #{kwargs.get('id')} güncelle")

    def run(self, ctx: ToolContext, **kwargs: Any) -> ToolResult:
        fields = {key: value for key, value in kwargs.items() if key != "id"}
        event = calendar_store.update_event(int(kwargs.get("id") or 0), **fields)
        if event is None:
            return ToolResult(False, f"Etkinlik bulunamadı: #{kwargs.get('id')}", untrusted=False)
        return ToolResult(True, "Güncellendi:\n" + _format_event(event), untrusted=False)


class DeleteEvent(Tool):
    name = "delete_event"
    description = "Bir etkinliği takvimden sil. Geri alınamaz, kullanıcı onayı ister."
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {"id": {"type": "integer", "description": "Etkinlik id'si."}},
        "required": ["id"],
    }
    # Geri alınamaz: çöp kutusu henüz yok (NEXT_PHASE.md "sıradaki iş b").
    risk = "confirm"
    writes = True

    def assess(self, ctx: ToolContext, **kwargs: Any) -> Decision:
        if calendar_store.get_event(int(kwargs.get("id") or 0)) is None:
            return Decision("blocked", f"Etkinlik bulunamadı: #{kwargs.get('id')}", "calendar-missing")
        return Decision("confirm", "Etkinlik kalıcı olarak silinecek.", "calendar-delete")

    def preview(self, ctx: ToolContext, **kwargs: Any) -> ToolPreview:
        event = calendar_store.get_event(int(kwargs.get("id") or 0))
        return ToolPreview(
            summary=f"Etkinliği sil: {event.get('title') if event else kwargs.get('id')}",
            detail=_format_event(event) if event else "",
        )

    def run(self, ctx: ToolContext, **kwargs: Any) -> ToolResult:
        deleted = calendar_store.delete_event(int(kwargs.get("id") or 0))
        if not deleted:
            return ToolResult(False, f"Etkinlik bulunamadı: #{kwargs.get('id')}", untrusted=False)
        return ToolResult(True, f"Etkinlik #{kwargs.get('id')} silindi.", untrusted=False)


class MailToEvent(Tool):
    name = "mail_to_event"
    description = (
        "Bir maildeki toplantıyı takvime ekle. Mailde takvim daveti (ICS) varsa "
        "tarih tam olarak okunur; yoksa metinden tahmin edilir. Kullanıcı 'bu "
        "toplantıyı takvime ekle' dediğinde bunu kullan."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "mail_id": {"type": "integer", "description": "list_mail'in verdiği mail id'si."},
            "reminder_minutes": {"type": "integer", "description": "Kaç dakika önce bildirim gelsin."},
        },
        "required": ["mail_id"],
    }
    risk = "safe"

    def assess(self, ctx: ToolContext, **kwargs: Any) -> Decision:
        return Decision("safe", "Maildeki toplantıyı kendi takvimimize yazar.", "calendar-write")

    def preview(self, ctx: ToolContext, **kwargs: Any) -> ToolPreview:
        return ToolPreview(summary=f"Mail #{kwargs.get('mail_id')} → takvim")

    def run(self, ctx: ToolContext, **kwargs: Any) -> ToolResult:
        try:
            event = calendar_service.event_from_mail(
                int(kwargs.get("mail_id") or 0),
                reminder_minutes=kwargs.get("reminder_minutes"),
            )
        except ValueError as exc:
            return ToolResult(False, str(exc), untrusted=False)
        return ToolResult(
            True, "Maildeki toplantı takvime eklendi:\n" + _format_event(event),
            untrusted=False, meta={"id": event["id"]},
        )


class InspectMailMeeting(Tool):
    name = "inspect_mail_meeting"
    description = (
        "Bir mailden çıkarılabilecek toplantı bilgisini ÖNİZLE. "
        "DİKKAT: bu araç takvime HİÇBİR ŞEY EKLEMEZ, sadece okur. Etkinliği "
        "gerçekten oluşturmak için ayrıca mail_to_event çağırman ZORUNLUDUR. "
        "Sadece bu aracı çağırıp kullanıcıya 'ekledim' deme."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {"mail_id": {"type": "integer", "description": "Mail id'si."}},
        "required": ["mail_id"],
    }
    risk = "safe"

    def assess(self, ctx: ToolContext, **kwargs: Any) -> Decision:
        return Decision("safe", "Sadece okur, hiçbir şey kaydetmez.", "calendar-read")

    def preview(self, ctx: ToolContext, **kwargs: Any) -> ToolPreview:
        return ToolPreview(summary=f"Mail #{kwargs.get('mail_id')} toplantı bilgisi")

    def run(self, ctx: ToolContext, **kwargs: Any) -> ToolResult:
        try:
            draft = calendar_service.draft_from_mail(int(kwargs.get("mail_id") or 0))
        except ValueError as exc:
            return ToolResult(False, str(exc), untrusted=False)
        mail_id = int(kwargs.get("mail_id") or 0)
        if not draft.starts_at:
            return ToolResult(
                True,
                f"ÖNİZLEME (hiçbir şey kaydedilmedi).\n"
                f"Bu mailde tarih/saat bulunamadı. Gerekçe: {draft.reason}",
                untrusted=False,
            )

        existing = calendar_store.find_by_source("mail", str(mail_id))
        lines = [
            "ÖNİZLEME — bu araç takvime HİÇBİR ŞEY EKLEMEDİ.",
            f"Başlık: {draft.title}",
            f"Başlangıç: {draft.starts_at}",
            f"Bitiş: {draft.ends_at or '(belirtilmemiş, +1 saat varsayılacak)'}",
            f"Güven: %{int(draft.confidence * 100)} — {draft.reason}",
        ]
        if draft.meeting_url:
            lines.append(f"Bağlantı: {draft.meeting_url}")
        if draft.attendees:
            lines.append(f"Katılımcılar: {', '.join(draft.attendees)}")
        if draft.recurring:
            lines.append("⚠ Bu davet TEKRARLAYAN bir seri — sadece ilk oluşumu eklenecek.")
        if draft.confidence < 0.5:
            lines.append("⚠ Güven düşük. Kullanıcıya tarihi doğrulat, doğrudan ekleme.")

        # Sonraki adımı çıktının kendisine yaz: model bu aracı çağırıp
        # "ekledim" dediği canlı bir vaka görüldü (bkz. DECISIONS.md
        # "Faz 8 kabul testi"). Araç açıklaması tek başına yetmiyor.
        if existing:
            lines.append(
                f"\nDURUM: Bu mail zaten takvimde (#{existing['id']}). "
                "Tekrar eklemene gerek yok."
            )
        else:
            lines.append(
                f"\nDURUM: Bu toplantı takvimde YOK. Eklemek istiyorsan ŞİMDİ "
                f"mail_to_event(mail_id={mail_id}) çağır — yoksa hiçbir şey eklenmemiş olur."
            )
        return ToolResult(True, "\n".join(lines), untrusted=False)


list_events = register(ListEvents())
create_event = register(CreateEvent())
update_event = register(UpdateEvent())
delete_event = register(DeleteEvent())
mail_to_event = register(MailToEvent())
inspect_mail_meeting = register(InspectMailMeeting())
