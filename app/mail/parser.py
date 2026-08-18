"""Ham RFC822 baytları → düz alanlar.

Burada ağ yok, veritabanı yok, ayar yok: girdi bayt, çıktı bir dataclass.
Sebebi test edilebilirlik — mail ayrıştırma bu sistemdeki en çok "gerçek
dünya bozuk verisi" gören yer (eksik başlık, yanlış charset, iç içe
multipart, tek başına HTML gövde), ve bunların hepsi IMAP sunucusu olmadan
test edilebilmeli.

Güvenlik notu: buradan çıkan hiçbir şeye güvenilmez. Mail içeriği tanım
gereği dışarıdan gelir; ajan araçları (`app/agent/tools/mail.py`) bunu modele
her zaman `untrusted=True` ile verir (spec §6.4 prompt injection).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email import message_from_bytes, policy
from email.header import decode_header, make_header
from email.message import EmailMessage
from email.utils import parseaddr, parsedate_to_datetime, getaddresses
from typing import Any

SNIPPET_LENGTH = 240

# HTML'i düz metne indirirken tamamen atılacak bloklar (içeriği görünmez).
_DROP_BLOCKS = re.compile(
    r"<(script|style|head|title)\b.*?</\1>", re.IGNORECASE | re.DOTALL
)
_BLOCK_BREAK = re.compile(
    r"</?(p|div|br|tr|li|h[1-6]|table|blockquote)\b[^>]*>", re.IGNORECASE
)
_ANY_TAG = re.compile(r"<[^>]+>")
_WHITESPACE_RUN = re.compile(r"[ \t\r\f\v]+")
_BLANK_LINES = re.compile(r"\n{3,}")

# Toplantı bağlantıları — takvim etkinliğine taşınır.
MEETING_URL = re.compile(
    r"https?://(?:[\w.-]*\.)?"
    r"(?:meet\.google\.com|zoom\.us|teams\.microsoft\.com|teams\.live\.com|"
    r"whereby\.com|meet\.jit\.si|webex\.com|discord\.gg)"
    r"/[^\s<>\"')]+",
    re.IGNORECASE,
)


@dataclass
class ParsedMail:
    message_id: str = ""
    from_name: str = ""
    from_addr: str = ""
    to_addrs: list[str] = field(default_factory=list)
    cc_addrs: list[str] = field(default_factory=list)
    subject: str = ""
    date_ts: str = ""
    body_text: str = ""
    body_html: str = ""
    snippet: str = ""
    attachments: list[dict[str, Any]] = field(default_factory=list)
    ics_payload: str = ""
    # Sınıflandırıcının kullandığı ham başlıklar (List-Unsubscribe,
    # Auto-Submitted, Precedence gibi) — modele gitmiyor, kurala giriyor.
    headers: dict[str, str] = field(default_factory=dict)

    @property
    def meeting_urls(self) -> list[str]:
        found = MEETING_URL.findall(f"{self.body_text}\n{self.body_html}")
        seen: list[str] = []
        for url in found:
            if url not in seen:
                seen.append(url)
        return seen


def decode_mime_header(raw: str | None) -> str:
    """`=?UTF-8?B?...?=` gibi kodlanmış başlıkları çöz.

    Bozuk kodlamada patlamak yerine ham metni döndürür — bir başlığın
    kodlaması hatalı diye tüm mailin kaybolması kabul edilemez.
    """
    if not raw:
        return ""
    try:
        return str(make_header(decode_header(raw))).strip()
    except (UnicodeDecodeError, LookupError, ValueError):
        return raw.strip()


def html_to_text(html: str) -> str:
    """Kaba ama bağımlılıksız HTML → metin.

    Amaç sadeleştirmek: liste görünümünde snippet, modele verilirken de
    okunur bir gövde. Tam bir renderer değil (BeautifulSoup/lxml eklemedik —
    bkz. DECISIONS.md), pazarlama maillerinin tablo düzenini korumaz.
    """
    if not html:
        return ""
    text = _DROP_BLOCKS.sub(" ", html)
    text = _BLOCK_BREAK.sub("\n", text)
    text = _ANY_TAG.sub("", text)
    # Varlık çözümü: en sık geçenler elle, kalanı `html.unescape`.
    import html as html_module

    text = html_module.unescape(text)
    text = text.replace("‌", "").replace("\xa0", " ")
    text = _WHITESPACE_RUN.sub(" ", text)
    text = "\n".join(line.strip() for line in text.split("\n"))
    return _BLANK_LINES.sub("\n\n", text).strip()


def make_snippet(text: str, limit: int = SNIPPET_LENGTH) -> str:
    flat = _WHITESPACE_RUN.sub(" ", text.replace("\n", " ")).strip()
    if len(flat) <= limit:
        return flat
    return flat[: limit - 1].rstrip() + "…"


def _payload_text(part: EmailMessage) -> str:
    """Bir parçanın metnini charset'e saygılı biçimde çöz."""
    try:
        payload = part.get_payload(decode=True)
    except Exception:  # bozuk base64 vb.
        return ""
    if payload is None:
        return ""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except LookupError:  # sunucunun uydurduğu charset adı
        return payload.decode("utf-8", errors="replace")


def parse_message(raw: bytes, *, body_limit: int = 200_000) -> ParsedMail:
    """Ham mail baytlarını ayrıştır. Hiçbir koşulda istisna fırlatmaz."""
    try:
        message = message_from_bytes(raw, policy=policy.default)
    except Exception:
        # Tamamen okunamayan bir mail bile listede görünsün, kaybolmasın.
        return ParsedMail(subject="(ayrıştırılamayan mesaj)", body_text="")

    parsed = ParsedMail()
    parsed.message_id = (message.get("Message-ID") or "").strip()
    parsed.subject = decode_mime_header(message.get("Subject"))

    from_name, from_addr = parseaddr(decode_mime_header(message.get("From")))
    parsed.from_name = from_name or from_addr
    parsed.from_addr = from_addr.lower()

    parsed.to_addrs = _address_list(message, "To")
    parsed.cc_addrs = _address_list(message, "Cc")

    parsed.date_ts = _parse_date(message.get("Date"))

    for header in ("List-Unsubscribe", "List-Id", "Precedence", "Auto-Submitted",
                   "X-Priority", "Importance", "Return-Path", "Reply-To",
                   # Sunucunun kendi spam kararı — bizimkinden iyisi.
                   "X-Spam-Flag", "X-Spam-Status", "X-Gm-Spam", "X-Gm-Phishy"):
        value = message.get(header)
        if value:
            parsed.headers[header] = decode_mime_header(value)

    _walk_parts(message, parsed)

    if not parsed.body_text and parsed.body_html:
        parsed.body_text = html_to_text(parsed.body_html)

    parsed.body_text = parsed.body_text[:body_limit]
    parsed.body_html = parsed.body_html[:body_limit]
    parsed.snippet = make_snippet(parsed.body_text or parsed.subject)
    return parsed


def _address_list(message: EmailMessage, header: str) -> list[str]:
    raw = message.get_all(header, [])
    if not raw:
        return []
    decoded = [decode_mime_header(str(item)) for item in raw]
    return [addr.lower() for _, addr in getaddresses(decoded) if addr]


def _parse_date(raw: str | None) -> str:
    """Date başlığı → ISO8601 UTC. Başlık yoksa/bozuksa boş string."""
    if not raw:
        return ""
    try:
        parsed = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return ""
    if parsed is None:
        return ""
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def _walk_parts(message: EmailMessage, parsed: ParsedMail) -> None:
    """Gövde parçalarını topla; ek olan her şeyi meta veriye çevir.

    `text/calendar` özel: içeriği hem ek listesine hem `ics_payload`a gider —
    takvim davetlerini tespit etmenin en güvenilir yolu bu (konu satırındaki
    "toplantı" kelimesinden çok daha kesin).
    """
    for part in message.walk():
        if part.is_multipart():
            continue

        content_type = (part.get_content_type() or "").lower()
        disposition = (part.get_content_disposition() or "").lower()
        filename = decode_mime_header(part.get_filename())

        if content_type == "text/calendar":
            parsed.ics_payload = parsed.ics_payload or _payload_text(part)
            parsed.attachments.append(
                {
                    "filename": filename or "davet.ics",
                    "content_type": content_type,
                    "size": len(part.get_payload(decode=True) or b""),
                    "calendar": True,
                }
            )
            continue

        if disposition == "attachment" or (filename and not content_type.startswith("text/")):
            payload = part.get_payload(decode=True) or b""
            parsed.attachments.append(
                {
                    "filename": filename or "(adsız)",
                    "content_type": content_type,
                    "size": len(payload),
                }
            )
            continue

        if content_type == "text/plain" and not parsed.body_text:
            parsed.body_text = _payload_text(part)
        elif content_type == "text/html" and not parsed.body_html:
            parsed.body_html = _payload_text(part)


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
