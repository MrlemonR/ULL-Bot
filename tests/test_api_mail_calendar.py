"""Mail ve takvim REST uçları (Faz 8).

IMAP'e giden uçlar (senkron, işaretle, taşı) burada test EDİLMİYOR — gerçek
bir sunucu gerektiriyorlar ve sahtelemek protokolün kendisini değil
sahtelemenin doğruluğunu test etmek olurdu. Onlar canlı doğrulandı
(bkz. DECISIONS.md "Faz 8 kabul testi"). Burada test edilen şey: yerel
önbellekten okuyan ve takvimi yöneten uçların sözleşmesi.
"""

from __future__ import annotations

from types import SimpleNamespace

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    from app.settings import settings

    monkeypatch.setattr(settings, "db_path", str(tmp_path / "api.db"))
    # Arka plan döngüleri testte çalışmasın: mail senkronu ağa çıkar,
    # hatırlatıcı gerçek bildirim gönderirdi.
    monkeypatch.setattr(settings, "mail_sync_interval_seconds", 0)
    monkeypatch.setattr(settings, "notifications_enabled", False)

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


def iso(**kwargs) -> str:
    return (datetime.now().astimezone() + timedelta(**kwargs)).isoformat()


# --- config -----------------------------------------------------------------


def test_config_faz8_alanlarini_tasir(client):
    data = client.get("/api/config").json()
    assert "categories" in data and "toplanti" in data["categories"]
    assert "notifications" in data
    assert data["mail_accounts"] == 0
    assert "default_reminder_minutes" in data


# --- mail -------------------------------------------------------------------


def test_mail_hesap_listesi_bos_baslar(client):
    data = client.get("/api/mail/accounts").json()
    assert data["accounts"] == []
    assert data["secret_backend"] in ("libsecret", "file")


def test_mail_mesajlari_bos_liste_dondurur(client):
    data = client.get("/api/mail/messages").json()
    assert data["messages"] == []
    assert data["counts"]["total"] == 0


def test_olmayan_mail_404(client):
    assert client.get("/api/mail/messages/999").status_code == 404


def test_gecersiz_kategori_400(client):
    response = client.post("/api/mail/messages/1/category", json={"category": "uydurma"})
    assert response.status_code == 400
    assert "Bilinmeyen kategori" in response.json()["detail"]


def test_yanlis_bilgiyle_hesap_ekleme_400(client):
    """Bağlanamayan bir hesap kaydedilmemeli — her senkronda hata verirdi."""
    response = client.post(
        "/api/mail/accounts",
        json={
            "email": "x@example.invalid",
            "host": "imap.example.invalid",
            "port": 993,
            "username": "x",
            "password": "y",
        },
    )
    assert response.status_code == 400
    assert client.get("/api/mail/accounts").json()["accounts"] == []


# --- takvim -----------------------------------------------------------------


def test_etkinlik_olustur_listele_sil(client):
    created = client.post(
        "/api/calendar/events",
        json={"title": "Test toplantısı", "starts_at": "2026-08-20T15:00", "location": "Oda 1"},
    ).json()
    assert created["id"]
    assert created["starts_at"].startswith("2026-08-20T15:00")

    events = client.get("/api/calendar/events").json()
    assert len(events["events"]) == 1
    assert events["stats"]["total"] == 1

    assert client.delete(f"/api/calendar/events/{created['id']}").json()["ok"] is True
    assert client.get("/api/calendar/events").json()["events"] == []


def test_gecersiz_zamanla_etkinlik_400(client):
    response = client.post("/api/calendar/events", json={"title": "X", "starts_at": "bir ara"})
    assert response.status_code == 400


def test_etkinlik_guncelleme(client):
    created = client.post(
        "/api/calendar/events", json={"title": "A", "starts_at": "2026-08-20T15:00"}
    ).json()
    updated = client.patch(
        f"/api/calendar/events/{created['id']}", json={"title": "B", "location": "Yeni yer"}
    ).json()
    assert updated["title"] == "B"
    assert updated["location"] == "Yeni yer"
    assert updated["starts_at"] == created["starts_at"]  # dokunulmadı


def test_olmayan_etkinligi_guncelleme_404(client):
    assert client.patch("/api/calendar/events/999", json={"title": "X"}).status_code == 404


def test_aralik_filtresi(client):
    client.post("/api/calendar/events", json={"title": "Ağustos", "starts_at": "2026-08-20T15:00"})
    client.post("/api/calendar/events", json={"title": "Eylül", "starts_at": "2026-09-20T15:00"})
    found = client.get("/api/calendar/events?start=2026-08-01&end=2026-09-01").json()
    assert [event["title"] for event in found["events"]] == ["Ağustos"]


def test_upcoming_gecmisi_atlar(client):
    client.post("/api/calendar/events", json={"title": "Geçmiş", "starts_at": iso(hours=-3)})
    client.post("/api/calendar/events", json={"title": "Gelecek", "starts_at": iso(hours=3)})
    events = client.get("/api/calendar/upcoming").json()["events"]
    assert [event["title"] for event in events] == ["Gelecek"]


def test_ics_disa_aktarim(client):
    client.post(
        "/api/calendar/events",
        json={"title": "Dışa aktarılan", "starts_at": "2026-08-20T15:00", "reminder_minutes": 10},
    )
    response = client.get("/api/calendar/export.ics")
    assert response.status_code == 200
    assert "text/calendar" in response.headers["content-type"]
    assert "BEGIN:VCALENDAR" in response.text
    assert "SUMMARY:Dışa aktarılan" in response.text


def test_ics_ice_aktarim_ve_tekillestirme(client):
    payload = (
        "BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:import-1@x\nSUMMARY:İçe aktarılan\n"
        "DTSTART:20260820T120000Z\nEND:VEVENT\nEND:VCALENDAR\n"
    )
    first = client.post("/api/calendar/import", json={"ics": payload}).json()
    assert first["imported"] == 1

    # Aynı UID ikinci kez → kopya değil, güncelleme.
    client.post("/api/calendar/import", json={"ics": payload})
    assert len(client.get("/api/calendar/events").json()["events"]) == 1


def test_iptal_edilmis_davet_ice_aktarilmaz(client):
    payload = (
        "BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:x\nSUMMARY:İptal\nSTATUS:CANCELLED\n"
        "DTSTART:20260820T120000Z\nEND:VEVENT\nEND:VCALENDAR\n"
    )
    assert client.post("/api/calendar/import", json={"ics": payload}).json()["imported"] == 0


def test_bozuk_ics_ice_aktarimi_patlatmaz(client):
    assert client.post("/api/calendar/import", json={"ics": "çöp"}).json()["imported"] == 0


def test_olmayan_mailden_taslak_404(client):
    assert client.get("/api/calendar/draft-from-mail/999").status_code == 404


def test_bekleyen_toplantilar_bos_baslar(client):
    assert client.get("/api/calendar/pending-meetings").json()["pending"] == []


# --- bildirim ---------------------------------------------------------------


def test_bildirim_durumu(client):
    data = client.get("/api/notifications").json()
    assert data["enabled"] is False  # fixture kapattı
    assert "backend" in data
    assert data["pending"] == []


def test_bildirim_kapaliyken_test_ucu_basarisiz_ama_patlamaz(client):
    data = client.post("/api/notifications/test").json()
    assert data["ok"] is False
    assert "kapalı" in data["backend"]


def test_hatirlatmasi_olan_etkinlik_bekleyenlerde_gorunur(client):
    client.post(
        "/api/calendar/events",
        json={"title": "Yaklaşan", "starts_at": iso(hours=6), "reminder_minutes": 15},
    )
    pending = client.get("/api/notifications").json()["pending"]
    assert [event["title"] for event in pending] == ["Yaklaşan"]


def test_hatirlatmasi_kapali_etkinlik_bekleyenlerde_gorunmez(client):
    client.post(
        "/api/calendar/events",
        json={"title": "Sessiz", "starts_at": iso(hours=6), "reminder_minutes": -1},
    )
    assert client.get("/api/notifications").json()["pending"] == []


# --- spam "Tümü"den çıkarılıyor mu (Faz 8c) ---------------------------------


def _seed(client, monkeypatch, categories):
    """Verilen kategorilerle mail satırları yaz (IMAP'siz)."""
    from app.mail import store as mail_store
    from app.mail.parser import ParsedMail

    account_id = mail_store.add_account(
        email="ben@example.com", host="imap.example.com", port=993, username="ben"
    )
    for index, category in enumerate(categories, start=1):
        mail_store.upsert_message(
            account_id, "INBOX", index,
            ParsedMail(subject=f"{category} #{index}", from_addr="x@y.z",
                       date_ts="2026-08-17T10:00:00+00:00"),
            seen=False, flagged=False, answered=False,
            category=category, category_source="rule", category_reason="test",
        )
    return account_id


def test_spam_tumu_listesinde_gorunmez(client, monkeypatch):
    _seed(client, monkeypatch, ["bulten", "spam", "fatura", "spam"])
    data = client.get("/api/mail/messages").json()
    kategoriler = [m["category"] for m in data["messages"]]
    assert "spam" not in kategoriler
    assert len(data["messages"]) == 2


def test_spam_kategorisi_secilince_gorunur(client, monkeypatch):
    _seed(client, monkeypatch, ["bulten", "spam", "spam"])
    data = client.get("/api/mail/messages?category=spam").json()
    assert len(data["messages"]) == 2
    assert all(m["category"] == "spam" for m in data["messages"])


def test_okunmamis_gorunumu_de_spami_atlar(client, monkeypatch):
    """Spam 'Tümü'den çıkıp 'Okunmamış'ta kalsaydı sürgün yarım olurdu."""
    _seed(client, monkeypatch, ["bulten", "spam", "spam"])
    data = client.get("/api/mail/messages?unread=true").json()
    assert len(data["messages"]) == 1


def test_toplam_sayaci_spami_saymaz(client, monkeypatch):
    """Liste 2 gösterip rozet 4 deseydi kullanıcı 'mail kayboldu' derdi."""
    _seed(client, monkeypatch, ["bulten", "spam", "fatura", "spam"])
    counts = client.get("/api/mail/messages").json()["counts"]
    assert counts["total"] == 2
    assert counts["unread"] == 2
    # Kategori kırılımı spam'i İÇERİR — kendi satırının sayısı oradan geliyor.
    by_cat = {row["category"]: row["total"] for row in counts["categories"]}
    assert by_cat["spam"] == 2


def test_spam_kategorisi_elle_atanabilir(client, monkeypatch):
    _seed(client, monkeypatch, ["bulten"])
    message_id = client.get("/api/mail/messages").json()["messages"][0]["id"]
    client.post(f"/api/mail/messages/{message_id}/category", json={"category": "spam"})
    # Artık Tümü'de yok
    assert client.get("/api/mail/messages").json()["messages"] == []
    # Ama spam'de var
    assert len(client.get("/api/mail/messages?category=spam").json()["messages"]) == 1


def test_spam_config_kategorilerinde_var(client):
    assert client.get("/api/config").json()["categories"]["spam"] == "Spam"


# --- özet kuralları (Faz 10) ------------------------------------------------


def test_ozet_kurallari_crud(workspace) -> None:
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    assert client.get("/api/mail/rules").json() == {"rules": []}

    created = client.post("/api/mail/rules", json={"text": "Son fiyatı da yaz."}).json()
    assert created["text"] == "Son fiyatı da yaz." and created["enabled"] == 1

    assert client.patch(f"/api/mail/rules/{created['id']}", json={"enabled": False}).json()["ok"]
    assert client.get("/api/mail/rules").json()["rules"][0]["enabled"] == 0

    assert client.delete(f"/api/mail/rules/{created['id']}").json()["ok"]
    assert client.get("/api/mail/rules").json() == {"rules": []}


def test_bos_kural_reddediliyor(workspace) -> None:
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    assert client.post("/api/mail/rules", json={"text": "   "}).status_code == 400
    assert client.post("/api/mail/rules", json={"text": "x" * 401}).status_code == 400


async def test_ozet_kullanici_kurallarini_prompta_ekliyor(workspace, monkeypatch) -> None:
    """Kullanıcının kuralları promptun SONUNDA olmalı — sonda olan ezer.

    Kullanıcının somut isteği: "indirime giren oyunların son fiyatını da
    göstersin". Bunu her seferinde elle yazmak yerine kural olarak ekliyor.
    """
    from app.mail import service, store

    store.add_rule("İndirimli oyunların son fiyatını da yaz.")
    store.add_rule("Kapalı kural")
    kapali = store.list_rules()[1]
    store.set_rule_enabled(kapali["id"], False)

    from app.mail.parser import ParsedMail

    account_id = store.add_account(
        email="ben@example.com", host="imap.example.com", port=993, username="ben"
    )
    message_id = store.upsert_message(
        account_id, "INBOX", 1,
        ParsedMail(subject="Steam indirimi", from_addr="x@y.z",
                   body_text="Teardown %50 indirimde.",
                   date_ts="2026-08-18T10:00:00+00:00"),
        seen=False, flagged=False, answered=False,
    )

    seen: dict = {}

    async def fake_complete(messages, *, task_type="default", session_id=""):
        seen["content"] = messages[0]["content"]
        seen["task_type"] = task_type
        return SimpleNamespace(text="- Teardown %50 indirimde.", model="m", provider="p")

    monkeypatch.setattr(service, "complete_once", fake_complete)
    await service.summarize(message_id)

    assert "İndirimli oyunların son fiyatını da yaz." in seen["content"]
    assert "Kapalı kural" not in seen["content"], "kapalı kural prompta girmemeli"
    # Kurallar mail GÖVDESİNDEN önce, talimat bölümünde olmalı.
    assert seen["content"].index("son fiyatını") < seen["content"].index("<email")
    # Özetleme yerel modele düşmemeli (bozuk karakter sorunu).
    assert seen["task_type"] == "long_context"
