"""ICS (RFC 5545) okuma ve yazma — bağımlılıksız, ihtiyacımız kadarı.

`icalendar` paketini eklemedik (bkz. DECISIONS.md): tek yönlü, tek kullanıcılı
bir takvim için gereken alt küme küçük — VEVENT, DTSTART/DTEND, SUMMARY,
DESCRIPTION, LOCATION, ATTENDEE, UID. Tekrarlayan etkinlikler (RRULE)
DESTEKLENMİYOR; bir davet RRULE taşıyorsa ilk oluşumu alınır ve kullanıcıya
bunun tekrarlayan bir seri olduğu söylenir.

Ayrıştırıcı hoşgörülüdür: bozuk bir davet ekini yüzünden mail senkronunun
düşmesi kabul edilemez, o yüzden her hata `None`/atlama ile sonuçlanır.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

# RFC 5545: 75 oktetten uzun satırlar CRLF + tek boşlukla katlanır.
_UNFOLD = re.compile(r"\r?\n[ \t]")
_LINE = re.compile(r"^(?P<name>[A-Za-z0-9-]+)(?P<params>;[^:]*)?:(?P<value>.*)$")
_DURATION = re.compile(
    r"^P(?:(?P<weeks>\d+)W)?(?:(?P<days>\d+)D)?"
    r"(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?)?$"
)


@dataclass
class IcsEvent:
    uid: str = ""
    title: str = ""
    description: str = ""
    location: str = ""
    starts_at: str = ""       # ISO8601, ofsetli
    ends_at: str = ""
    all_day: bool = False
    attendees: list[str] = field(default_factory=list)
    organizer: str = ""
    meeting_url: str = ""
    recurring: bool = False
    status: str = ""          # CONFIRMED | CANCELLED | TENTATIVE


def _unescape(value: str) -> str:
    """RFC 5545 metin kaçışları: `\\n`, `\\,`, `\\;`, `\\\\`."""
    out: list[str] = []
    index = 0
    while index < len(value):
        char = value[index]
        if char == "\\" and index + 1 < len(value):
            nxt = value[index + 1]
            out.append({"n": "\n", "N": "\n", ",": ",", ";": ";", "\\": "\\"}.get(nxt, nxt))
            index += 2
            continue
        out.append(char)
        index += 1
    return "".join(out)


def _escape(value: str) -> str:
    return (
        (value or "")
        .replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
    )


def _parse_params(raw: str | None) -> dict[str, str]:
    params: dict[str, str] = {}
    if not raw:
        return params
    for chunk in raw.lstrip(";").split(";"):
        if "=" in chunk:
            key, _, value = chunk.partition("=")
            params[key.strip().upper()] = value.strip().strip('"')
    return params


def parse_datetime(value: str, params: dict[str, str]) -> tuple[str, bool]:
    """ICS tarih değeri → (ISO8601 string, tüm_gün mü).

    Üç biçim var: `20260817` (tüm gün), `20260817T140000` (yerel/floating),
    `20260817T110000Z` (UTC). TZID parametresi olan yerel saatler, IANA adı
    tanınırsa o bölgeye, tanınmazsa sistem saat dilimine bağlanır — bilinmeyen
    bir TZID yüzünden daveti tamamen düşürmek yerine yaklaşık ama kullanılır
    bir sonuç vermeyi tercih ediyoruz.
    """
    value = value.strip()
    if not value:
        return "", False

    if params.get("VALUE", "").upper() == "DATE" or (len(value) == 8 and "T" not in value):
        try:
            parsed = datetime.strptime(value, "%Y%m%d")
        except ValueError:
            return "", False
        return parsed.replace(tzinfo=_local_tz()).isoformat(), True

    if value.endswith("Z"):
        try:
            parsed = datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        except ValueError:
            return "", False
        return parsed.astimezone(_local_tz()).isoformat(), False

    try:
        parsed = datetime.strptime(value, "%Y%m%dT%H%M%S")
    except ValueError:
        return "", False

    tzid = params.get("TZID", "")
    tzinfo = _local_tz()
    if tzid:
        try:
            from zoneinfo import ZoneInfo

            tzinfo = ZoneInfo(tzid)
        except Exception:
            pass  # tanınmayan TZID — sistem saat dilimiyle devam
    return parsed.replace(tzinfo=tzinfo).isoformat(), False


def _local_tz():
    return datetime.now().astimezone().tzinfo or timezone.utc


def _parse_duration(value: str) -> timedelta | None:
    match = _DURATION.match(value.strip())
    if not match:
        return None
    parts = {key: int(val) for key, val in match.groupdict(default="0").items()}
    return timedelta(
        weeks=parts["weeks"], days=parts["days"], hours=parts["hours"],
        minutes=parts["minutes"], seconds=parts["seconds"],
    )


def parse_ics(payload: str) -> list[IcsEvent]:
    """Bir ICS gövdesindeki tüm VEVENT'leri çıkar. Hata fırlatmaz."""
    if not payload:
        return []
    text = _UNFOLD.sub("", payload)

    events: list[IcsEvent] = []
    current: IcsEvent | None = None
    duration: timedelta | None = None
    # VEVENT'in İÇİNDE başka bileşenler olabilir — VALARM neredeyse her
    # gerçek davette var ve kendi SUMMARY/DESCRIPTION'ını taşır. Onları
    # etkinliğin alanları sanmamak için iç içe derinliği sayıyoruz.
    nested = 0

    for line in text.splitlines():
        line = line.strip("\r")
        if not line:
            continue
        match = _LINE.match(line)
        if not match:
            continue
        name = match.group("name").upper()
        params = _parse_params(match.group("params"))
        value = match.group("value")
        component = value.strip().upper()

        if name == "BEGIN" and component == "VEVENT":
            current = IcsEvent()
            duration = None
            nested = 0
            continue
        if name == "END" and component == "VEVENT":
            if current is not None:
                if not current.ends_at and current.starts_at:
                    current.ends_at = _apply_duration(current, duration)
                if current.title or current.starts_at:
                    events.append(current)
            current = None
            nested = 0
            continue
        if current is None:
            continue

        # VEVENT içindeki alt bileşen (VALARM, X-...) — alanlarını yut.
        if name == "BEGIN":
            nested += 1
            continue
        if name == "END":
            nested = max(0, nested - 1)
            continue
        if nested:
            continue

        if name == "UID":
            current.uid = _unescape(value).strip()
        elif name == "SUMMARY":
            current.title = _unescape(value).strip()
        elif name == "DESCRIPTION":
            current.description = _unescape(value).strip()
        elif name == "LOCATION":
            current.location = _unescape(value).strip()
        elif name == "DTSTART":
            current.starts_at, current.all_day = parse_datetime(value, params)
        elif name == "DTEND":
            current.ends_at, _ = parse_datetime(value, params)
        elif name == "DURATION":
            duration = _parse_duration(value)
        elif name == "ATTENDEE":
            address = _mailto(value) or params.get("CN", "")
            if address:
                current.attendees.append(address)
        elif name == "ORGANIZER":
            current.organizer = _mailto(value) or params.get("CN", "")
        elif name == "RRULE":
            current.recurring = True
        elif name == "STATUS":
            current.status = value.strip().upper()
        elif name in ("URL", "X-GOOGLE-CONFERENCE"):
            current.meeting_url = current.meeting_url or value.strip()

    for event in events:
        if not event.meeting_url:
            from app.mail.parser import MEETING_URL

            found = MEETING_URL.search(f"{event.description}\n{event.location}")
            if found:
                event.meeting_url = found.group(0)
    return events


def _apply_duration(event: IcsEvent, duration: timedelta | None) -> str:
    """DTEND yoksa: DURATION varsa onu uygula, yoksa varsayılan süre."""
    try:
        start = datetime.fromisoformat(event.starts_at)
    except ValueError:
        return ""
    if duration is not None:
        return (start + duration).isoformat()
    if event.all_day:
        return (start + timedelta(days=1)).isoformat()
    return (start + timedelta(hours=1)).isoformat()


def _mailto(value: str) -> str:
    value = value.strip()
    if value.lower().startswith("mailto:"):
        return value[7:].strip().lower()
    return ""


# --- yazma ------------------------------------------------------------------


def _fold(line: str) -> str:
    """75 oktetlik satır katlaması. UTF-8'de karakter ortasından kesmemeli."""
    encoded = line.encode("utf-8")
    if len(encoded) <= 73:
        return line
    chunks: list[str] = []
    buffer = ""
    size = 0
    for char in line:
        char_size = len(char.encode("utf-8"))
        limit = 73 if not chunks else 72
        if size + char_size > limit:
            chunks.append(buffer)
            buffer = char
            size = char_size
        else:
            buffer += char
            size += char_size
    chunks.append(buffer)
    return "\r\n ".join(chunks)


def _ics_timestamp(value: str, *, all_day: bool = False) -> tuple[str, str]:
    """ISO8601 → (ICS değeri, parametre eki)."""
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        parsed = datetime.now(_local_tz())
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_local_tz())
    if all_day:
        return parsed.strftime("%Y%m%d"), ";VALUE=DATE"
    return parsed.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ"), ""


def build_ics(events: list[dict[str, Any]], *, calendar_name: str = "ULL-Bot") -> str:
    """Etkinlik satırlarını ICS metnine çevir (dışa aktarım)."""
    lines: list[str] = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//ULL-Bot//Takvim//TR",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{_escape(calendar_name)}",
    ]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    for event in events:
        all_day = bool(event.get("all_day"))
        start_value, start_param = _ics_timestamp(str(event.get("starts_at") or ""), all_day=all_day)
        uid = str(event.get("uid") or "").strip() or f"ullbot-{event.get('id', 'x')}@localhost"
        lines.append("BEGIN:VEVENT")
        lines.append(f"UID:{uid}")
        lines.append(f"DTSTAMP:{stamp}")
        lines.append(f"DTSTART{start_param}:{start_value}")
        if event.get("ends_at"):
            end_value, end_param = _ics_timestamp(str(event["ends_at"]), all_day=all_day)
            lines.append(f"DTEND{end_param}:{end_value}")
        lines.append(_fold(f"SUMMARY:{_escape(str(event.get('title') or ''))}"))
        if event.get("description"):
            lines.append(_fold(f"DESCRIPTION:{_escape(str(event['description']))}"))
        if event.get("location"):
            lines.append(_fold(f"LOCATION:{_escape(str(event['location']))}"))
        if event.get("meeting_url"):
            lines.append(_fold(f"URL:{event['meeting_url']}"))
        for attendee in event.get("attendees") or []:
            lines.append(_fold(f"ATTENDEE;CUTYPE=INDIVIDUAL:mailto:{attendee}"))
        reminder = event.get("reminder_minutes")
        if reminder is not None and int(reminder) >= 0:
            lines.extend([
                "BEGIN:VALARM",
                "ACTION:DISPLAY",
                f"TRIGGER:-PT{int(reminder)}M",
                _fold(f"DESCRIPTION:{_escape(str(event.get('title') or 'Hatırlatma'))}"),
                "END:VALARM",
            ])
        lines.append("END:VEVENT")

    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


def day_bounds(day: date) -> tuple[str, str]:
    """Bir günün [00:00, 24:00) sınırları — aralık sorguları için."""
    tz = _local_tz()
    start = datetime.combine(day, time.min, tzinfo=tz)
    return start.isoformat(), (start + timedelta(days=1)).isoformat()
