"""Takvim yüzeyi — özellikle **maildeki toplantıyı etkinliğe çevirme**.

Kullanıcının istediği akış tam olarak bu: "mailleri okuyup ayıracak …
meetingler için calendara meeting ekleyecek". İki yol var ve ikisi de burada:

1. **Kesin yol — ICS eki.** Mailde `text/calendar` varsa tarih, saat, katılımcı
   ve toplantı bağlantısı tahmin edilmez, okunur. Hiç LLM harcanmaz.
2. **Tahmin yolu — düz metin.** Ek yoksa gövdedeki tarih/saat kalıpları
   aranır (Türkçe ve İngilizce). Bulunursa `confidence` ile birlikte bir
   TASLAK döner; kullanıcı ya da model onaylayıp kaydeder.

İkinci yol bilerek "taslak" üretir, doğrudan takvime yazmaz: bir maildeki
"salı 15:00" ifadesi hangi salı olduğu belirsiz olabilir, yanlış bir etkinlik
sessizce eklenirse kullanıcı bunu ancak bildirim çaldığında fark eder.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from app.calendar import ics, store
from app.calendar.store import local_tz, normalize_dt
from app.mail import store as mail_store
from app.mail.parser import MEETING_URL
from app.settings import settings

# --- Türkçe/İngilizce tarih-saat kalıpları ----------------------------------

_MONTHS = {
    "ocak": 1, "şubat": 2, "subat": 2, "mart": 3, "nisan": 4, "mayıs": 5, "mayis": 5,
    "haziran": 6, "temmuz": 7, "ağustos": 8, "agustos": 8, "eylül": 9, "eylul": 9,
    "ekim": 10, "kasım": 11, "kasim": 11, "aralık": 12, "aralik": 12,
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8,
    "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}
_WEEKDAYS = {
    "pazartesi": 0, "salı": 1, "sali": 1, "çarşamba": 2, "carsamba": 2,
    "perşembe": 3, "persembe": 3, "cuma": 4, "cumartesi": 5, "pazar": 6,
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}

_TIME = r"(?P<hour>[01]?\d|2[0-3])[:.](?P<minute>[0-5]\d)"

# Tarih ile saat arasına giren doldurma kelimeleri. Türkçe'de bu boşluk
# neredeyse hiç boş kalmıyor: "20.08.2026 TARİHİNDE SAAT 14:00",
# "20 Ağustos GÜNÜ 14:00", "Perşembe GÜNÜ SAAT 09:30". Bu kalıp yeterince
# geniş olmazsa saat sessizce kaybolur ve etkinlik varsayılan saate (09:00)
# düşer — kullanıcı bunu ancak yanlış saatte bildirim gelince fark eder.
# En fazla iki kelimelik bir köprüye izin veriyoruz; daha uzun araya giren
# metinde saatin o tarihe ait olduğu artık güvenilir değil.
_CONNECTOR = (
    r"(?:\s*[,·-]?\s*"
    r"(?:günü|gunu|tarihinde|tarihli|itibariyle|itibarıyla|de|da|te|ta|"
    r"on|at|starting|from|için|icin)?"
    r"\s*(?:saat|saatinde|at|@)?\s*)"
)
_TIME_SUFFIX = r"(?:['’]?(?:te|ta|de|da|deki|daki))?"

# "20.08.2026 14:30" / "20/08/2026 tarihinde saat 14:30"
_NUMERIC_DATE = re.compile(
    r"(?P<day>[0-3]?\d)[./-](?P<month>[01]?\d)[./-](?P<year>20\d{2})"
    r"(?:" + _CONNECTOR + _TIME + _TIME_SUFFIX + r")?",
    re.IGNORECASE,
)
# "20 Ağustos 2026 saat 14:30" / "20 Ağustos günü 14:30"
_NAMED_DATE = re.compile(
    r"(?P<day>[0-3]?\d)\s+(?P<month>" + "|".join(_MONTHS) + r")\s*(?P<year>20\d{2})?"
    r"(?:" + _CONNECTOR + _TIME + _TIME_SUFFIX + r")?",
    re.IGNORECASE,
)
# "yarın 15:00", "bugün saat 09:30"
_RELATIVE = re.compile(
    r"(?P<word>yarın|yarin|bugün|bugun|öbür gün|obur gun|tomorrow|today)"
    r"(?:" + _CONNECTOR + _TIME + _TIME_SUFFIX + r")?",
    re.IGNORECASE,
)
# "salı 14:00", "önümüzdeki cuma günü saat 10:30"
_WEEKDAY = re.compile(
    r"(?:(?P<next>önümüzdeki|onumuzdeki|gelecek|next)\s+)?"
    r"(?P<day>" + "|".join(_WEEKDAYS) + r")"
    r"(?:" + _CONNECTOR + _TIME + _TIME_SUFFIX + r")?",
    re.IGNORECASE,
)
# Sadece saat: "toplantı 15:00'te"
_BARE_TIME = re.compile(r"(?:saat\s+)?" + _TIME + _TIME_SUFFIX, re.IGNORECASE)

DEFAULT_HOUR = 9  # saat verilmemiş bir gün için varsayılan başlangıç


@dataclass
class EventDraft:
    """Kaydedilmemiş bir etkinlik önerisi."""

    title: str = ""
    starts_at: str = ""
    ends_at: str = ""
    description: str = ""
    location: str = ""
    attendees: list[str] = field(default_factory=list)
    meeting_url: str = ""
    all_day: bool = False
    confidence: float = 0.0
    reason: str = ""
    source: str = "mail"
    source_ref: str = ""
    recurring: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "title": self.title, "starts_at": self.starts_at, "ends_at": self.ends_at,
            "description": self.description, "location": self.location,
            "attendees": self.attendees, "meeting_url": self.meeting_url,
            "all_day": self.all_day, "confidence": round(self.confidence, 2),
            "reason": self.reason, "source": self.source, "source_ref": self.source_ref,
            "recurring": self.recurring,
        }


def _resolve_time(match: re.Match[str]) -> tuple[int, int] | None:
    groups = match.groupdict()
    if groups.get("hour") is None:
        return None
    return int(groups["hour"]), int(groups["minute"])


def extract_datetime(text: str, *, reference: datetime | None = None) -> tuple[str, float, str]:
    """Metinden ilk makul tarih-saati çıkar → (ISO8601, güven, gerekçe).

    `reference` "yarın"ın neye göre yarın olduğunu belirler; mail için
    mailin kendi tarihi verilir, sohbet için şimdi.
    """
    now = reference or datetime.now(local_tz())
    if now.tzinfo is None:
        now = now.replace(tzinfo=local_tz())
    haystack = text[:4000]

    match = _NUMERIC_DATE.search(haystack)
    if match:
        clock = _resolve_time(match) or (DEFAULT_HOUR, 0)
        try:
            found = now.replace(
                year=int(match.group("year")), month=int(match.group("month")),
                day=int(match.group("day")), hour=clock[0], minute=clock[1],
                second=0, microsecond=0,
            )
        except ValueError:
            return "", 0.0, "Sayısal tarih geçersiz (örn. 31 Şubat)."
        return found.isoformat(), 0.9 if _resolve_time(match) else 0.7, f"Tam tarih bulundu: {match.group(0).strip()!r}"

    match = _NAMED_DATE.search(haystack)
    if match:
        month = _MONTHS.get(match.group("month").lower())
        clock = _resolve_time(match) or (DEFAULT_HOUR, 0)
        year = int(match.group("year")) if match.group("year") else now.year
        try:
            found = now.replace(
                year=year, month=month or now.month, day=int(match.group("day")),
                hour=clock[0], minute=clock[1], second=0, microsecond=0,
            )
        except ValueError:
            return "", 0.0, "Ay adlı tarih geçersiz."
        # Yıl yazılmamış ve tarih geçmişte kalıyorsa gelecek yıl kastedilmiştir.
        if not match.group("year") and found < now - timedelta(days=1):
            found = found.replace(year=year + 1)
        return found.isoformat(), 0.85 if _resolve_time(match) else 0.65, f"Ay adlı tarih: {match.group(0).strip()!r}"

    match = _RELATIVE.search(haystack)
    if match:
        word = match.group("word").lower()
        offset = {"yarın": 1, "yarin": 1, "tomorrow": 1, "öbür gün": 2, "obur gun": 2}.get(word, 0)
        clock = _resolve_time(match) or (DEFAULT_HOUR, 0)
        found = (now + timedelta(days=offset)).replace(
            hour=clock[0], minute=clock[1], second=0, microsecond=0
        )
        return found.isoformat(), 0.7 if _resolve_time(match) else 0.45, f"Göreli tarih: {word!r}"

    match = _WEEKDAY.search(haystack)
    if match:
        target = _WEEKDAYS.get(match.group("day").lower())
        if target is not None:
            clock = _resolve_time(match) or (DEFAULT_HOUR, 0)
            ahead = (target - now.weekday()) % 7
            if ahead == 0 or match.group("next"):
                ahead = ahead or 7
                if match.group("next") and ahead < 7:
                    ahead += 7
            found = (now + timedelta(days=ahead)).replace(
                hour=clock[0], minute=clock[1], second=0, microsecond=0
            )
            return found.isoformat(), 0.55 if _resolve_time(match) else 0.35, f"Hafta günü: {match.group(0).strip()!r}"

    match = _BARE_TIME.search(haystack)
    if match:
        clock = _resolve_time(match)
        if clock:
            found = now.replace(hour=clock[0], minute=clock[1], second=0, microsecond=0)
            if found < now:
                found += timedelta(days=1)
            return found.isoformat(), 0.3, f"Sadece saat bulundu: {match.group(0).strip()!r} (gün tahmin edildi)"

    return "", 0.0, "Metinde tarih/saat bulunamadı."


def draft_from_mail(message_id: int) -> EventDraft:
    """Bir mailden etkinlik taslağı üret. ICS varsa kesin, yoksa tahmin."""
    message = mail_store.get_message(message_id)
    if message is None:
        raise ValueError(f"Mesaj bulunamadı: {message_id}")

    subject = message.get("subject") or "(konusuz)"
    sender = message.get("from_addr") or ""
    body = message.get("body_text") or ""

    # 1. ICS eki — kesin yol.
    payload = message.get("ics_payload") or ""
    if payload:
        events = ics.parse_ics(payload)
        if events:
            event = events[0]
            attendees = list(dict.fromkeys(event.attendees + ([sender] if sender else [])))
            return EventDraft(
                title=event.title or subject,
                starts_at=event.starts_at,
                ends_at=event.ends_at,
                description=event.description or f"Kaynak mail: {subject}",
                location=event.location,
                attendees=attendees,
                meeting_url=event.meeting_url,
                all_day=event.all_day,
                confidence=1.0,
                reason="Maildeki takvim daveti (ICS) okundu — tarih tahmin edilmedi.",
                source_ref=str(message_id),
                recurring=event.recurring,
            )

    # 2. Düz metin — tahmin yolu.
    reference = None
    if message.get("date_ts"):
        try:
            reference = datetime.fromisoformat(message["date_ts"]).astimezone(local_tz())
        except ValueError:
            reference = None

    starts_at, confidence, reason = extract_datetime(f"{subject}\n{body}", reference=reference)
    url_match = MEETING_URL.search(body)
    if url_match and confidence:
        # 1.0 SADECE ICS yolunun rozeti — "tarih okundu, tahmin edilmedi"
        # anlamına geliyor ve UI bunu farklı bir metinle gösteriyor. Metinden
        # çıkarım ne kadar iyi giderse gitsin bir tahmindir, tavanı 0.95.
        confidence = min(0.95, confidence + 0.1)
        reason += " Toplantı bağlantısı da bulundu."

    return EventDraft(
        title=subject,
        starts_at=starts_at,
        ends_at="",
        description=f"Kaynak mail: {subject}\nKimden: {sender}",
        attendees=[sender] if sender else [],
        meeting_url=url_match.group(0) if url_match else "",
        confidence=confidence,
        reason=reason,
        source_ref=str(message_id),
    )


def save_draft(draft: EventDraft, *, reminder_minutes: int | None = None) -> dict[str, Any]:
    """Taslağı gerçek bir etkinliğe çevir.

    Aynı mailden daha önce bir etkinlik oluşturulduysa yenisi eklenmez,
    mevcut olan güncellenir — kullanıcı "takvime ekle"ye iki kez basınca
    takvimde iki kopya olmasın.
    """
    if not draft.starts_at:
        raise ValueError("Başlangıç zamanı olmayan bir taslak kaydedilemez.")

    existing = None
    if draft.source_ref:
        existing = store.find_by_source(draft.source, draft.source_ref)
    if existing:
        updated = store.update_event(
            int(existing["id"]),
            title=draft.title, starts_at=draft.starts_at, ends_at=draft.ends_at or None,
            description=draft.description, location=draft.location,
            attendees=draft.attendees, meeting_url=draft.meeting_url,
            all_day=draft.all_day,
        )
        return updated or existing

    return store.create_event(
        title=draft.title,
        starts_at=draft.starts_at,
        ends_at=draft.ends_at,
        description=draft.description,
        location=draft.location,
        all_day=draft.all_day,
        attendees=draft.attendees,
        meeting_url=draft.meeting_url,
        source=draft.source,
        source_ref=draft.source_ref,
        reminder_minutes=(
            reminder_minutes if reminder_minutes is not None else settings.default_reminder_minutes
        ),
    )


def event_from_mail(message_id: int, *, reminder_minutes: int | None = None) -> dict[str, Any]:
    """Mail → takvim, tek adımda. Tarih bulunamazsa hata."""
    draft = draft_from_mail(message_id)
    if not draft.starts_at:
        raise ValueError(
            f"Bu mailde bir tarih/saat bulunamadı ({draft.reason}). "
            "Etkinliği elle oluşturman ya da tarihi belirtmen gerekiyor."
        )
    return save_draft(draft, reminder_minutes=reminder_minutes)


def import_ics(payload: str, *, source: str = "ics") -> list[dict[str, Any]]:
    """ICS metnindeki tüm etkinlikleri takvime al (UID ile tekilleştirir)."""
    created: list[dict[str, Any]] = []
    for event in ics.parse_ics(payload):
        if not event.starts_at or event.status == "CANCELLED":
            continue
        created.append(
            store.create_event(
                uid=event.uid,
                title=event.title or "(başlıksız)",
                starts_at=event.starts_at,
                ends_at=event.ends_at,
                description=event.description,
                location=event.location,
                all_day=event.all_day,
                attendees=event.attendees,
                meeting_url=event.meeting_url,
                source=source,
            )
        )
    return created


def export_ics(*, start: str = "", end: str = "") -> str:
    return ics.build_ics(store.list_events(start=start, end=end, limit=2000))


def scan_meeting_mails(limit: int = 30) -> list[dict[str, Any]]:
    """`toplanti` kategorisindeki, henüz takvime alınmamış mailler.

    UI'daki "takvime eklenmeyi bekleyen toplantılar" listesi bu.
    """
    pending: list[dict[str, Any]] = []
    for message in mail_store.list_messages(category="toplanti", limit=limit):
        if store.find_by_source("mail", str(message["id"])):
            continue
        try:
            draft = draft_from_mail(int(message["id"]))
        except ValueError:
            continue
        if not draft.starts_at:
            continue
        pending.append({
            "message": {
                "id": message["id"], "subject": message["subject"],
                "from_name": message["from_name"], "from_addr": message["from_addr"],
                "date_ts": message["date_ts"], "has_invite": message.get("has_invite"),
            },
            "draft": draft.as_dict(),
        })
    return pending
