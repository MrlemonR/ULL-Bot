# Faz 9 teslim notu — web araştırma, spam sürgünü, HTML mail

2026-08-17. Faz 8'in üstüne, kullanıcının canlı kullanımda bildirdiği
sorunlar ve yeni bir istek üzerine yapıldı.

Bu dosya **yalnızca Faz 8'den sonra değişenleri** anlatır. Masaüstü
uygulaması, mail/takvim API'si ve UI yapısı için
[`FAZ8_TESLIM.md`](./FAZ8_TESLIM.md); WebSocket protokolü için
[`FAZ7_TESLIM.md`](./FAZ7_TESLIM.md) (değişmedi).

---

## 1. Web araştırma (yeni)

> "internette araştırma yapmasını istiyorum … '4000TL bütçem var, bana
> alabileceğim kulaklıkları karşılaştır' dediğimde bir kıyaslama versin"

### Yeni paket: `app/web/`

```
app/web/search.py   Brave API (anahtar varsa) → DDG kazıma (yedek)
app/web/fetch.py    sayfa getirme + HTML→metin + SSRF kapısı
```

### Yeni araçlar

| Araç | Risk | Not |
|---|---|---|
| `web_search` | safe | çıktı **untrusted**; sonuç: başlık + adres + özet |
| `fetch_url` | safe | çıktı **untrusted**; SSRF kapısından geçer |

Toplam araç sayısı **20**.

### Arama: iki yol

1. **Brave Search API** — `.env`de `BRAVE_API_KEY` varsa ÖNCE bu. Ücretsiz
   katman ayda 2000 sorgu, kart istemiyor.
   https://api-dashboard.search.brave.com/
2. **DuckDuckGo kazıma** — anahtarsız yedek, kurulum istemiyor.

**Kazımanın sınırı ölçüldü ve küçük değil:** DDG iki hızlı sorgudan sonra
HTTP **202** + "anomaly" sayfası, Mojeek doğrudan **Captcha** döndürüyor.
202 bir hata kodu olmadığı için ilk sürüm bunu "sonuç yok" sanıyordu ve
model aramayı tekrarlıyordu — her tekrar engeli sıkıştırıyor.

Savunmalar (`app/web/search.py`): 2.5 sn kendi aralığımız, 5 dk sorgu
önbelleği, hız sınırı tespiti (202/429/captcha/anomaly), **bir kez otomatik
bekle-tekrar dene**, iki DDG uç noktası (`html` + `lite`) ve iki ayrı
ayrıştırıcı. Hiçbiri tutmazsa **hata fırlatılır** — sessizce boş liste
dönmek modelin uydurmaya başlaması demek.

**Şu an DDG bu makinenin IP'sini engelliyor** (bkz. NEXT_PHASE.md §3a).

### SSRF kapısı — kaldırılamaz

`fetch_url`'ün adresini **model** seçiyor ve model, az önce okuduğu bir
sayfanın metninden etkilenmiş olabiliyor. Yani karar dolaylı olarak bir
yabancının etkisi altında. Kapı olmasaydı bir sayfa modele
`http://127.0.0.1:8080/api/mail/messages` okutup kullanıcının maillerini
kendi çıktısına taşıyabilirdi.

`app/web/fetch.py` → `guard_url()`:

- yalnızca `http`/`https` (şema `^scheme:` ile önce tanınıyor —
  `javascript:alert(1)` içinde `://` yok, körü körüne `https://` eklemek
  onu şema denetiminden geçirirdi)
- yalnızca genel IP'ler: private, loopback, link-local, reserved,
  multicast, unspecified hepsi kapalı (DNS rebinding dahil)
- **her yönlendirme adımı yeniden denetleniyor** — açık yönlendirme
  kapıyı atlamasın
- 2 MB tavan, 5 yönlendirme tavanı

13 vakası testte (`tests/test_web_research.py`).

## 2. Markdown tablo + resim (arayüz)

Karşılaştırma cevabının taşıyıcısı tablo. `web/js/util.js` → `markdown()`
artık `| a | b |` / `|---|---|` bloklarını gerçek `<table>`a çeviriyor
(hizalama `:---`/`---:` destekli, dar panelde kendi yatay kaydırma kabında)
ve `![alt](url)` resimlerini çiziyor.

Eklerken **iki eski hata** ortaya çıktı ve düzeltildi:

1. Blok yer tutucusu ` BLOCK0 ` boşluğa bağlıydı; paragraf kontrolü
   `trim()` edilmiş metne boşluklu kalıpla bakıyordu. Sonuç: fenced kod
   blokları ekranda **"BLOCK0" yazısı** olarak çıkıyordu. Baştan beri
   böyleymiş.
2. Yer tutucu kendi paragrafında kalmayınca tablo bir `<p>` içine
   sarılıyordu — `<div>` bir `<p>` içinde geçersiz.

`markdown()` artık görünmez bir yer tutucu kullanıyor; davranışı
`tests/markdown_check.mjs` ile 22 iddiayla kilitli (pytest Node'u çağırıyor).

## 3. Kısır döngü koruması (ajan döngüsü)

Canlı testte zayıf bir model `web_search`i **boş sorguyla** çağırdı,
"reddedildi" aldı, **aynı çağrıyı adım limiti dolana kadar tekrarladı**.

`AgentLoop` artık `(araç_adı, argümanlar)` imzası başına başarısızlık
sayıyor; `MAX_REPEATED_FAILURES = 3`'e ulaşınca tur `stopped` olayıyla
bitiyor. Sayaç **ad + argüman** imzasına bakıyor: farklı sorgularla denemek
"takılmak" değil, model yol arıyor demektir.

`web_search`/`fetch_url`un boş-argüman hatası da artık modele ne yapması
gerektiğini söylüyor (örnek çağrı + "aynı çağrıyı tekrarlama").

## 4. Spam sürgünü (mail)

> "spam mailleri tümü kısmında görünmesin, en altta spam olarak ayrı görünsün"
> "gmail sitesinde otomatik spama düşüyordu, burada da öyle olmalı"

**Gmail'in Spam klasörü artık senkronlanıyor.** Klasör verilmezse gelen
kutusu **ve** spam klasörü çekiliyor; spam klasörünün gerçek adı RFC 6154
`\Junk` bayrağından bulunuyor (Gmail'de ad hesabın diline göre değişiyor).

`spam` kategorisi sıradan bir kategori değil — `HIDDEN_FROM_ALL` kümesinde:

- "Tümü", "Okunmamış" ve aramaya **hiç karışmaz**
- yalnızca kendi kategorisi seçilince görünür
- şeritte **"Ayrılanlar"** başlığı altında, en altta
- `counts()`in `total`/`unread` değerleri spam'i saymaz (liste 198 gösterip
  rozet 210 deseydi kullanıcı "mail kayboldu" derdi), ama kategori kırılımı
  sayar çünkü spam satırının kendi sayısı oradan gelir

Kaynaklar: sunucunun spam klasörü, `X-Spam-Flag` gibi başlıklar, ve mail
detayındaki **"🚫 Spam"** düğmesi. Sunucunun kararı bizim kurallarımızın
**önünde** — Gmail'in filtresi bizimkinden iyi.

Bir incelik: senkron sırasında bilinen mesajların bayrakları tazelenirken
`include_hidden=True` gerekiyor, yoksa spam'e alınmış mailler sunucuda
okunsa bile burada okunmamış kalırdı.

Canlı doğrulandı: `[Gmail]/Spam`'den 8 mail geldi, hepsi `spam` oldu.

## 5. HTML mail render'ı

Kullanıcı "resimler görünmüyor" dedi. Sebep: `body_html` kaydediliyordu
ama UI yalnızca `body_text` gösteriyordu — bu kutuda **200 mailin
194'ünde HTML var**.

`web/js/mailbody.js` (yeni): iki katmanlı savunmayla HTML render.

1. **Temizleme** — `DOMParser` ile (regex değil): `<script>`, `<iframe>`,
   `<object>`, `<form>`, `on*` öznitelikleri, `javascript:` adresleri.
2. **Kum havuzu** — `<iframe srcdoc sandbox="allow-same-origin">`.
   `allow-scripts` **verilmiyor**. `allow-same-origin` var ki ana sayfa
   bağlantı tıklamalarını yakalayıp sistem tarayıcısına yönlendirebilsin;
   betik çalışamadığı için bu kombinasyon güvenli. İkisi birlikte
   verilseydi mail içindeki bir betik ana sayfaya erişebilirdi — test
   bunu kilitliyor.

**Uzak resimler varsayılan engelli** (takip pikseli). Tek tıkla açılıyor;
CSP'deki `img-src` o zaman genişliyor. Engellenen resim `src`i silinmek
yerine **saydam 1px** ile değiştiriliyor — `src`siz `<img>` tarayıcıyı
"bozuk resim" moduna sokup alt metnini tam boyutta basıyordu.

**Mail gövdesi BEYAZ zeminde.** Gönderenlerin neredeyse tamamı beyaz zemin
varsayıp renkleri satır içi dayatıyor; koyu zeminde Google'ın "Güvenlik
uyarısı" maili koyu gri metni koyu zeminde basıp hiç okunmuyordu.
Thunderbird, Gmail ve Apple Mail de aynısını yapıyor.

## 6. `.stage`de eksik `min-height: 0`

Kullanıcı "kaydıramıyorum" dedi. Ölçüldü: mail detay paneli
`scrollHeight == clientHeight == 25041` — panel kısıtlanmak yerine
içeriğine göre **büyümüştü**, kayacak bir şey yoktu.

Sebep: `.stage` bir grid öğesi ve grid öğelerinin varsayılan `min-height`ı
`auto` — içeriklerinin altına küçülmeyi reddederler. Zincirdeki diğer sekiz
halkada bu satır zaten vardı; eksik olan tek yer `.stage`di.

`tests/test_web_assets.py` artık zincirin her halkasını kontrol ediyor.

## 7. Yeni/değişen ayarlar

```bash
BRAVE_API_KEY=        # isteğe bağlı, arama güvenilirliği için
```

`GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET` **kaldırıldı** (OAuth kaldırıldı).

## 8. Yeni testler

```
tests/test_web_research.py     39  arama, getirme, SSRF, Brave, hız sınırı
tests/test_agent_stuck_loop.py  5  kısır döngü koruması
tests/markdown_check.mjs       22  markdown davranışı (Node, pytest'ten)
tests/test_web_assets.py       11  CSS/JS statik kontrolleri
```

Toplam **478 test** geçiyor.
