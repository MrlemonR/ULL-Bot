"""Takvim deposu ve hatırlatıcı seçimi (Faz 8).

`due_reminders()` bu dosyanın asıl konusu: yanlış çalışırsa ya bildirim hiç
gelmez ya da uygulama her açıldığında geçmiş etkinliklerin bildirimi patlar.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.calendar import store
from app.calendar.store import local_tz, normalize_dt


@pytest.fixture(autouse=True)
def temiz_db(tmp_path, monkeypatch):
    """Her test kendi veritabanında çalışsın."""
    from app.db.connection import init_db
    from app.settings import settings

    monkeypatch.setattr(settings, "db_path", str(tmp_path / "test.db"))
    init_db()
    yield


def now():
    return datetime.now(local_tz())


# --- normalize_dt -----------------------------------------------------------


def test_normalize_saat_dilimsiz_girdiye_yerel_ofset_ekler():
    result = normalize_dt("2026-08-20T15:00")
    assert result.startswith("2026-08-20T15:00")
    assert datetime.fromisoformat(result).tzinfo is not None


def test_normalize_bosluklu_bicimi_kabul_eder():
    assert normalize_dt("2026-08-20 15:00").startswith("2026-08-20T15:00")


def test_normalize_z_sonekini_utc_sayar():
    parsed = datetime.fromisoformat(normalize_dt("2026-08-20T12:00:00Z"))
    assert parsed.utcoffset().total_seconds() == 0


def test_normalize_sadece_tarih():
    assert normalize_dt("2026-08-20").startswith("2026-08-20T00:00")


def test_normalize_bozuk_girdi_bos_dondurur():
    assert normalize_dt("yarın") == ""
    assert normalize_dt("") == ""
    assert normalize_dt(None) == ""


# --- CRUD -------------------------------------------------------------------


def test_etkinlik_olustur_ve_oku():
    event = store.create_event(title="Toplantı", starts_at="2026-08-20T15:00")
    assert event["id"]
    assert event["title"] == "Toplantı"
    # Bitiş verilmediyse +1 saat.
    delta = datetime.fromisoformat(event["ends_at"]) - datetime.fromisoformat(event["starts_at"])
    assert delta == timedelta(hours=1)
    assert store.get_event(event["id"])["title"] == "Toplantı"


def test_tum_gun_etkinligin_bitisi_ertesi_gun():
    event = store.create_event(title="Tatil", starts_at="2026-09-01", all_day=True)
    delta = datetime.fromisoformat(event["ends_at"]) - datetime.fromisoformat(event["starts_at"])
    assert delta == timedelta(days=1)


def test_gecersiz_baslangic_hata_verir():
    with pytest.raises(ValueError):
        store.create_event(title="X", starts_at="bir ara")


def test_ayni_uid_ikinci_kayit_acmaz_gunceller():
    first = store.create_event(title="İlk", starts_at="2026-08-20T15:00", uid="sabit@x")
    second = store.create_event(title="Güncel", starts_at="2026-08-20T16:00", uid="sabit@x")
    assert first["id"] == second["id"]
    assert second["title"] == "Güncel"
    assert len(store.list_events()) == 1


def test_guncelleme_sadece_verilen_alanlari_degistirir():
    event = store.create_event(title="A", starts_at="2026-08-20T15:00", location="Ofis")
    updated = store.update_event(event["id"], title="B")
    assert updated["title"] == "B"
    assert updated["location"] == "Ofis"  # dokunulmadı


def test_silme():
    event = store.create_event(title="A", starts_at="2026-08-20T15:00")
    assert store.delete_event(event["id"]) is True
    assert store.get_event(event["id"]) is None
    assert store.delete_event(9999) is False


def test_aralik_sorgusu():
    store.create_event(title="İçeride", starts_at="2026-08-20T15:00")
    store.create_event(title="Dışarıda", starts_at="2026-09-20T15:00")
    found = store.list_events(start="2026-08-01", end="2026-09-01")
    assert [event["title"] for event in found] == ["İçeride"]


def test_arama():
    store.create_event(title="Diş randevusu", starts_at="2026-08-20T15:00")
    store.create_event(title="Ekip senkronu", starts_at="2026-08-21T15:00")
    assert len(store.list_events(query="randevu")) == 1


def test_kaynaga_gore_bulma():
    store.create_event(title="Mailden", starts_at="2026-08-20T15:00", source="mail", source_ref="42")
    assert store.find_by_source("mail", "42")["title"] == "Mailden"
    assert store.find_by_source("mail", "43") is None


# --- hatırlatıcı ------------------------------------------------------------


def test_hatirlatma_zamani_gelince_listeye_girer():
    # 5 dakika sonra başlayan, 10 dakika önceden hatırlatılacak etkinlik:
    # hatırlatma anı 5 dakika ÖNCE geçti, şimdi gönderilmeli.
    store.create_event(
        title="Yakın", starts_at=(now() + timedelta(minutes=5)).isoformat(), reminder_minutes=10
    )
    due = store.due_reminders()
    assert [event["title"] for event in due] == ["Yakın"]


def test_hatirlatma_zamani_gelmeyen_listeye_girmez():
    store.create_event(
        title="Uzak", starts_at=(now() + timedelta(hours=5)).isoformat(), reminder_minutes=10
    )
    assert store.due_reminders() == []


def test_gecmis_etkinlik_hatirlatilmaz():
    """Uygulama kapalıyken geçen etkinlikler açılışta bildirim yağmuru yapmasın."""
    store.create_event(
        title="Dün", starts_at=(now() - timedelta(days=1)).isoformat(), reminder_minutes=10
    )
    assert store.due_reminders() == []


def test_hatirlatma_kapaliysa_listeye_girmez():
    store.create_event(
        title="Sessiz", starts_at=(now() + timedelta(minutes=5)).isoformat(), reminder_minutes=-1
    )
    assert store.due_reminders() == []


def test_isaretlenen_hatirlatma_tekrar_gelmez():
    event = store.create_event(
        title="Tek sefer", starts_at=(now() + timedelta(minutes=5)).isoformat(), reminder_minutes=10
    )
    assert len(store.due_reminders()) == 1
    store.mark_reminded(event["id"])
    assert store.due_reminders() == []


def test_zaman_degisince_hatirlatma_sifirlanir():
    """Etkinlik ertelenirse yeni saat için bildirim yeniden kurulmalı."""
    event = store.create_event(
        title="Ertelenen", starts_at=(now() + timedelta(minutes=5)).isoformat(), reminder_minutes=10
    )
    store.mark_reminded(event["id"])
    assert store.due_reminders() == []

    store.update_event(event["id"], starts_at=(now() + timedelta(minutes=8)).isoformat())
    assert len(store.due_reminders()) == 1


def test_upcoming_gecmisi_atlar():
    store.create_event(title="Geçmiş", starts_at=(now() - timedelta(hours=2)).isoformat())
    store.create_event(title="Gelecek", starts_at=(now() + timedelta(hours=2)).isoformat())
    assert [event["title"] for event in store.upcoming()] == ["Gelecek"]


def test_istatistikler():
    store.create_event(title="Bugün", starts_at=(now() + timedelta(minutes=30)).isoformat())
    store.create_event(title="Gelecek ay", starts_at=(now() + timedelta(days=40)).isoformat())
    stats = store.stats()
    assert stats["total"] == 2
    assert stats["next_7_days"] == 1
