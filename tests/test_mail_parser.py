"""Mail ayrıştırma testleri (Faz 8).

Sahada karşılaşılan bozukluklar burada: kodlanmış başlıklar, tek başına HTML
gövde, iç içe multipart, takvim eki, eksik Date başlığı. Hiçbiri IMAP
sunucusu gerektirmiyor — `parser.py` bilerek saf tutuldu.
"""

from __future__ import annotations

from email.message import EmailMessage

from app.mail.parser import (
    ParsedMail,
    decode_mime_header,
    html_to_text,
    make_snippet,
    parse_message,
)


def build(
    *,
    subject: str = "Konu",
    sender: str = "Ahmet Yılmaz <ahmet@example.com>",
    to: str = "ben@example.com",
    text: str | None = "Merhaba dünya",
    html: str | None = None,
    headers: dict[str, str] | None = None,
) -> bytes:
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = sender
    message["To"] = to
    message["Date"] = "Mon, 17 Aug 2026 09:30:00 +0300"
    for key, value in (headers or {}).items():
        message[key] = value
    if text is not None:
        message.set_content(text)
    if html is not None:
        if text is None:
            message.set_content(html, subtype="html")
        else:
            message.add_alternative(html, subtype="html")
    return message.as_bytes()


def test_temel_alanlar_okunur():
    mail = parse_message(build(subject="Toplantı yarın", text="Saat 15:00'te görüşelim."))
    assert mail.subject == "Toplantı yarın"
    assert mail.from_addr == "ahmet@example.com"
    assert mail.from_name == "Ahmet Yılmaz"
    assert mail.to_addrs == ["ben@example.com"]
    assert "15:00" in mail.body_text
    assert mail.date_ts.startswith("2026-08-17T06:30")  # +03:00 → UTC


def test_kodlanmis_baslik_cozulur():
    # =?UTF-8?B?...?= biçimi — Türkçe konular neredeyse hep böyle geliyor.
    raw = build(subject="=?UTF-8?B?VG9wbGFudMSxIERhdmV0aQ==?=")
    assert parse_message(raw).subject == "Toplantı Daveti"


def test_bozuk_baslik_ham_haliyle_kalir():
    assert decode_mime_header("=?BOZUK?X?zzz?=") == "=?BOZUK?X?zzz?="
    assert decode_mime_header(None) == ""


def test_sadece_html_govde_metne_cevrilir():
    mail = parse_message(
        build(text=None, html="<html><body><p>Merhaba</p><p>İkinci satır</p></body></html>")
    )
    assert "Merhaba" in mail.body_text
    assert "İkinci satır" in mail.body_text
    assert "<p>" not in mail.body_text


def test_html_script_ve_style_atilir():
    text = html_to_text("<style>p{color:red}</style><script>alert(1)</script><p>Görünen</p>")
    assert text.strip() == "Görünen"


def test_multipart_alternative_duz_metni_tercih_eder():
    mail = parse_message(build(text="DÜZ METİN", html="<p>HTML SÜRÜM</p>"))
    assert mail.body_text.strip() == "DÜZ METİN"
    assert "HTML SÜRÜM" in mail.body_html


def test_takvim_eki_ics_payload_olarak_yakalanir():
    message = EmailMessage()
    message["Subject"] = "Davet"
    message["From"] = "org@example.com"
    message["Date"] = "Mon, 17 Aug 2026 09:30:00 +0300"
    message.set_content("Toplantıya davetlisin.")
    message.add_attachment(
        b"BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nSUMMARY:Test\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n",
        maintype="text",
        subtype="calendar",
        filename="invite.ics",
    )
    mail = parse_message(message.as_bytes())
    assert "VCALENDAR" in mail.ics_payload
    assert any(item.get("calendar") for item in mail.attachments)


def test_ek_meta_verisi_toplanir():
    message = EmailMessage()
    message["Subject"] = "Fatura"
    message["From"] = "billing@example.com"
    message["Date"] = "Mon, 17 Aug 2026 09:30:00 +0300"
    message.set_content("Ekte faturanız var.")
    message.add_attachment(b"%PDF-1.4 fake", maintype="application", subtype="pdf", filename="fatura.pdf")
    mail = parse_message(message.as_bytes())
    assert mail.attachments[0]["filename"] == "fatura.pdf"
    assert mail.attachments[0]["content_type"] == "application/pdf"
    # Ek gövdeye karışmamalı.
    assert "PDF" not in mail.body_text


def test_eksik_date_bosluk_dondurur_ama_patlamaz():
    message = EmailMessage()
    message["Subject"] = "Tarihsiz"
    message["From"] = "x@example.com"
    message.set_content("gövde")
    mail = parse_message(message.as_bytes())
    assert mail.date_ts == ""
    assert mail.subject == "Tarihsiz"


def test_tamamen_bozuk_girdi_bos_nesne_dondurur():
    mail = parse_message(b"\xff\xfe bu bir mail degil")
    assert isinstance(mail, ParsedMail)  # istisna fırlatmadı


def test_toplanti_baglantilari_bulunur():
    mail = parse_message(
        build(text="Katıl: https://meet.google.com/abc-defg-hij ve https://zoom.us/j/12345")
    )
    urls = mail.meeting_urls
    assert any("meet.google.com" in url for url in urls)
    assert any("zoom.us" in url for url in urls)


def test_snippet_kirpilir():
    assert make_snippet("a" * 500).endswith("…")
    assert len(make_snippet("a" * 500)) <= 240
    assert make_snippet("kısa metin") == "kısa metin"


def test_list_unsubscribe_basligi_saklanir():
    mail = parse_message(build(headers={"List-Unsubscribe": "<mailto:x@y.z>"}))
    assert "List-Unsubscribe" in mail.headers


# --- IMAP klasör adları (modified UTF-7) ------------------------------------


def test_klasor_adi_mutf7_kodlaniyor():
    """Sunucuya GİDEN klasör adı da modified UTF-7 olmalı (RFC 3501 §5.1.3).

    Sahada patladı: Gmail'in Türkçe çöp klasörü `[Gmail]/Çöp Kutusu`. Bir
    maili çöpe taşımak istendiğinde ad ham Unicode olarak `imaplib`e
    gidiyordu ve `UnicodeEncodeError` atıyordu; kullanıcı yalnızca
    "Internal Server Error" görüyordu. Kodda çözme vardı, kodlama yoktu —
    ASCII adlar sorunsuz geçtiği için hata görünmüyordu.
    """
    from app.mail.imap_client import _encode_folder_name

    # RFC 3501'in kendi örneği.
    assert _encode_folder_name("~peter/mail/台北/日本語") == "~peter/mail/&U,BTFw-/&ZeVnLIqe-"
    assert _encode_folder_name("[Gmail]/Çöp Kutusu") == "[Gmail]/&AMcA9g-p Kutusu"
    # ASCII adlar dokunulmadan geçmeli.
    assert _encode_folder_name("INBOX") == "INBOX"
    assert _encode_folder_name("[Gmail]/Spam") == "[Gmail]/Spam"
    # `&` özel karakter.
    assert _encode_folder_name("A&B") == "A&-B"


def test_klasor_adi_gidip_geliyor():
    """Kodla → çöz → aynı ad çıkmalı."""
    from app.mail.imap_client import _decode_folder_name, _encode_folder_name

    for name in ["INBOX", "[Gmail]/Çöp Kutusu", "Önemli", "A&B", "台北", "Arşiv/2026"]:
        encoded = _encode_folder_name(name)
        assert encoded.isascii(), f"{name!r} ASCII'ye inmedi: {encoded!r}"
        assert _decode_folder_name(encoded.encode("ascii")) == name


def test_quote_kodlanmis_adi_tirnakliyor():
    """`_quote` hem kodlamalı hem tırnaklamalı — `imaplib`e giden son hâli bu."""
    from app.mail.imap_client import _quote

    assert _quote("[Gmail]/Çöp Kutusu") == '"[Gmail]/&AMcA9g-p Kutusu"'
    assert _quote("INBOX") == '"INBOX"'
