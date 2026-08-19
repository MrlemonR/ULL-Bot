# Devir notu — yeni konuşma buradan başlasın

Son güncelleme: **2026-08-17**, Faz 9 sonunda.

**Bu dosyayı okuduysan kodu baştan taramana gerek yok.** Aşağıda ne
çalıştığı, neyin neden öyle olduğu ve sıradaki işler yazılı.

Okuma sırası:

0. **[`DEVAM.md`](./DEVAM.md)** — BAŞKA BİR MAKİNEDE devam ediyorsan önce
   bunu oku (Windows kurulumu, çalıştırma, test, kaldığın nokta).
1. **Bu dosya** — durum, kurallar, sıradaki iş, açık sorular.
2. **[`FAZ9_TESLIM.md`](./FAZ9_TESLIM.md)** — web araştırma, spam, mail
   render'ı: en son eklenenlerin referansı.
3. **[`FAZ8_TESLIM.md`](./FAZ8_TESLIM.md)** — masaüstü uygulaması, mail,
   takvim API'si ve UI yapısı.
4. **[`FAZ7_TESLIM.md`](./FAZ7_TESLIM.md)** — WebSocket sohbet protokolü.
   **Değişmedi**, hâlâ bağlayıcı.
5. `DECISIONS.md` — "neden böyle" arşivi. Aşağıdaki her karar orada
   gerekçesiyle var; bir şeyi değiştirmeden önce ilgili başlığı oku.

---

## 1. Tek cümlede: sistem ne

Kendi makinesinde çalışan, ücretsiz LLM kotalarını yöneten kişisel bir
asistan. Native bir masaüstü penceresi (`./scripts/ull-bot`) açılıyor,
servisleri kendisi başlatıp kapatıyor; içinde **sohbet**, **IMAP mail**,
**takvim**, **kota paneli** ve **geçmiş** var. Ajan; dosyalara, kabuğa,
maile, takvime ve **internete** erişebiliyor.

## 2. Durum

**587 test geçiyor** (`uv run pytest`). **Git'te son commit `Phase7` —
Faz 8 ve 9'un tamamı henüz commit EDİLMEDİ** (kullanıcı istemedi).

| Faz | Ne var |
|---|---|
| 1-7 | Spec'in fazları: LiteLLM+FastAPI, araçlar+güvenlik, çoklu sağlayıcı+kota, router, local model, profiller, hafıza/geçmiş/systemd |
| 8 | **Masaüstü uygulaması** (`app/desktop/`), **IMAP mail** (`app/mail/`), **takvim** (`app/calendar/`), **bildirimler** (`app/notify/`), **UI** (`web/`) |
| 8b | Google OAuth eklendi, denendi, **kaldırıldı** (aşağıda) |
| 8c | HTML mail render'ı, spam sürgünü, kaydırma düzeltmesi |
| 9 | **Web araştırma** (`app/web/`), markdown tablo, kısır döngü koruması |
| 9b | SearXNG+Tavily arama zinciri, `youtube_search`, turu toparlama, sohbet sıralaması |
| 10 | **Arayüz cilalaması**: kullanıcı teması, kare köşeler, terminal görünümü, mail kategorileri (`kod`/`genel`/`reklam`), mail üst barı + toplu işlemler |
| 10b | Özet kuralları (`mail_rules`), küçülen mail başlığı, ASCII seçim kutuları, açılır-kapanır ayar panelleri, animasyonlar |
| 11 | **Otomasyon** (`app/browser/`): Chromium+CDP, canlı ekran, DOM indeksi, planlayıcı/çalıştırıcı, adım düzenleme, beyaz liste. Tasarım: `docs/OTOMASYON.md` |

### Çalıştırma

```bash
cd /home/mrlemon/Projects/ULL-Bot
./scripts/ull-bot          # native pencere; servisleri kendisi açıp kapatır
```

Alternatifler: `systemctl --user start ull-bot.target` (Faz 7, servisler
arka planda kalır) ya da `uv run python -m app.desktop.supervisor`
(pencere yok, hata ayıklama). **Üçü çakışmaz** — süpervizör dinlenen bir
portu görürse o servisi benimser ve kapanışta ona dokunmaz.

Süpervizör logları: `~/.local/share/ai-orchestrator/logs/`.

### Şu anki veri (gerçek, kullanıcının kendi hesabı)

- `reallimon46@gmail.com` bağlı (uygulama parolasıyla, IMAP)
- 212 mail: 204 `INBOX` + 8 `[Gmail]/Spam`
- 41 sohbet oturumu, 0 takvim etkinliği

## 3. Sıradaki işler

### a) Web araması — kod tarafı bitti, KULLANICI EYLEMİ bekliyor

`web_search` DuckDuckGo kazımasıyla çalışıyordu ve **DDG bu makinenin IP'sini
engelledi** (HTTP 202 + "anomaly"). Mojeek captcha, Brave HTML 429 döndürdü.

**Brave elendi: kullanıcının kredi kartı kayıtta reddedildi.** Google Custom
Search de elendi — Google 2025'te yeni müşteriye kapattı, 1 Ocak 2027'de
tamamen kapanıyor. Kullanıcı yerine **SearXNG + Tavily** ikilisini seçti.

`app/web/search.py` artık dört yolu sırayla deniyor, ilk sonuç veren kazanır:

| Sıra | Yol | Ayar | Durum |
|---|---|---|---|
| 1 | SearXNG (yerel meta-arama) | `SEARXNG_URL` | **çalışıyor, canlı doğrulandı** |
| 2 | Tavily (ayda 1000 kredi, kartsız) | `TAVILY_API_KEY` | **çalışıyor, canlı doğrulandı** |
| 3 | Brave | `BRAVE_API_KEY` | kod duruyor, kullanılmıyor (kart reddedildi) |
| 4 | DDG kazıma | — | bu IP'de engelli, son çare |

**Arama çalışıyor ve bu iş KAPANDI** — ikisi de canlı doğrulandı. Kurulumda
iki tuzağa düşüldü, ikisi de `docs/SEARXNG.md`'de yazılı: (1) çekirdek
güncellemesinden sonra reboot edilmeden docker açılmıyor, (2) konteyner
İÇERİDE her zaman 8080 dinliyor — eşleme `127.0.0.1:8888:8080` olmalı,
`settings.yml`deki `port` bunu değiştirmiyor.

SearXNG kapansa bile arama ölmüyor: zincir 1.7 sn'de Tavily'ye düşüyor.

**Kullanıcıya sorulmadan bir arama sağlayıcısı eklenmemeli.**

### a2) Ücretsiz kotalar araştırma turuna yetmiyor — SIRADAKİ İŞ

Arama ve ajan tarafı artık çalışıyor ama uzun araştırma turları kotayı
bitiriyor. Ölçülen sınırlar:

- **Groq: dakikada 8000 token** (`x-ratelimit-limit-tokens`). `gpt-oss-120b`
  ve `gpt-oss-20b` için AYNI — model değiştirmek çözmez, ölçüldü.
- OpenRouter: günde 50 istek (kredisiz hesap); canlı %4-9'a kadar indi.
- Gemini: yerel sayaç %0 gösteriyordu.

Her adımda tüm konuşma yeniden gönderiliyor ve `fetch_url` çıktıları
20.000 karaktere kadar çıkabiliyor; 4-5 adımlık bir araştırma Groq'un
dakikalık bütçesini tek başına yiyor. Sonuç: tur, zincirin sonundaki
`ollama`ya (qwen2.5:3b) düşüyor ve o araştırmayı bitiremiyor.

**Yapıldı (2026-08-18), kullanıcı "ücretsiz kalsın, en iyisini yap" dedi:**

1. **Bağlam kırpma** (`AgentLoop.trim_context`): eski araç çıktıları 700
   karaktere iniyor, son ikisi tam kalıyor. Ölçüm: 6 çıktılı bir turda
   76.000 → 31.000 karakter (**%60 azalma**, ~19.000 → ~7.800 token).
2. **Kısa cooldown'da bekleme**: 429 sonrası sağlayıcı ≤12 sn içinde geri
   geliyorsa bekleniyor (Groq TPM aşımında genelde 5 sn). Sağlayıcı başına
   bir kez.
3. **`ollama` `tool_use` zincirinden çıkarıldı** — gerekçe aşağıda.

Canlı etki: tur, kotalar bitmeden önce 2 adım yerine **6 adım** bulut
sağlayıcılarında ilerliyor. Kotalar tamamen bittiğinde ise artık
"sağlayıcı yok, kotalar dolmuş, şu zaman tekrar dene" hatası veriyor.

**Kalan gerçek sınır:** ücretsiz katmanlar bu iş için dar. Groq dakikada
8000 token, OpenRouter günde 50 istek, Gemini günlük kota. Uzun bir
araştırma turu bunları bir öğleden sonrada bitirebiliyor. Kullanıcı ücretli
sağlayıcı istemiyor — bu yüzden kotalar bittiğinde beklemek gerekiyor.

### a3) Adım sayısı — kısmen ölçüldü, tamamlanmayı bekliyor

Canlı bir karşılaştırma turu **21 adım** sürdü (15'i `web_search`, 3
`youtube_search`, 2 `fetch_url`) ve günün kotasını bitirdi. Kullanıcı hedefi:
~10 adım.

Kaldıraç şu: `AgentLoop` bir mesajdaki **tüm** araç çağrılarını TEK adımda
çalıştırıyor (`for call in response.tool_calls`). Yani toplu çağrı kotayı
doğrudan düşürüyor. Prompt'a "Step budget" bölümü eklendi (paralel çağrı,
tek geniş arama, fiyatı doğrulamak için tekrar arama yok) ve
`youtube_search` açıklamasına da yazıldı.

**Canlı kanıt:** sonraki turda model adım 4'te üç `web_search`i aynı anda
gönderdi ve üçü tek adım saydı. Ama o tur kotalar bittiği için yarıda kaldı
— **tam bir turun kaç adıma indiği henüz ölçülmedi.** Kotalar yenilendiğinde
ilk iş bunu ölçmek; hâlâ 15+ ise sıradaki adım `web_search` sonuçlarını
zenginleştirip `fetch_url` ihtiyacını azaltmak olabilir.

### b) `@trendbox.io` hesabı eklenemiyor

Google Workspace hesabı; yöneticisi uygulama parolalarını ve muhtemelen
IMAP'i kapatmış ("Aradığınız ayar hesabınızda kullanılamıyor"). MX kaydı
doğrulandı, gerçekten Google Workspace. Kullanıcıya söylenenler:

1. Önce 2 adımlı doğrulamayı dene (kapalıysa uygulama parolası hiç çıkmaz)
2. Kapalıysa yöneticiden: Güvenlik → 2SV'ye izin, **ve** Gmail → Son
   kullanıcı erişimi → IMAP
3. Yönetici beklemeden: trendbox.io → gmail'e **otomatik yönlendirme**

Bu ikisi açılmadan hiçbir istemci bağlanamaz. Kullanıcı sonucu bildirmedi.

### c) Çöp kutusu / `write_file` / `edit_file` / `delete_file`

**Faz 7'den beri bekliyor**, hiçbir faza atanmadı. Şu an gerçek yazma/silme
sadece onaylanan `run_shell` ile oluyor, geri alma yok. `settings.trash_dir`
tanımlı ama kullanılmıyor. **Kullanıcı kapsamı belirlemeli** — otomatik
eklenmemeli.

### d) Telefona bildirim

Kullanıcı bilerek sonraya bıraktı. Hatırlatmalar şu an sadece masaüstü
bildirimi (dunst). Yol seçilmedi (ntfy, Gotify, Telegram…).

### e) Faz 7'den devralınan bilinen boşluk

`GET /api/quota`, `gemini_lite` ve `ollama`yı göstermiyor —
`describe_chain()` `task_type` verilmeden çağrılıyor.

## 3b. Arayüz (2026-08-18, kullanıcının listesi)

Hepsi canlı doğrulandı (headless Chromium + ekran görüntüsü):

| İstek | Nasıl yapıldı |
|---|---|
| Özel CSS teması | `~/.config/ull-bot/theme.css` (ya da `.env` → `USER_THEME`). `/theme.css` ucu servis ediyor, `index.html` **en son** yüklüyor. Belge: `docs/TEMA.md`, örnek: `config/themes/phosphor.css` |
| Kare köşeler | `--r-sm/--r/--r-lg = 0` + sabit yazılmış tüm yarıçaplar sıfırlandı; test kuralı koruyor |
| Terminal havası | Başlıklar `[ SOHBET ]` biçiminde, etiketler/rozetler/sekmeler monospace. Gövde metni normal yazı tipinde — "abartmadan" |
| Geçmiş yeri | Sol şeritten kalktı; üst barda "hazır" rozetinin sağında ve yalnızca Sohbet'te görünüyor |
| Mail kategorileri | `kod` (Aktivasyon Kodu), `genel` (sipariş/kargo/dekont), `bildirim` (hesabınla ilgili), `reklam` (eski `bulten`). Açılış görünümü "Öncelikli" — reklam ve kararsızlar dışarıda |
| Aktivasyon kodu | Gövdeden çıkarılıp detayda `[ KOD ] 016823` kartı + "Kodu kopyala" |
| Mail düzeni | Kategoriler üst bara taşındı, liste en solda, asistan dock'u hiç daralmıyor. Liste öğelerinde seçim kutusu; seçim varken üst bar toplu işlemlere dönüşüyor (okundu/okunmadı/yıldız/kategori/sil + tümünü okundu yap) |
| Mail detayı | Kategori rozeti ortalandı, sağ üstte ← → gezinme ve ✕ kapatma |

Mevcut 236 mail yeniden sınıflandırıldı (`POST /api/mail/reclassify`, LLM
yok): 22 aktivasyon kodu ve 19 sipariş maili yanlış kategorilerden çıktı.

### Faz 10b — ikinci arayüz turu (2026-08-18)

| İstek | Nasıl yapıldı |
|---|---|
| Özet kuralları | Ayarlar → `[ MAIL KURALLARI ]`. `mail_rules` tablosu, `GET/POST/PATCH/DELETE /api/mail/rules`. Kurallar `SUMMARY_PROMPT`in SONUNA ekleniyor (sonda olan ezer) ve **talimat** olarak gidiyor — mail içeriği onlara dokunamıyor |
| Seçim kutuları | ASCII: `[ ]` / `[X]`. Gerçek `<input>` DOM'da kalıyor (klavye + erişilebilirlik), sadece görsel olarak gizli |
| Küçülen mail başlığı | 60px kaydırınca bar tek satıra iniyor (180px → 65px, ölçüldü). Sağ alt köşede elle aç/kapa düğmesi; elle seçim otomatiği ezer |
| Kategori rozeti | Mail alanının sol üstünde, gezinme oklarının solunda |
| Açılır-kapanır ayarlar | Her panel `<details>`; göstergesi `[+]` / `[-]` |
| Animasyonlar | Aktif sekme/şerit düğmesinin kenarında dönen ışık (`@property --spin` + konik gradyan maskesi), görünüm geçişi, liste satırlarının sırayla belirmesi, okunmamış noktanın nabzı, ASCII çevirici (`\|/-`), terminal imleci `▌`, "çalışıyor" rozetinde tarayıcı çizgisi. Hepsi `prefers-reduced-motion` ile susuyor |

## 4. Cevaplanmış soru (eski "teşhis edilemedi" maddesi)

Sağ kenardaki dar dikey şerit **çözüldü**. Kullanıcı tarif etti: dock
daraltılınca kalem düğmesi kalıyor, daraltma düğmesi sağa kayıyor.

Ölçüm (headless Chromium, CDP): daraltılmış kolon 46px, ama `[data-new]`
(✎) gizlenmiyordu. İki düğme + boşluk + padding 76px eder; başlığın
`scrollWidth`i 52px (client 45px) ve daraltma düğmesinin sağ kenarı
pencerenin **7px dışında** kalıyordu. Önceki ölçüm bunu bulamamıştı çünkü
sadece dikey kaydırma zinciri ölçülmüştü, dock BAŞLIĞI değil.

`style.css`te `[data-new]` de gizlenenler listesine eklendi; ölçüm
tekrarlandı (45 = 45, düğme pencere içinde). `tests/test_web_assets.py`
kuralı koruyor.

## 5. Bozulmaması gereken kurallar

Faz 1-7'den:

1. **Model isimlerini ve kota sayılarını uydurma** (spec §12). Canlı dene.
2. **Güvenlik katmanını kısayol geçme.** Shell erişimi var.
3. **Blocked komut listesi UI'dan düzenlenemez** (spec §7.3).
4. **Audit log ajana okunabilir/yazılabilir olmamalı.** 0600.
5. **LiteLLM'in kendi fallback'i kapalı kalmalı** (`num_retries: 0`).
6. **`fastapi==0.136.3` pini** — kaldırma.
7. **Commit/push sadece kullanıcı açıkça isterse.**
8. **`force_first`, elle kapatılan sağlayıcıyı zorlamaz.**
9. **Görev tipi seçimi kota elemesinin ALTINA eklenir.**
10. **`gemini_lite`/`ollama` gerçek sağlayıcı değil.**
11. **Laptop'ta local dışlaması statik**, bayrak değil.
12. **`litellm.desktop.yaml` ve `litellm.laptop.yaml` aynı modelleri tanımlar.**
13. **`remember`in ayrı "recall" aracı yok** — notlar sistem promptunda.
14. **`install.sh` servisleri enable/start etmiyor**, kasıtlı.

Faz 8-9'dan:

15. **Mail VE web içeriği düşman girdidir.** `read_mail`, `list_mail`,
    `web_search`, `fetch_url` çıktıları her zaman `untrusted=True`.
16. **IMAP parolası SQLite'a yazılmaz** — libsecret, yoksa 0600 dosya.
17. **Mail yazma önce IMAP'e, sonra önbelleğe.**
18. **Süpervizör benimsediği servisi öldürmez.**
19. **Takvimde güven 1.0 sadece ICS yolunun rozeti** (metin tahmini ≤0.95).
20. **Kural tabanlı sınıflandırıcı önce, LLM sadece kararsızlara ve elle.**
21. **`FAZ7_TESLIM.md`deki WebSocket protokolü sabit.**
22. **`fetch_url`ün SSRF kapısı kaldırılamaz** — adresi model seçiyor ve
    model okuduğu sayfadan etkileniyor.
23. **Spam sürgündür, filtre değil** — "Tümü"ye, "Okunmamış"a ve aramaya
    hiç karışmaz. Sunucunun kararı bizim kurallarımızın önünde.
24. **Yükseklik zincirindeki her grid/flex öğesinde `min-height: 0`**
    olmalı (`tests/test_web_assets.py` kontrol ediyor).
25. **Mail gövdesi beyaz zeminde, kum havuzunda, `allow-scripts` OLMADAN.**

## 6. Bilinçli olarak YAPILMAYANLAR

- **Google OAuth** → eklendi, denendi, **kaldırıldı**. `mail.google.com`
  kısıtlı kapsam olduğu için Google, doğrulanmamış uygulamaların
  yetkilendirmelerini **7 günde bir iptal ediyor**; haftada bir kopan bir
  mail istemcisi hiç OAuth'tan kötü. Detay: DECISIONS.md → "Google OAuth,
  kişisel Gmail için pratik değil". `mail_accounts.auth_type` sütunu duruyor
  ama hep `'password'`.
- **Mail gönderme/cevaplama** → sadece okuma, işaretleme, taşıma.
- **Tekrarlayan etkinlik (RRULE)** → ICS'te varsa ilk oluşum alınıyor.
- **Takvimin telefona senkronu** → kullanıcının kararı; dışa aktarım ICS.
- **Görsel ek desteği** → `classifier.has_image` hep `False`.
- **Sınıflandırıcının LLM'e taşınması** → Faz 5'te ertelendi.
- **LAN üzerinden Ollama** → kod hazır, açık değil, test edilmedi.
- **Ajanı ayrı sistem kullanıcısı altında çalıştırma** → hiçbir fazda yok.
- **Windows kabuk politikası** → `run_shell` Windows'ta kapalı.

## 7. Bilinen, düzeltilmemiş gerçekler

Sağlayıcı tarafı (bizim kodumuz değil):

- **OpenRouter'ın ücretsiz modeli ara sıra boş cevap ya da "Provider
  returned error" döndürüyor.** Canlı görüldü: `fetch_url` başarılı,
  ardından model hiç metin üretmedi ve tur `done` ile boş bitti.
- **Groq ara sıra `tool_use_failed` döndürüyor.**
- **Bir sağlayıcı yayına başlayıp aynı adımda düşebiliyor** — UI bunu
  `model_switch`i "arabelleği sıfırla" sinyali sayarak çözüyor.
- **Yerel model (qwen2.5:3b) çok adımlı araştırmada zayıf** — boş/bozuk
  araç argümanları üretebiliyor. Kısır döngü koruması bunu yakalıyor.

Bizim tarafımız:

- **DDG bu IP'yi engelledi** (yukarıda 3a).
- **HTML mailler kaba metne indirgeniyor** (`parser.html_to_text` regex).
- **Doğal dil tarih çıkarımı kural tabanlı** — kapsamı
  `tests/test_calendar_meeting.py`de sabitlendi.
- **`move_mail` gerçek sunucuda denenmedi.**
- **IMAP klasör adı çözümü (modified UTF-7) sahada test edilmedi.**
- **Pencere Wayland'de `WEBKIT_DISABLE_DMABUF_RENDERER=1` ile açılıyor**
  (yazılım render yolu).

## 8. Bu oturumda yakalanan hatalar — tekrar etmesin

Hepsi canlı kullanımda ya da testte çıktı, hepsi teste bağlandı:

| Hata | Belirti | Kök sebep |
|---|---|---|
| `hidden` niteliği ezilmişti | Tüm arayüz sönük, hiçbir şeye tıklanmıyor | `.modal-backdrop { display: grid }` UA stylesheet'i eziyordu |
| `.stage`de `min-height: 0` yok | Mail paneli kaymıyor | Grid öğesi içeriğinin altına küçülmüyor, panel 25.000px oldu |
| Mail koyu-üstü-koyu | Metin okunmuyor | Gönderen beyaz zemin varsayıyor |
| Yalnızca `INBOX` senkronu | Spam algılanmıyor | Gmail'in spam kutusu hiç çekilmiyordu |
| Markdown yer tutucusu | Kod blokları "BLOCK0" yazıyor | ` BLOCK0 ` boşluğa bağlıydı, `trim()` bozuyordu |
| Kısır döngü | Ajan dakikalarca dönüyor | Reddedilen çağrı turu durdurmuyordu |
| DDG 202 | "sonuç yok" sanılıyor | 202 hata kodu değil, hız sınırı sayfası |
| VALARM | Etkinlik açıklaması yanlış | ICS iç bileşenleri atlanmıyordu |
| Türkçe saat kaybı | Etkinlik 09:00'a düşüyor | "20.08.2026 **tarihinde saat** 14:00" köprüsü kalıpta yoktu |
| Groq modeli kaldırılmış | Her turda `provider_error` 404, tur zayıf modele düşüyor | `llama-3.3-70b-versatile` Groq'ta yok; `groq/openai/gpt-oss-120b` ile değişti (canlı listeden) |
| Boş cevap turu bitiriyordu | Araçlar çalışıyor, sonra HİÇBİR ŞEY gelmiyor | Sağlayıcı ne metin ne araç çağrısı döndürünce `done` yayımlanıp boş metin dönülüyordu; artık sağlayıcı hatası sayılıp sıradakine geçiliyor |
| Aynı aramanın tekrarı | Ajan 15-20 adım dönüp sonuç vermiyor | Küçük yerel model neredeyse aynı sorguyu tekrarlıyordu; sorgular birebir aynı olmadığı için döngü koruması görmüyordu — `web_search` artık tur içinde benzer sorguyu reddediyor |
| Özeti yerel model yazıyordu | Özet yerine ham mail + `hata枭` gibi bozuk karakterler | Kısa mailler `trivial` zincirine gidiyordu (qwen2.5:3b ilk sırada); özetleme artık `long_context` (gemini → openrouter), yerel modele hiç düşmüyor |
| Özetteki bağlantı taşıyordu | 500 karakterlik Steam adresi karttan taşıyor, tıklanamıyor | `escapeHtml` yerine `markdown()` + `overflow-wrap: anywhere`; prompta "uzun bağlantıyı yapıştırma" kuralı |
| Çöpe taşıma 500 veriyordu | "Internal Server Error" | Gmail'in Türkçe çöp klasörü `[Gmail]/Çöp Kutusu`; IMAP klasör adları modified UTF-7 ister, kodda çözme vardı KODLAMA yoktu (`_encode_folder_name`) |
| Geçmiş ham markdown gösteriyordu | Tablolar `\| a \| b \|`, linkler tıklanamıyor | `resumeSession` ve geçmiş önizlemesi `escapeHtml` kullanıyordu; ikisi de `markdown()`e geçti |
| Eski arayüz ekranda kalıyordu | CSS/JS değişiyor, ekran değişmiyor | Statik dosyalarda `Cache-Control` yoktu; tarayıcı "heuristic freshness" ile sunucuya sormadan diskten servis ediyordu — artık `no-cache` |
| `index.html` önbellekte takıldı | Yeni kategoriler/düzen hiç gelmedi, ekran bozuk göründü | `/` ve `/theme.css` `/static` altında değil, mount'un `no-cache` başlığı onlara geçmiyordu. Yeni CSS + ESKİ HTML birleşince düzen dağıldı. İkisine de başlık eklendi; takılı kalan kopya için `~/.cache/ULL-Bot/WebKitCache` silinmeli |
| Tablo hücreleri ham markdown | `**Razer BlackShark V2 Pro**` ve tıklanamayan `[İnceleme](url)` | Tablolar satır içi kurallardan ÖNCE yer tutucuya alınıyordu; `inline()` ayrıldı ve hücrelere de uygulanıyor |
| Tablo sütunları eziliyordu | Ürün adı "Ra / zer Barra / cuda X" diye bölünüyor | `width: 100%` + miras `word-break: break-word`; artık `width: auto; min-width: 100%` ve hücrede `word-break: normal` |
| Yerel model cevap uyduruyordu | Fiyat sütunu boş, YouTube linkleri `watch?v=example_video_id` | Kotalar bitince tur qwen2.5:3b'ye kalıyordu; `tool_use` zincirinden çıkarıldı (`trivial`de duruyor) |
| Toparlama tek zincire bakıyordu | `wrap_up_failed`, toplanan veri çöpe | `reasoning` zincirindeki iki sağlayıcı da 429 yiyince pes ediyordu; artık `tool_use` zinciri de deneniyor |
| Araç kartları ters sırada | "Yaptıkları en altta, cevap en üstte" | `insertBefore(node, streamBody.nextSibling)` her yeni kartı gövdenin arkasına koyuyordu; artık gövdeden ÖNCE ekleniyor |
| Kesilen tur veriyi çöpe atıyordu | "işlem durduruldu", toplanan araştırma kayıp | Döngü koruması turu kesince hiç cevap üretilmiyordu; artık araçlar kapatılıp eldeki veriyle cevap yazdırılıyor (`AgentLoop._wrap_up`) |
| Daraltılmış dock taşıyordu | Kalem kalıyor, daraltma düğmesi sağa kayıyor | 46px'lik kolonda `[data-new]` gizlenmiyordu; başlık 52px'e taşıp düğmeyi pencere dışına itiyordu |
| Şablon dizesini kapatan yorum | Uygulama "bağlanıyor"da asılı kalıyor | `mailbody.js`teki CSS şablonu, yorumdaki ters tırnakla (`` `color-scheme: dark` ``) erken kapandı; `SyntaxError` modül grafiğini düşürdü, `app.start()` hiç çalışmadı, WS açılmadı |

**Ders (DECISIONS.md'de de yazılı):** UI doğrulaması `element.click()` ile
yapılırsa hit-testing atlanır ve sayfayı kaplayan görünmez bir örtü
görünmez. Gerçek fare olayı (`Input.dispatchMouseEvent`) ve
`elementFromPoint` kullan.
