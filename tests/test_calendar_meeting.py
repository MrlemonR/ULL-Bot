"""Maildeki toplantıyı takvime çevirme (Faz 8).

İki yol test ediliyor ve ikisinin farkı önemli:

- ICS eki varsa **tahmin yok**, güven 1.0.
- Metinden çıkarımda güven skoru düşüyor ve UI bunu kullanıcıya gösteriyor.
  Buradaki testler o skorların anlamlı kaldığını kilitliyor: "20 Ağustos
  14:30" ile "salı" aynı güveni almamalı.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.calendar.service import EventDraft, draft_from_mail, extract_datetime, save_draft
from app.calendar.store import local_tz


# 2026-08-17 bir Pazartesi — göreli tarih testleri buna dayanıyor.
REFERENCE = datetime(2026, 8, 17, 10, 0, tzinfo=local_tz())


@pytest.fixture(autouse=True)
def temiz_db(tmp_path, monkeypatch):
    from app.db.connection import init_db
    from app.settings import settings

    monkeypatch.setattr(settings, "db_path", str(tmp_path / "test.db"))
    init_db()
    yield


def parse(text):
    return extract_datetime(text, reference=REFERENCE)


# --- sayısal tarihler -------------------------------------------------------


def test_nokta_ayirmali_tarih_ve_saat():
    value, confidence, _ = parse("Toplantı 20.08.2026 14:30'da başlıyor.")
    parsed = datetime.fromisoformat(value)
    assert (parsed.year, parsed.month, parsed.day) == (2026, 8, 20)
    assert (parsed.hour, parsed.minute) == (14, 30)
    assert confidence >= 0.85


@pytest.mark.parametrize(
    "metin,beklenen_saat",
    [
        # Türkçe'de tarih ile saat arasına neredeyse hep bir kelime giriyor.
        # Bu köprü kalıba girmezse saat sessizce kaybolur ve etkinlik
        # varsayılan 09:00'a düşer — kullanıcı bunu ancak yanlış saatte
        # bildirim gelince fark eder.
        ("20.08.2026 tarihinde saat 14:00'te buluşalım.", 14),
        ("Toplantı 20.08.2026 14:30'da", 14),
        ("20/08/2026, 09:15", 9),
        ("20 Ağustos 2026 günü saat 16:45", 16),
        ("20 Ağustos günü 11:00", 11),
        ("25 Agustos 2026 saat 13:00", 13),
        ("Yarın saat 15:00", 15),
        ("Perşembe günü saat 09:30", 9),
        ("Meeting on 20 August 2026 at 15:00", 15),
    ],
)
def test_tarih_saat_koprusu_varyantlari(metin, beklenen_saat):
    value, _, _ = parse(metin)
    assert value, f"tarih hiç bulunamadı: {metin!r}"
    assert datetime.fromisoformat(value).hour == beklenen_saat, f"saat kaçtı: {metin!r}"


def test_slash_ayirmali_tarih():
    value, _, _ = parse("Tarih: 05/09/2026 saat 09:00")
    parsed = datetime.fromisoformat(value)
    assert (parsed.month, parsed.day, parsed.hour) == (9, 5, 9)


def test_saatsiz_tarih_dusuk_guven_ve_varsayilan_saat():
    value, confidence, _ = parse("Etkinlik 20.08.2026 tarihinde.")
    assert datetime.fromisoformat(value).hour == 9  # DEFAULT_HOUR
    assert confidence < 0.85


def test_gecersiz_tarih_reddedilir():
    value, confidence, _ = parse("31.02.2026 14:00")
    assert value == ""
    assert confidence == 0.0


# --- ay adlı tarihler -------------------------------------------------------


def test_turkce_ay_adi():
    value, confidence, _ = parse("20 Ağustos 2026 saat 15:00'te görüşelim")
    parsed = datetime.fromisoformat(value)
    assert (parsed.month, parsed.day, parsed.hour) == (8, 20, 15)
    assert confidence >= 0.8


def test_turkce_ay_adi_noktasiz_yazim():
    value, _, _ = parse("12 Eylul 2026 11:00")
    assert datetime.fromisoformat(value).month == 9


def test_ingilizce_ay_adi():
    value, _, _ = parse("Meeting on 20 August 2026 at 15:00")
    parsed = datetime.fromisoformat(value)
    assert (parsed.month, parsed.day, parsed.hour) == (8, 20, 15)


def test_yil_yazilmamis_gecmis_tarih_gelecek_yila_kayar():
    # Referans 17 Ağustos 2026; "3 Mart" geçmişte kaldı → 2027 kastedilmiş.
    value, _, _ = parse("3 Mart günü 10:00")
    assert datetime.fromisoformat(value).year == 2027


def test_yil_yazilmamis_yakin_gelecek_ayni_yil():
    value, _, _ = parse("25 Ağustos 14:00")
    parsed = datetime.fromisoformat(value)
    assert parsed.year == 2026 and parsed.day == 25


# --- göreli ifadeler --------------------------------------------------------


def test_yarin_referansa_gore():
    value, _, _ = parse("Yarın 15:00'te toplantı var")
    parsed = datetime.fromisoformat(value)
    assert parsed.day == 18 and parsed.hour == 15


def test_bugun():
    value, _, _ = parse("Bugün saat 16:30")
    parsed = datetime.fromisoformat(value)
    assert parsed.day == 17 and parsed.hour == 16


def test_tomorrow_ingilizce():
    assert datetime.fromisoformat(parse("tomorrow at 09:00")[0]).day == 18


def test_hafta_gunu_gelecek_ilk_o_gun():
    # Referans Pazartesi; "Perşembe" → aynı haftanın Perşembesi (20 Ağustos).
    value, confidence, _ = parse("Perşembe 14:00'te görüşelim")
    parsed = datetime.fromisoformat(value)
    assert parsed.day == 20 and parsed.hour == 14
    # Hafta günü en zayıf sinyallerden — güven düşük olmalı.
    assert confidence <= 0.6


def test_onumuzdeki_hafta_gunu_bir_hafta_ileri():
    bu_hafta = datetime.fromisoformat(parse("Perşembe 14:00")[0])
    gelecek = datetime.fromisoformat(parse("önümüzdeki Perşembe 14:00")[0])
    assert (gelecek - bu_hafta).days == 7


def test_sadece_saat_en_dusuk_guven():
    value, confidence, reason = parse("Toplantı saat 15:00'te")
    assert value
    assert confidence <= 0.35
    assert "gün tahmin" in reason


def test_sadece_saat_gecmisse_ertesi_gune_kayar():
    # Referans 10:00; "09:00" bugün geçti → yarın.
    assert datetime.fromisoformat(parse("09:00")[0]).day == 18


def test_tarih_yoksa_bos_doner():
    value, confidence, reason = parse("Merhaba, nasılsın? Bir ara görüşelim.")
    assert value == ""
    assert confidence == 0.0
    assert "bulunamadı" in reason


# --- mailden taslak ---------------------------------------------------------


def make_mail(**kwargs):
    """Doğrudan mail_messages'a bir satır yaz (IMAP'siz)."""
    from app.mail import store as mail_store
    from app.mail.parser import ParsedMail

    mail_store.add_account(
        email="ben@example.com", host="imap.example.com", port=993, username="ben"
    )
    parsed = ParsedMail(
        subject=kwargs.get("subject", "Toplantı"),
        from_addr=kwargs.get("from_addr", "org@example.com"),
        from_name="Organizatör",
        body_text=kwargs.get("body_text", ""),
        ics_payload=kwargs.get("ics_payload", ""),
        date_ts=kwargs.get("date_ts", REFERENCE.isoformat()),
    )
    return mail_store.upsert_message(
        1, "INBOX", kwargs.get("uid", 1), parsed, seen=False, flagged=False, answered=False
    )


def test_ics_ekli_mail_tam_guvenle_okunur():
    message_id = make_mail(
        subject="Davet: Ekip toplantısı",
        ics_payload=(
            "BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:inv-1\nSUMMARY:Ekip toplantısı\n"
            "LOCATION:Oda 3\nDTSTART:20260820T120000Z\nDTEND:20260820T130000Z\n"
            "ATTENDEE:mailto:ahmet@example.com\nEND:VEVENT\nEND:VCALENDAR\n"
        ),
    )
    draft = draft_from_mail(message_id)
    assert draft.confidence == 1.0
    assert draft.title == "Ekip toplantısı"
    assert draft.location == "Oda 3"
    assert "ahmet@example.com" in draft.attendees
    assert "tahmin edilmedi" in draft.reason


def test_duz_metinli_mail_tahmin_yolunu_kullanir():
    message_id = make_mail(
        subject="Görüşme", body_text="20.08.2026 tarihinde saat 14:00'te buluşalım."
    )
    draft = draft_from_mail(message_id)
    assert draft.confidence < 1.0
    assert datetime.fromisoformat(draft.starts_at).hour == 14


def test_toplanti_baglantisi_guveni_artirir():
    message_id = make_mail(
        subject="Görüşme",
        body_text="20.08.2026 14:00. Katıl: https://meet.google.com/abc-defg-hij",
    )
    draft = draft_from_mail(message_id)
    assert "meet.google.com" in draft.meeting_url
    assert "bağlantı" in draft.reason


def test_tarihsiz_mail_bos_baslangic_dondurur():
    draft = draft_from_mail(make_mail(subject="Selam", body_text="Bir ara konuşalım"))
    assert draft.starts_at == ""
    assert draft.confidence == 0.0


def test_ayni_mailden_iki_kez_ekleme_tek_etkinlik_yapar():
    """Kullanıcı 'Takvime ekle'ye iki kez basarsa kopya oluşmamalı."""
    from app.calendar import store as calendar_store

    message_id = make_mail(subject="Toplantı", body_text="20.08.2026 14:00")
    draft = draft_from_mail(message_id)

    first = save_draft(draft)
    second = save_draft(draft)
    assert first["id"] == second["id"]
    assert len(calendar_store.list_events()) == 1


def test_baslangicsiz_taslak_kaydedilemez():
    with pytest.raises(ValueError):
        save_draft(EventDraft(title="X", starts_at=""))


# --- önizleme aracının sözleşmesi -------------------------------------------


def test_inspect_araci_kaydetmedigini_ve_sonraki_adimi_soyler():
    """Canlı testte model bu aracı çağırıp "ekledim" dedi — hiçbir şey eklememişti.

    Araç açıklaması tek başına yetmedi; çıktının kendisi de ne YAPMADIĞINI ve
    sıradaki çağrının ne olduğunu söylemeli (bkz. DECISIONS.md "Faz 8 kabul
    testi"). Bu test o metni kilitliyor.
    """
    from app.agent.tools import get_tool
    from app.agent.tools.base import ToolContext

    message_id = make_mail(subject="Toplantı", body_text="20.08.2026 saat 14:00")
    tool = get_tool("inspect_mail_meeting")
    ctx = ToolContext(cwd=__import__("pathlib").Path("/tmp"), session_id="t")

    result = tool.run(ctx, mail_id=message_id)
    assert result.ok
    assert "HİÇBİR ŞEY EKLEMEDİ" in result.output
    assert f"mail_to_event(mail_id={message_id})" in result.output
    assert "takvimde YOK" in result.output

    # Etkinlik gerçekten eklendikten sonra metin değişmeli.
    save_draft(draft_from_mail(message_id))
    again = tool.run(ctx, mail_id=message_id)
    assert "zaten takvimde" in again.output
    assert "mail_to_event" not in again.output.split("DURUM:")[1]


def test_metin_tahmininin_guveni_1_0_olamaz():
    """1.0 sadece ICS yolunun rozeti — UI "okundu" ile "tahmin" ayrımını buna bakarak yapıyor."""
    message_id = make_mail(
        subject="Görüşme",
        body_text="20.08.2026 tarihinde saat 14:00. Katıl: https://meet.google.com/abc-defg-hij",
    )
    draft = draft_from_mail(message_id)
    assert 0.9 <= draft.confidence <= 0.95, draft.confidence

    ics_id = make_mail(
        uid=2,
        subject="Davet",
        ics_payload=(
            "BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:i\nSUMMARY:X\n"
            "DTSTART:20260820T120000Z\nEND:VEVENT\nEND:VCALENDAR\n"
        ),
    )
    assert draft_from_mail(ics_id).confidence == 1.0
