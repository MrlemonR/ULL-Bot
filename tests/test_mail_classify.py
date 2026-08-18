"""Mail sınıflandırma testleri (Faz 8).

Kuralın en önemli özelliği ne yakaladığı değil, **hangi sırayla** yakaladığı:
takvim eki her şeyi yener, List-Unsubscribe konudaki "toplantı" kelimesini
yenmez ama gövdedeki anahtar kelimeleri yener. Testler bu sırayı kilitliyor —
kural eklerken sıra bozulursa burası kırılır.
"""

from __future__ import annotations

from app.mail.classify import CATEGORIES, classify, is_valid, label
from app.mail.parser import ParsedMail


def mail(**kwargs) -> ParsedMail:
    defaults = {
        "subject": "",
        "body_text": "",
        "from_addr": "kisi@example.com",
        "from_name": "Bir Kişi",
        "to_addrs": ["ben@example.com"],
        "headers": {},
    }
    return ParsedMail(**{**defaults, **kwargs})


def test_takvim_eki_her_seyi_yener():
    # Konu bülten gibi, başlık bülten gibi — ama ICS eki var.
    decision = classify(
        mail(
            subject="Haftalık bülten indirim kampanyası",
            ics_payload="BEGIN:VCALENDAR\nEND:VCALENDAR",
            headers={"List-Unsubscribe": "<mailto:x@y.z>"},
        )
    )
    assert decision.category == "toplanti"
    assert decision.confidence == 1.0
    assert not decision.needs_llm()


def test_list_unsubscribe_bulten_demektir():
    decision = classify(
        mail(subject="Yeni yazılar", headers={"List-Unsubscribe": "<https://x.com/u>"})
    )
    assert decision.category == "bulten"
    assert decision.confidence >= 0.9


def test_konudaki_fatura_bultenden_once_gelmez_ama_govdeden_once_gelir():
    # Konuda fatura + gövdede toplantı kelimeleri → fatura kazanmalı.
    decision = classify(
        mail(subject="Ağustos faturanız hazır", body_text="toplantı toplantı görüşme randevu")
    )
    assert decision.category == "fatura"


def test_konudaki_toplanti_yakalanir():
    decision = classify(mail(subject="Yarınki toplantı hakkında"))
    assert decision.category == "toplanti"
    assert not decision.needs_llm()


def test_noreply_gonderici_bildirim():
    decision = classify(mail(subject="Girişin yapıldı", from_addr="no-reply@github.com"))
    assert decision.category == "bildirim"


def test_noreply_ama_fatura_konusu_fatura_kalir():
    # Sıra önemli: fatura kontrolü otomatik gönderici kontrolünden ÖNCE.
    decision = classify(mail(subject="Ödeme makbuzunuz", from_addr="noreply@stripe.com"))
    assert decision.category == "fatura"


def test_auto_submitted_basligi_bildirim():
    decision = classify(mail(subject="Sistem raporu", headers={"Auto-Submitted": "auto-generated"}))
    assert decision.category == "bildirim"


def test_govdedeki_anahtar_kelimeler_dusuk_guvenle_kategori_verir():
    decision = classify(mail(subject="Selam", body_text="Proje raporu ve teslim tarihi için görev listesi"))
    assert decision.category == "is"
    assert decision.needs_llm()  # güven düşük — LLM'e sorulabilir


def test_toplanti_baglantisi_ve_konu_birlikte():
    decision = classify(
        mail(subject="Görüşme", body_text="Katıl: https://meet.google.com/abc-defg-hij")
    )
    assert decision.category == "toplanti"
    assert decision.confidence >= 0.85


def test_kisisel_tek_alici_ve_hitap():
    decision = classify(
        mail(subject="Naber", body_text="Merhaba, nasılsın? Sevgiler.", to_addrs=["ben@example.com"])
    )
    assert decision.category == "kisisel"


def test_sinyalsiz_mail_diger_ve_llm_ister():
    decision = classify(mail(subject="xyz", body_text="abc"))
    assert decision.category == "diger"
    assert decision.needs_llm()


def test_kategori_dogrulama():
    assert is_valid("toplanti")
    assert not is_valid("uydurma")
    assert not is_valid(None)
    assert label("fatura") == "Fatura / Ödeme"
    assert label("yok") == CATEGORIES["diger"]


# --- spam sürgünü -----------------------------------------------------------


def test_spam_klasorunden_gelen_spam():
    """Sunucunun kararı bizimkinden iyi — Gmail'in spam kutusu tartışmasız."""
    for folder in ("[Gmail]/Spam", "Junk", "Gereksiz E-posta", "INBOX.Spam"):
        decision = classify(mail(subject="Kazandınız!"), folder=folder)
        assert decision.category == "spam", folder
        assert decision.confidence == 1.0


def test_spam_klasoru_takvim_ekini_bile_yener():
    """Spam maildeki 'davet' ekine güvenilmez; sürgün her şeyin önünde."""
    decision = classify(
        mail(subject="Toplantı daveti", ics_payload="BEGIN:VCALENDAR\nEND:VCALENDAR"),
        folder="[Gmail]/Spam",
    )
    assert decision.category == "spam"


def test_sunucu_spam_basligi_okunur():
    decision = classify(mail(subject="X", headers={"X-Spam-Flag": "YES"}))
    assert decision.category == "spam"


def test_spam_basligi_no_ise_spam_degil():
    decision = classify(mail(subject="Toplantı", headers={"X-Spam-Flag": "NO"}))
    assert decision.category == "toplanti"


def test_normal_klasor_spam_yapmaz():
    assert classify(mail(subject="Toplantı"), folder="INBOX").category == "toplanti"


def test_is_spam_folder_tanimasi():
    from app.mail.classify import is_spam_folder

    assert is_spam_folder("[Gmail]/Spam")
    assert is_spam_folder("Önemsiz")
    assert not is_spam_folder("INBOX")
    assert not is_spam_folder("")


def test_spam_gecerli_bir_kategori():
    assert is_valid("spam")
    assert label("spam") == "Spam"


def test_spam_tumu_gorunumunden_haric():
    from app.mail.classify import HIDDEN_FROM_ALL

    assert "spam" in HIDDEN_FROM_ALL
    # Diğer hiçbir kategori gizlenmemeli — aksi hâlde mailler sessizce kaybolur.
    assert HIDDEN_FROM_ALL == {"spam"}
