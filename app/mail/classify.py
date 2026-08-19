"""Mail kategorisi — kural tabanlı ilk geçiş.

Neden LLM değil: gelen her mail için model çağırmak kotanın en aptalca
harcanma yolu. Maillerin büyük çoğunluğu başlıklardan kesin olarak
sınıflanır — `List-Unsubscribe` varsa bülten, `text/calendar` eki varsa
toplantı davetı, `noreply@` göndericiyse otomatik bildirim. Bunlar kurala
sığar ve kural bedava.

LLM sadece **kararsız kalanlar** için devreye girer (`needs_llm()` true
dönenler) ve o çağrı `trivial` görev tipiyle yapılır (`config/routing.yaml`),
yani önce local model denenir. Bu, `app/router/classifier.py`nin sohbet
sınıflandırması için kurduğu düzenin aynısı: kural önce, LLM son çare.

Kategoriler Türkçe ve UI'da göründükleri gibi:
`kod`, `genel`, `bildirim`, `reklam`, `toplanti`, `fatura`, `is`,
`kisisel`, `diger`, `spam`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.mail.parser import ParsedMail

# Sıra UI'daki sırayla AYNI: kullanıcı "önemli mailler önde gelsin" dedi ve
# listeyi kendisi verdi — genel, aktivasyon kodu, reklam, bildirim.
CATEGORIES: dict[str, str] = {
    # Aktivasyon/doğrulama kodları. Zamana duyarlı ve tek kullanımlık: bir
    # giriş kodunu reklamların arasında aramak en can sıkıcı senaryo.
    "kod": "Aktivasyon Kodu",
    # Sipariş onayı, kargo/teslimat, dekont. Para zaten ödenmiş, bu bir
    # bilgilendirme. Ödeme İSTEYEN mailler `fatura`da.
    "genel": "Genel",
    # Hesabınla ilgili otomatik haberler: istek listesindeki oyun indirime
    # girdi, aboneliğin bitiyor, yeni oturum açıldı.
    "bildirim": "Bildirim",
    # Pazarlama: kampanya, indirim, bülten, duyuru.
    "reklam": "Reklam",
    "toplanti": "Toplantı",
    "fatura": "Fatura / Ödeme",
    "is": "İş",
    "kisisel": "Kişisel",
    "diger": "Diğer",
    "spam": "Spam",
}

# `bulten` 2026-08-18'de `reklam` oldu (kullanıcı "bunları reklam
# kategorisine koy" dedi). Eski satırlar için veri göçü:
# `app/db/connection.py` → `_apply_data_migrations`.
RENAMED: dict[str, str] = {"bulten": "reklam"}

# "Tümü" görünümünden ÇIKARILAN kategoriler.
#
# Kullanıcının isteği: "spam mailleri tümü kısmında görünmesin, en altta
# spam olarak ayrı görünsün". Spam bir filtre değil, bir sürgün: listeye
# hiç karışmıyor, yalnızca kendi kategorisi seçilince görünüyor.
HIDDEN_FROM_ALL: frozenset[str] = frozenset({"spam"})

# Bu eşiğin altındaki kararlar "kararsız" sayılır ve LLM'e sorulabilir.
CONFIDENT = 0.75


@dataclass
class MailCategory:
    category: str
    confidence: float
    reason: str
    source: str = "rule"

    def needs_llm(self) -> bool:
        return self.confidence < CONFIDENT


def _words(*parts: str) -> re.Pattern[str]:
    """Kelime sınırlı, Türkçe karakterlere duyarsız kalıp.

    `\\b` Türkçe harflerde beklendiği gibi çalışır (Python `re` Unicode
    modunda `ı`, `ş`, `ğ` kelime karakteridir), ama arama metnini de
    küçültüyoruz, o yüzden desenler küçük harfle yazılı.
    """
    return re.compile(r"\b(?:" + "|".join(parts) + r")\b", re.IGNORECASE | re.UNICODE)


TOPLANTI = _words(
    "toplantı", "toplanti", "toplantıya", "görüşme", "gorusme", "randevu",
    "meeting", "invite", "invitation", "davet", "davetiye", "appointment",
    "calendar", "takvim", "call", "sunum", "demo", "standup", "sync",
    "webinar", "seminer", "mülakat", "mulakat", "interview",
)
FATURA = _words(
    "fatura", "faturanız", "ödeme", "odeme", "tahsilat", "makbuz", "dekont",
    "invoice", "receipt", "payment", "billing", "bill", "subscription",
    "abonelik", "son ödeme", "borç", "borc", "tutar", "ekstre", "iade",
    "refund", "charged", "ücretlendir", "ucretlendir",
)
IS = _words(
    "proje", "rapor", "teslim", "deadline", "termin", "görev", "gorev",
    "task", "ticket", "issue", "pull request", "merge request", "deploy",
    "sözleşme", "sozlesme", "teklif", "onay", "revize", "brief", "sprint",
    "müşteri", "musteri", "client", "iş", "çalışma", "toplantı notu",
)
REKLAM = _words(
    "bülten", "bulten", "newsletter", "haftalık", "haftalik", "digest",
    "abonelikten çık", "unsubscribe", "duyuru", "kampanya", "indirim",
    "fırsat", "firsat", "promo", "promosyon", "sale", "offer", "deal",
    "blog", "yeni yazı", "weekly", "monthly",
)

# --- Aktivasyon / doğrulama kodları -----------------------------------------
#
# Kullanıcının şikâyeti netti: "verify your email"ler Bildirim'e düşüyordu ve
# giriş kodunu bulmak için listeyi taramak gerekiyordu. Bunlar kendi
# kategorisinde ve en üstte.
KOD = _words(
    "doğrulama kodu", "dogrulama kodu", "giriş kodu", "giris kodu",
    "onay kodu", "aktivasyon kodu", "aktivasyon", "güvenlik kodu",
    "guvenlik kodu", "tek kullanımlık", "tek kullanimlik", "şifre sıfırlama",
    "sifre sifirlama", "verification code", "confirmation code",
    "security code", "login code", "sign-in code", "access code",
    "one-time", "one time password", "otp", "2fa", "two-factor",
    "verify your email", "verify your account", "confirm your email",
    "confirm your account", "activate your account", "reset your password",
    "e-postanı doğrula", "e-postani dogrula", "hesabını doğrula",
    "hesabini dogrula", "kodunuz", "kodun",
)

# Gövdedeki asıl kod: 4-8 haneli rakam ya da harf+rakam karışımı bir blok.
# Kelime sınırı şart — "2026" gibi yıl ya da fiyat yakalamasın diye
# `_find_code` ayrıca bağlama bakıyor.
_CODE_TOKEN = re.compile(r"\b([0-9]{4,8}|[A-Z0-9]{5,8})\b")
# Anahtar kelime ile kodun arasına birkaç kelime girebiliyor:
# "enter the code below:\n002834". O yüzden araya bakmıyoruz, sadece kodun
# ÖNCESİNDEKİ kısa pencerede bir kod kelimesi arıyoruz.
_CODE_CONTEXT = re.compile(
    r"(kod(?:u|un|unuz|umuz)?|code|otp|pin|şifre|sifre|password|token)",
    re.IGNORECASE | re.UNICODE,
)


def find_code(text: str) -> str | None:
    """Doğrulama kodunu gövdeden çıkar (UI'daki "Kodu kopyala" düğmesi için).

    Sadece bir "kod/code/OTP" kelimesinin hemen ardından gelen bloklar
    sayılıyor; aksi hâlde tarih, fiyat ve sipariş numarası da eşleşirdi.
    """
    if not text:
        return None
    for match in _CODE_TOKEN.finditer(text[:4000]):
        before = text[max(0, match.start() - 60):match.start()]
        if _CODE_CONTEXT.search(before):
            return match.group(1)
    return None


# --- Sipariş / kargo (Genel) ------------------------------------------------
# `_words` kelime sınırı koyuyor ama Türkçe ekler onu bozuyor:
# "sipariş" deseni "siparişi" ile EŞLEŞMEZ. Bu yüzden gövdeleri son ek
# serbestliğiyle yazıyoruz (`sipariş\w*`).
GENEL = re.compile(
    r"\b(?:"
    r"sipariş\w*|siparis\w*|kargo\w*|teslim\w*|dekont\w*|makbuz\w*|"
    r"takip numaras\w*|gönderi\w*|gonderi\w*|"
    r"order(?:s|ed|ing)?|shipp\w*|deliver\w*|tracking|dispatched"
    r")\b",
    re.IGNORECASE | re.UNICODE,
)

# --- Hesapla ilgili bildirimler ---------------------------------------------
#
# Bunlar "indirim" kelimesi geçse bile REKLAM DEĞİL: kullanıcının kendi
# hesabına/listesine dair. Kullanıcının verdiği örnekler: "istek listendeki
# oyun indirime girdi", "aboneliğinin yenilenmesine şu kadar kaldı".
BILDIRIM_HESAP = _words(
    "istek listen", "istek listendeki", "istek listesi", "wishlist",
    "aboneliğin", "aboneligin", "aboneliğiniz", "abonelik yenileme",
    "subscription", "renew", "renewal", "expires", "expiring", "ending soon",
    "hesabınız", "hesabiniz", "hesabında", "hesabinda", "your account",
    "güvenlik uyarısı", "guvenlik uyarisi", "security alert",
    "new sign-in", "yeni oturum", "oturum açma", "oturum acma",
)

# Otomatik gönderici kalıpları — kişisel mail olma ihtimali sıfıra yakın.
AUTOMATED_LOCALPART = re.compile(
    r"^(no-?reply|do-?not-?reply|donotreply|bounce|mailer-daemon|postmaster|"
    r"notifications?|alerts?|bildirim|auto|system|robot|bot|info|destek|support|"
    r"newsletter|news|hello|team|noc)([+.-]|$)",
    re.IGNORECASE,
)

# Kişiselliği güçlendiren işaretler: doğrudan bana yazılmış, tek alıcı.
PERSONAL_HINT = _words(
    "merhaba", "selam", "naber", "nasılsın", "nasilsin", "teşekkür",
    "tesekkur", "sevgiler", "görüşürüz", "gorusuruz", "canım", "hocam",
)


# Sunucunun kendi spam kararı — bizimkinden daha iyisi. Gmail/Outlook bu
# başlıkları mesaja ekliyor.
SPAM_HEADERS = ("X-Spam-Flag", "X-Spam-Status", "X-Gm-Spam", "X-Gm-Phishy")


def is_spam_folder(folder: str) -> bool:
    """Klasör adı sunucunun spam kutusuna mı işaret ediyor?

    Gerçek tespit `imap_client.Folder.is_junk` ile RFC 6154 bayrağından
    yapılıyor; bu, elimizde yalnızca klasör ADI olan yerler için yedek.
    """
    lowered = (folder or "").lower()
    return any(
        needle in lowered
        for needle in ("spam", "junk", "gereksiz", "önemsiz", "onemsiz", "bulk mail")
    )


def classify(mail: ParsedMail, *, own_address: str = "", folder: str = "") -> MailCategory:
    """Kural tabanlı kategori kararı.

    Sıra önemli: en kesin sinyal (takvim eki) en başta, en zayıf tahmin
    (anahtar kelime sayımı) en sonda. İlk eşleşen kazanır.
    """
    subject = mail.subject or ""
    body = (mail.body_text or "")[:4000]
    haystack = f"{subject}\n{body}"
    sender = mail.from_addr or ""
    localpart = sender.split("@", 1)[0] if "@" in sender else sender

    # 0. Spam her şeyin önünde: sunucu zaten karar verdiyse ona uyuyoruz.
    #    Bizim kurallarımız Gmail'in spam filtresinden iyi değil ve bir spam
    #    maili "fatura" diye sınıflayıp listeye sokmak, kullanıcının tam da
    #    istemediği şey.
    if folder and is_spam_folder(folder):
        return MailCategory("spam", 1.0, f"Sunucunun spam klasöründen geldi ({folder}).")
    for header in SPAM_HEADERS:
        value = mail.headers.get(header, "")
        if value and value.strip().upper().startswith(("YES", "TRUE")):
            return MailCategory("spam", 0.95, f"Sunucu spam işaretledi ({header}: {value[:40]}).")

    # 1. Takvim eki — tartışmasız toplantı.
    if mail.ics_payload:
        return MailCategory("toplanti", 1.0, "Mailde takvim daveti (text/calendar) eki var.")

    # 1b. Aktivasyon/doğrulama kodu — bültenden de bildirimden de ÖNCE.
    #
    # Sıra kritik: bu maillerin çoğu `noreply@` adresinden gelir (adım 6
    # onları "bildirim" yapardı) ve bazılarında `List-Unsubscribe` başlığı
    # bile vardır (adım 3 "reklam" yapardı). Kullanıcının şikâyeti tam
    # buydu: "verify your email"ler Bildirim'e düşüyor.
    if KOD.search(subject):
        code = find_code(body)
        detail = f" (kod: {code})" if code else ""
        return MailCategory("kod", 0.95, f"Konu satırında doğrulama/giriş kodu ifadesi{detail}.")
    if KOD.search(body) and find_code(body):
        return MailCategory("kod", 0.85, "Gövdede kod ifadesi ve kodun kendisi bulundu.")

    # 1c. Sipariş / kargo / dekont — "Genel".
    #
    # Bu da `List-Unsubscribe` kontrolünden ÖNCE olmalı: mağazaların sipariş
    # ve kargo mailleri neredeyse her zaman o başlığı taşıyor ve eskiden
    # hepsi bültene düşüyordu ("KAFT — Siparişin Teslim Edildi" gibi).
    if GENEL.search(subject):
        return MailCategory("genel", 0.88, f"Konu satırında sipariş/kargo ifadesi: {subject[:60]!r}")

    # 1d. Hesabınla ilgili bildirim — reklamdan ÖNCE.
    #
    # "İstek listendeki oyun indirime girdi" maili "indirim" kelimesi
    # yüzünden reklama düşerdi; oysa kullanıcı bunu Bildirim'de istiyor.
    # Ayırt edici şey konunun SENİN hesabın/listen olması.
    if BILDIRIM_HESAP.search(haystack):
        return MailCategory("bildirim", 0.85, "Hesabınla/listenle ilgili otomatik bildirim.")

    # 2. Toplantı bağlantısı + konuda toplantı kelimesi.
    if mail.meeting_urls and TOPLANTI.search(haystack):
        return MailCategory(
            "toplanti", 0.92,
            f"Toplantı bağlantısı ({mail.meeting_urls[0].split('/')[2]}) ve konu eşleşmesi.",
        )

    # 3. Bülten: List-Unsubscribe başlığı standart ve güvenilir.
    if "List-Unsubscribe" in mail.headers or "List-Id" in mail.headers:
        return MailCategory(
            "reklam", 0.9, "List-Unsubscribe/List-Id başlığı var — toplu gönderim listesi."
        )

    # 4. Fatura: konu satırında geçmesi gövdede geçmesinden çok daha güçlü.
    if FATURA.search(subject):
        return MailCategory("fatura", 0.88, f"Konu satırında ödeme/fatura ifadesi: {subject[:60]!r}")

    # 5. Toplantı: konu satırında.
    if TOPLANTI.search(subject):
        return MailCategory("toplanti", 0.85, f"Konu satırında toplantı ifadesi: {subject[:60]!r}")

    # 6. Otomatik gönderici → bildirim (fatura/toplantı yukarıda elendi).
    if AUTOMATED_LOCALPART.match(localpart):
        return MailCategory("bildirim", 0.8, f"Otomatik gönderici adresi: {sender}")
    if mail.headers.get("Auto-Submitted", "").lower() not in ("", "no"):
        return MailCategory("bildirim", 0.8, "Auto-Submitted başlığı — makine üretimi mesaj.")
    if mail.headers.get("Precedence", "").lower() in ("bulk", "list", "junk"):
        return MailCategory("reklam", 0.78, "Precedence: bulk/list başlığı.")

    # 7. Gövdedeki anahtar kelimeler — buradan sonrası tahmin, güven düşük.
    scores = {
        "fatura": len(FATURA.findall(haystack)),
        "toplanti": len(TOPLANTI.findall(haystack)),
        "reklam": len(REKLAM.findall(haystack)),
        "genel": len(GENEL.findall(haystack)),
        "is": len(IS.findall(haystack)),
    }
    best = max(scores, key=lambda key: scores[key])
    if scores[best] >= 2:
        return MailCategory(
            best, 0.6, f"Gövdede {scores[best]} '{CATEGORIES[best]}' anahtar kelimesi."
        )

    # 8. Kişisel: az alıcı + insan gibi bir gönderici + selamlaşma.
    single_recipient = len(mail.to_addrs) <= 1 and not mail.cc_addrs
    if single_recipient and PERSONAL_HINT.search(haystack) and not AUTOMATED_LOCALPART.match(localpart):
        return MailCategory("kisisel", 0.65, "Tek alıcı ve kişisel hitap ifadesi.")

    return MailCategory("diger", 0.3, "Belirgin bir sinyal yok — kural karar veremedi.")


def label(category: str | None) -> str:
    return CATEGORIES.get(category or "diger", CATEGORIES["diger"])


def is_valid(category: str | None) -> bool:
    return category in CATEGORIES
