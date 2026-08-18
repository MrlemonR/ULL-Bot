"""ICS okuma/yazma testleri (Faz 8).

`icalendar` paketi eklemedik, ayrıştırıcı elle yazıldı — bu yüzden RFC
5545'in bizi ilgilendiren köşeleri burada tek tek kilitleniyor: satır
katlama, metin kaçışları, DTEND yerine DURATION, TZID, tüm gün etkinlikler.
"""

from __future__ import annotations

from datetime import datetime

from app.calendar.ics import build_ics, parse_ics

SIMPLE = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:abc-123@example.com
SUMMARY:Ekip toplantısı
DESCRIPTION:Haftalık senkron\\nGündem: Faz 8
LOCATION:Toplantı Odası 2
DTSTART:20260820T120000Z
DTEND:20260820T130000Z
ATTENDEE;CN=Ahmet:mailto:ahmet@example.com
ATTENDEE;CN=Ayse:mailto:ayse@example.com
ORGANIZER:mailto:org@example.com
END:VEVENT
END:VCALENDAR
"""


def test_temel_vevent_okunur():
    events = parse_ics(SIMPLE)
    assert len(events) == 1
    event = events[0]
    assert event.uid == "abc-123@example.com"
    assert event.title == "Ekip toplantısı"
    assert event.location == "Toplantı Odası 2"
    # \n kaçışı gerçek satır sonuna çevrilmeli.
    assert "Gündem" in event.description
    assert "\n" in event.description
    assert event.attendees == ["ahmet@example.com", "ayse@example.com"]
    assert event.organizer == "org@example.com"
    assert not event.all_day


def test_utc_zamani_yerel_ofsete_cevrilir():
    event = parse_ics(SIMPLE)[0]
    parsed = datetime.fromisoformat(event.starts_at)
    assert parsed.tzinfo is not None
    # 12:00 UTC — hangi bölgede olursak olalım aynı ana denk gelmeli.
    assert parsed.utctimetuple().tm_hour == 12


def test_tum_gun_etkinlik():
    events = parse_ics(
        "BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:x\nSUMMARY:Tatil\n"
        "DTSTART;VALUE=DATE:20260901\nEND:VEVENT\nEND:VCALENDAR\n"
    )
    assert events[0].all_day
    assert events[0].starts_at.startswith("2026-09-01T00:00")
    # DTEND yoksa tüm gün etkinliği +1 gün sürer.
    assert events[0].ends_at.startswith("2026-09-02")


def test_duration_dtend_yerine_gecer():
    events = parse_ics(
        "BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:x\nSUMMARY:Kısa\n"
        "DTSTART:20260820T090000Z\nDURATION:PT45M\nEND:VEVENT\nEND:VCALENDAR\n"
    )
    start = datetime.fromisoformat(events[0].starts_at)
    end = datetime.fromisoformat(events[0].ends_at)
    assert (end - start).total_seconds() == 45 * 60


def test_dtend_yoksa_bir_saat_varsayilir():
    events = parse_ics(
        "BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:x\nSUMMARY:Y\n"
        "DTSTART:20260820T090000Z\nEND:VEVENT\nEND:VCALENDAR\n"
    )
    start = datetime.fromisoformat(events[0].starts_at)
    end = datetime.fromisoformat(events[0].ends_at)
    assert (end - start).total_seconds() == 3600


def test_satir_katlamasi_acilir():
    """RFC 5545 §3.1: katlama CRLF + TEK bir boşluk ekler, açma ikisini de siler.

    Yani `devam\\r\\n ediyor` → `devamediyor`. Kelimeler arasında boşluk
    kalması isteniyorsa katlanmış metinde İKİ boşluk olur (eklenen + asıl).
    Bu testin ikinci satırı bilerek iki boşlukla başlıyor.
    """
    folded = (
        "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:x\r\n"
        "SUMMARY:Çok uzun bir toplantı başlığı burada devam\r\n  ediyor ve bitiyor\r\n"
        "DTSTART:20260820T090000Z\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
    )
    assert parse_ics(folded)[0].title == "Çok uzun bir toplantı başlığı burada devam ediyor ve bitiyor"

    # Tek boşlukla katlanmış hâl gerçekten birleşik okunmalı.
    glued = folded.replace("devam\r\n  ediyor", "de\r\n vam ediyor")
    assert "devam ediyor" in parse_ics(glued)[0].title


def test_valarm_alanlari_etkinligin_alanlarini_ezmez():
    """Gerçek davetlerde VEVENT'in içinde VALARM var ve kendi DESCRIPTION'ı olur.

    Ayrıştırıcı iç bileşenleri atlamazsa etkinliğin açıklaması hatırlatıcının
    metniyle değişir — Google Calendar davetlerinde bu her seferinde olurdu.
    """
    payload = (
        "BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:x\n"
        "SUMMARY:Gerçek başlık\nDESCRIPTION:Gerçek açıklama\n"
        "DTSTART:20260820T090000Z\n"
        "BEGIN:VALARM\nACTION:DISPLAY\nTRIGGER:-PT10M\n"
        "SUMMARY:Alarm başlığı\nDESCRIPTION:Alarm açıklaması\nEND:VALARM\n"
        "END:VEVENT\nEND:VCALENDAR\n"
    )
    event = parse_ics(payload)[0]
    assert event.title == "Gerçek başlık"
    assert event.description == "Gerçek açıklama"


def test_rrule_tekrarlayan_isaretlenir():
    events = parse_ics(
        "BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:x\nSUMMARY:Haftalık\n"
        "DTSTART:20260820T090000Z\nRRULE:FREQ=WEEKLY;COUNT=10\nEND:VEVENT\nEND:VCALENDAR\n"
    )
    assert events[0].recurring


def test_tzid_ile_yerel_saat():
    events = parse_ics(
        "BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:x\nSUMMARY:Y\n"
        "DTSTART;TZID=Europe/Istanbul:20260820T150000\nEND:VEVENT\nEND:VCALENDAR\n"
    )
    parsed = datetime.fromisoformat(events[0].starts_at)
    assert parsed.hour == 15
    assert parsed.utcoffset().total_seconds() == 3 * 3600


def test_bilinmeyen_tzid_dusurmez():
    events = parse_ics(
        "BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:x\nSUMMARY:Y\n"
        "DTSTART;TZID=Mars/Olympus:20260820T150000\nEND:VEVENT\nEND:VCALENDAR\n"
    )
    assert events and events[0].starts_at  # etkinlik kayboldu mu diye


def test_bozuk_ics_bos_liste_dondurur():
    assert parse_ics("bu ics degil") == []
    assert parse_ics("") == []


def test_aciklamadaki_toplanti_baglantisi_bulunur():
    events = parse_ics(
        "BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:x\nSUMMARY:Y\n"
        "DESCRIPTION:Katıl: https://meet.google.com/abc-defg-hij\n"
        "DTSTART:20260820T090000Z\nEND:VEVENT\nEND:VCALENDAR\n"
    )
    assert "meet.google.com" in events[0].meeting_url


def test_birden_fazla_vevent():
    payload = SIMPLE.replace("END:VCALENDAR", "").rstrip() + (
        "\nBEGIN:VEVENT\nUID:ikinci\nSUMMARY:İkinci\n"
        "DTSTART:20260821T090000Z\nEND:VEVENT\nEND:VCALENDAR\n"
    )
    assert len(parse_ics(payload)) == 2


# --- yazma tarafı -----------------------------------------------------------


def test_build_ics_gidip_gelir():
    """Ürettiğimiz ICS'i kendi ayrıştırıcımız okuyabilmeli (round-trip)."""
    output = build_ics([
        {
            "id": 1,
            "uid": "test-1@ull-bot",
            "title": "Kahve; molası, kısa",   # kaçış gerektiren karakterler
            "description": "Satır1\nSatır2",
            "location": "Mutfak",
            "starts_at": "2026-08-20T15:00:00+03:00",
            "ends_at": "2026-08-20T15:30:00+03:00",
            "attendees": ["a@b.c"],
            "reminder_minutes": 10,
            "all_day": False,
        }
    ])
    assert "BEGIN:VCALENDAR" in output and "END:VCALENDAR" in output
    assert "BEGIN:VALARM" in output and "TRIGGER:-PT10M" in output

    parsed = parse_ics(output)[0]
    assert parsed.uid == "test-1@ull-bot"
    assert parsed.title == "Kahve; molası, kısa"
    assert parsed.description == "Satır1\nSatır2"
    assert parsed.location == "Mutfak"
    assert parsed.attendees == ["a@b.c"]


def test_build_ics_uzun_basligi_katlar_ve_geri_acilir():
    # Sondaki boşluk bilerek yok: ayrıştırıcı değerleri `strip()` ediyor
    # (gerçek ICS'lerde başta/sonda kaçak boşluk sık), o yüzden sonu
    # boşlukla biten bir başlık gidip gelirken o boşluğu kaybeder.
    title = ("Çok uzun bir başlık " * 8).strip()
    output = build_ics([{"id": 1, "title": title, "starts_at": "2026-08-20T15:00:00+03:00"}])
    assert "\r\n " in output  # katlandı
    assert parse_ics(output)[0].title == title


def test_build_ics_hatirlatma_kapaliysa_valarm_yazmaz():
    output = build_ics([
        {"id": 1, "title": "X", "starts_at": "2026-08-20T15:00:00+03:00", "reminder_minutes": -1}
    ])
    assert "BEGIN:VALARM" not in output


def test_build_ics_tum_gun_date_degeri_yazar():
    output = build_ics([
        {"id": 1, "title": "Tatil", "starts_at": "2026-09-01T00:00:00+03:00", "all_day": True}
    ])
    assert "DTSTART;VALUE=DATE:20260901" in output
    assert parse_ics(output)[0].all_day
