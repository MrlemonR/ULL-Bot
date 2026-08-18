# Faz 8 teslim notu — masaüstü uygulaması, mail ve takvim

Bu dosya **UI'ın kurulduğu** fazın devir notu (2026-08-17). Faz 7 bittiğinde
"backend tamam, UI hiç yok" durumu vardı; bu faz onu kapattı ve iki yeni
alan ekledi: **IMAP mail** ve **uygulamanın kendi takvimi**.

Okuma sırası:

0. **[`FAZ9_TESLIM.md`](./FAZ9_TESLIM.md) DAHA YENİ** — web araştırma,
   spam sürgünü ve HTML mail render'ı orada. Bu dosyadaki bazı ayrıntılar
   (özellikle Google OAuth) sonradan değişti.
1. **Bu dosya** — ne eklendi, nasıl çalıştırılır, hangi kararlar verildi.
2. [`FAZ7_TESLIM.md`](./FAZ7_TESLIM.md) — WebSocket protokolü ve Faz 1-7
   REST uçlarının referansı. **Değişmedi**, hâlâ geçerli.
3. [`DECISIONS.md`](./DECISIONS.md) — "neden böyle" arşivi.
4. [`NEXT_PHASE.md`](./NEXT_PHASE.md) — sıradaki iş.

---

## 1. Kullanıcının isteği ve nasıl karşılandı

> "şuan hiçbir servis açık değil, servisleri uygulama açılınca açılıp uygulama
> kapanınca kapanmasını istiyorum … chat kısmını hazırla, chat kısmının yanına
> mail ve calendar ekle, bu sayede mailleri okuyup ayıracak, eğer istersem
> özetleyecek, meetingler için de calendara meeting ekleyecek"

Netleştirme turunda verilen kararlar:

- **Mail: IMAP + uygulama parolası.** (Faz 8b'de bir süre "Google ile
  giriş" (OAuth) da vardı ama **KALDIRILDI** — Google, kısıtlı
  `mail.google.com` kapsamı için doğrulanmamış uygulamaların
  yetkilendirmelerini 7 günde bir iptal ediyor. Aşağıdaki §8 o dönemin
  kaydı; geçerli durum için DECISIONS.md → "Google OAuth, kişisel Gmail
  için pratik değil".)
- **Takvim: uygulamanın kendisi.** Google Calendar/CalDAV yok. Mail'deki
  toplantı buradan düzenleniyor.
- **Bildirim: OS'un kendi sistemi.** Bu makinede dunst. Kendi bildirim
  penceremizi çizmiyoruz.
- **Telefona mesaj: sonraya bırakıldı.**
- **Düzen:** Sohbet kendi tam ekran paneli; Mail ve Takvim'e geçince yanda
  bağlamlı bir sohbet kutusu açılıyor.

---

## 2. Çalıştırma — artık iki yol var

### a) Masaüstü uygulaması (yeni, istenen davranış)

```bash
./scripts/ull-bot          # ya da uygulama menüsünden "ULL-Bot"
```

Native bir pencere açılır (pywebview + WebKitGTK, tarayıcı değil). Açılışta
LiteLLM (:4000) ve FastAPI (:8080) çocuk süreç olarak başlar, pencere
kapanınca ikisi de durur.

### b) systemd (Faz 7, değişmedi)

```bash
systemctl --user enable --now ull-bot.target
```

**İkisi çakışmaz.** Süpervizör bir portu dinleyen bir şey görürse o servisi
"benimser": başlatmaz ve kapanışta **durdurmaz**. Yani systemd servisleri
açıkken uygulamayı kapatmak onları öldürmez.

`./scripts/install.sh` artık `.desktop` kısayolunu da kuruyor ama —Faz 7'deki
ilkeye sadık kalarak— **hiçbir şeyi enable/start etmiyor**.

### Servissiz hata ayıklama

```bash
uv run python -m app.desktop.supervisor   # pencere açmadan servisleri kaldır
```

---

## 3. Yeni REST uçları

Faz 1-7 uçları **değişmedi**. `GET /api/config`e alan EKLENDİ (kırıcı değil):
`mail_accounts`, `mail_sync_interval`, `notifications`, `categories`,
`default_reminder_minutes`.

### Mail

```
GET    /api/mail/accounts                      → {accounts, secret_backend}
POST   /api/mail/accounts/test                 → bağlantıyı dene, klasörleri dön
POST   /api/mail/accounts                      → doğrula + parolayı sakla + kaydet
DELETE /api/mail/accounts/{id}
GET    /api/mail/accounts/{id}/folders

POST   /api/mail/sync?account_id=              → IMAP'ten yeni mailleri çek
GET    /api/mail/messages?category=&unread=&flagged=&q=&limit=&offset=
GET    /api/mail/messages/{id}                 → gövde dahil tam kayıt
POST   /api/mail/messages/{id}/mark            → {seen?, flagged?}  (IMAP'e de yazar)
POST   /api/mail/messages/{id}/move            → {destination}  __trash__/__archive__/__junk__
POST   /api/mail/messages/{id}/category        → {category}
POST   /api/mail/messages/{id}/summarize?force=
POST   /api/mail/categorize?limit=             → kararsızları modele sor
```

**Okuma uçları yerel SQLite önbelleğinden döner, IMAP'e gitmez.** Ağa çıkan
tek uçlar: `sync`, `mark`, `move`, hesap kurulumu.

Kategoriler: `toplanti`, `is`, `fatura`, `bulten`, `bildirim`, `kisisel`,
`diger`. Etiketleri `/api/config`in `categories` alanından oku, sabitleme.

### Takvim

```
GET    /api/calendar/events?start=&end=&q=     → {events, stats}
GET    /api/calendar/upcoming?limit=&days=
POST   /api/calendar/events                    → title + starts_at zorunlu
PATCH  /api/calendar/events/{id}
DELETE /api/calendar/events/{id}
GET    /api/calendar/export.ics
POST   /api/calendar/import                    → {ics}
GET    /api/calendar/pending-meetings          → takvime alınmamış toplantı mailleri
GET    /api/calendar/draft-from-mail/{mail_id} → KAYDETMEDEN çıkar (onay ekranı)
POST   /api/calendar/from-mail/{mail_id}       → kaydet (gövdeyle alan ezilebilir)
```

Zaman biçimi: **ofsetli ISO8601** (`2026-08-20T15:00:00+03:00`). Saat
dilimsiz gönderirsen sistemin yerel dilimi varsayılır.

### Bildirim

```
GET  /api/notifications        → {enabled, backend, available, poll_seconds, pending[]}
POST /api/notifications/test
```

---

## 4. Yeni ajan araçları (13 tane)

| Araç | Risk | Not |
|---|---|---|
| `list_mail` | safe | önbellekten, filtreli |
| `read_mail` | safe | **çıktı `untrusted`** |
| `sync_mail` | safe | IMAP'ten çeker |
| `summarize_mail` | safe | özet önbelleklenir |
| `mark_mail` | safe | geri alınabilir |
| `categorize_mail` | safe | sadece yerel etiket |
| `move_mail` | **confirm** | sunucuda gerçekleşir, dry-run'a uyar |
| `list_events` | safe | |
| `create_event` | safe | mutlak `starts_at` ister |
| `update_event` | safe | |
| `delete_event` | **confirm** | geri alınamaz |
| `mail_to_event` | safe | ICS varsa tahmin yok |
| `inspect_mail_meeting` | safe | kaydetmeden gösterir |

**Risk modelinin mantığı:** uygulamanın kendi SQLite'ına yazmak `remember`
ile aynı kategoride sayıldı (`safe`). `confirm` olanlar dış dünyada
(IMAP sunucusu) ya da geri alınamaz şekilde iş yapanlar.

**Sistem promptu değişti:** artık bugünün tarihi/saati/günü var (takvim
araçları mutlak zaman istiyor, model "yarın"ı ancak bugünü bilirse çevirir)
ve mail içeriğinin düşman girdi olduğu ayrıca yazılı.

---

## 5. UI'ın yapısı

`web/index.html` + `web/style.css` + `web/js/*.js` (ES modülleri, derleme
adımı yok — projede node bağımlılığı bilerek yaratılmadı).

```
web/js/app.js       kabuk: sol şerit, görünüm değişimi, onay diyalogları, dock'lar
web/js/chat.js      ChatCore (tek WebSocket) + ChatView (çok örnekli görünüm)
web/js/mail.js      filtre şeridi + liste + detay + takvime ekleme diyaloğu
web/js/calendar.js  ay ızgarası + seçili gün + bekleyen toplantılar
web/js/panels.js    kota, geçmiş, ayarlar
web/js/api.js       REST sarmalayıcı
web/js/util.js      kaçış, markdown, tarih biçimleme, toast, modal
```

**Üç sohbet yüzeyi tek `ChatCore`u paylaşır** — Sohbet paneli, mail dock'u ve
takvim dock'u aynı oturumu gösterir. Mail'de "bunu özetle" deyip Sohbet'e
geçince konuşmanın devamı orada.

**Bağlam görünürdür:** bir mail seçiliyken dock'ta bir çip belirir ve
gönderilen mesajın başına `[Bağlam: mail #12 — konu]` eklenir. Gizli prompt
enjeksiyonu yapılmıyor; kullanıcı ne gönderildiğini birebir görüyor.

FAZ7_TESLIM.md §4'teki davranış kurallarının hepsi uygulandı; özellikle
§4.7 (aynı adımda `model_switch` gelince token arabelleğini sıfırla) —
`chat.js` → `ChatView.resetStream()`.

---

## 6. Bozulmaması gerekenler (Faz 7 listesine ek)

15. **Mail içeriği düşman girdidir.** `read_mail`/`list_mail` çıktısı her
    zaman `untrusted=True` dönmeli. Özetleme istemi de maili
    `<email untrusted="true">` bloğuna sarıyor.
16. **IMAP parolası SQLite'a yazılmaz.** `mail_accounts.secret_backend`
    sadece "parola nerede" der. Parola libsecret'ta, o yoksa `data_dir`
    altında 0600 bir dosyada.
17. **Yazma önce IMAP'e, sonra önbelleğe.** Ters sırada yapılırsa sunucuya
    ulaşılamayan bir anda UI gerçekte olmayan bir durumu gösterir.
18. **Süpervizör benimsediği servisi öldürmez.** Açmadığımız şeyi
    kapatmıyoruz (systemd kullanıcıları için).
19. **Güven 1.0 sadece ICS yolunun rozeti.** Metinden çıkarım tavanı 0.95;
    UI "okundu" ile "tahmin edildi" ayrımını buna bakarak yapıyor.
20. **Kural tabanlı sınıflandırıcı önce, LLM sadece kararsızlara.** Gelen
    her mail için model çağırmak kotanın en aptalca harcanma yolu.

---

## 7. Bilinen sınırlar

- **Tekrarlayan etkinlik (RRULE) yok.** ICS'te RRULE varsa sadece ilk oluşum
  eklenir ve kullanıcıya "bu tekrarlayan bir seri" denir.
- **Mail gönderme/cevaplama yok.** Sadece okuma + işaretleme + taşıma.
- **Takvim telefona senkronlanmaz** (kullanıcının kararı). Dışa aktarım ICS.
- **HTML mailler kaba metne indirgenir** — `parser.html_to_text` regex
  tabanlı, tablo düzenini korumaz.
- **Doğal dil tarih çıkarımı Türkçe/İngilizce ve kural tabanlı.** Kapsamı
  `tests/test_calendar_meeting.py`de sabitlendi; kaçırdığı bir biçim varsa
  önce oraya bir vaka eklenmeli.
- **Faz 7'den devralınan boşluk:** `GET /api/quota` hâlâ `gemini_lite` ve
  `ollama`yı göstermiyor (`describe_chain()` `task_type`sız çağrılıyor).

---

## 8. Faz 8b — Google hesabı ekleme (2026-08-17, kullanıcı isteği)

Kullanıcı ikinci bir adresi (`@trendbox.io`, Google Workspace) eklemek
isteyince "hesap ekleme Google'ın sayfasına da yönlendirsin" dedi ve
"ikisi de olsun" seçti. Eklenen iki yol:

### a) Uygulama parolası + doğrudan Google linki (kurulum gerektirmez)

`web/js/accounts.js` — hesap ekleme diyaloğu yeniden yazıldı:

- E-posta yazıldıkça sunucu/port alan adından tahmin ediliyor
  (`gmail.com`, `outlook.com`, `yandex`, `yahoo`, `icloud` + genel
  `imap.<alanadı>` yedeği).
- Google adresi ya da tanınmayan bir alan adı (= Workspace olabilir)
  girilince Google bölümü açılıyor: parola etiketi "Uygulama parolası"
  oluyor ve **"Google'da uygulama parolası oluştur ↗"** düğmesi
  `myaccount.google.com/apppasswords?authuser=<adres>` adresini sistem
  tarayıcısında açıyor — `authuser` sayesinde doğru hesap ön seçili.
- **Workspace tuzağı:** özel alan adında sunucu `imap.<alanadı>` tahmin
  ediliyor ama Workspace hesapları `imap.gmail.com` kullanır. Diyalog bunu
  söylüyor ve tek tıkla düzelten bir düğme veriyor. Ayrıca yöneticinin
  IMAP'i kapatmış olabileceği ve admin konsolu linki yazılı.

### b) Google ile bağlan (OAuth, isteğe bağlı)

`app/mail/oauth.py`. **Gmail API kullanılmıyor** — IMAP'in XOAUTH2 SASL
mekanizması kullanılıyor, yani `app/mail/` katmanının tamamı aynı kaldı;
değişen tek şey `imaplib`e parola yerine erişim jetonu vermek
(`imap_client.connect(..., access_token=...)`).

Akış: loopback + PKCE. Ayrı bir geçici sunucu YOK — Google zaten çalışan
FastAPI'nin `/api/mail/oauth/callback` route'una dönüyor.

```
GET  /api/mail/oauth/status          → {configured, redirect_uri, scope}
POST /api/mail/oauth/start           → {url, state}   (UI bunu tarayıcıda açar)
GET  /api/mail/oauth/poll?state=     → {done, result} (UI yokluyor)
GET  /api/mail/oauth/callback        → Google buraya döner, HTML sayfa gösterir
```

`.env`de `GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET` yoksa düğme kapalı ve
UI ne yapılması gerektiğini (Cloud Console linki + tam yönlendirme adresi)
yazıyor.

`mail_accounts.auth_type` (`password` | `oauth`) eklendi — migration
`app/db/connection.py`de, var olan hesaplar `password` sayılıyor.
Yenileme jetonu anahtarlıkta ama **ayrı bir anahtarda** (`oauth:<key>`),
parolayla karışmasın diye.

### c) Dış bağlantı açma

```
POST /api/open-external  {url}   → xdg-open, yalnızca http/https
```

Gerekli çünkü native WebKit penceresinde `<a target="_blank">` hiçbir şey
yapmıyor. `web/js/util.js` → `interceptExternalLinks()` sayfadaki tüm dış
bağlantıları yakalayıp buraya yönlendiriyor; takvimdeki toplantı
bağlantıları da artık çalışıyor.

### Ek kural

22. **OAuth ve uygulama parolası aynı mail katmanına bağlanır.** İkisi de
    IMAP; `service.Credentials` hangi yolun kullanıldığını tek yerde
    çözüyor, `imap_client.connect()` dışındaki hiçbir kod bunu bilmiyor.
    Yeni bir mail işlemi eklerken `_open(account, creds)` kullan.
