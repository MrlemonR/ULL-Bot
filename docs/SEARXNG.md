# Yerel arama: SearXNG kurulumu

`web_search` artık dört yolu sırayla deniyor: **SearXNG → Tavily → Brave →
DuckDuckGo kazıma**. Bu belge birincisini anlatıyor.

**Neden:** DDG bu makinenin IP'sini engelledi ve kazıma güvenilmez hale
geldi (`NEXT_PHASE.md` §3a). SearXNG bir *meta-arama*: sorguyu Google,
Bing, Startpage, Wikipedia gibi motorlara paralel soruyor. Kota yok,
anahtar yok, kredi kartı yok, sorgular üçüncü tarafa gitmiyor. Tek bir
motor engellenirse arama ölmüyor — asıl kazanç bu.

---

## 1. Docker'ı kur (bu makinede kurulu değil)

Arch tabanlı sistemde:

```bash
sudo pacman -S docker docker-compose
sudo systemctl enable --now docker
sudo usermod -aG docker $USER
```

Son komuttan sonra **oturumu kapatıp açman** (ya da yeniden başlatman)
gerekiyor; grup üyeliği ancak o zaman geçerli oluyor. Doğrulama:

```bash
docker run --rm hello-world
```

`sudo` olmadan çalışıyorsa hazırsın.

### "Cannot connect to the Docker daemon" — önce çekirdeği kontrol et

Kurulum sırasında çekirdek de güncellendiyse **yeniden başlatana kadar
docker açılmaz** ve hata mesajı bunu hiç söylemez. Belirti, günlükte şu:

```
iptables ... CHAIN_ADD failed (No such file or directory): chain PREROUTING
```

Sebep docker değil: çalışan çekirdeğin modül dizini güncellemede silinmiş,
bu yüzden `iptable_nat` yüklenemiyor. Teşhis:

```bash
uname -r                        # calisan cekirdek
ls /usr/lib/modules/            # diskteki modul dizinleri
```

İkisi tutmuyorsa tek çözüm **reboot**. Docker `enabled` olduğu için
açılışta kendiliğinden gelir.

## 2. Gizli anahtarı üret

`config/searxng/settings.yml` içindeki `secret_key` yer tutucu. Değiştir:

```bash
cd /home/mrlemon/Projects/ULL-Bot
sed -i "s/DEGISTIR_bunu_docs\/SEARXNG.md_diyor/$(python3 -c 'import secrets; print(secrets.token_hex(32))')/" \
  config/searxng/settings.yml
```

## 3. Başlat

```bash
docker compose -f config/searxng/docker-compose.yml up -d
```

İlk çalıştırma imajı indirir (~200 MB), sonrakiler saniyeler sürer.

## 4. Doğrula — bu adımı atlama

```bash
curl -s "http://127.0.0.1:8888/search?q=test&format=json" | head -c 300
```

- **JSON görüyorsan** hazır.
- **HTML görüyorsan** `settings.yml`deki `search.formats` listesine `json`
  eklenmemiş ya da dosya konteynere bağlanmamış demektir. Bu en sık yapılan
  hata: SearXNG varsayılanı yalnızca `html` ve JSON kapalıyken hata da
  vermiyor, sessizce sayfa döndürüyor.
- **Boş cevap / `HTTP 000`** — port açık görünür (`ss` LISTEN der) ama her
  istek sıfırlanır. İki sebebi var:
  1. **Port eşlemesi yanlış.** Konteynerin içindeki sunucu **her zaman
     8080**'i dinler; `settings.yml`deki `server.port` bunu değiştirmez.
     Eşleme `127.0.0.1:8888:8080` olmalı. Sağa 8888 yazarsan Docker host
     portunu yine açar, ama arkasında dinleyen olmadığı için reset gelir.
  2. `settings.yml`de `bind_address` `127.0.0.1` kalmıştır. Konteynerin
     içinde loopback'e bağlanmak host'tan erişimi keser; `0.0.0.0` olmalı
     (dışarı açılma host tarafındaki eşlemeyle zaten engelli).
- **429** alıyorsan `limiter: false` satırı uygulanmamıştır.

## 5. Uygulamaya bağla

`.env` dosyasına:

```
SEARXNG_URL=http://127.0.0.1:8888
```

Uygulamayı yeniden başlat. Artık her arama önce buradan geçiyor.

---

## Bakım

| İş | Komut |
|---|---|
| Durum | `docker ps --filter name=ull-bot-searxng` |
| Log | `docker logs -f ull-bot-searxng` |
| Ayar değişikliğinden sonra | `docker compose -f config/searxng/docker-compose.yml restart` |
| Güncelle | `docker compose -f config/searxng/docker-compose.yml pull && ... up -d` |
| Durdur | `docker compose -f config/searxng/docker-compose.yml down` |

**Motorlar zaman zaman kırılır.** Google ya da Bing ayrıştırıcısı bozulduğunda
SearXNG boş sonuç döner; `docker logs` bunu yazar ve genelde bir `pull` ile
düzelir. Bu yüzden zincirde arkasında Tavily duruyor — SearXNG boş dönerse
arama otomatik oraya geçer, sen fark etmezsin.

## Güvenlik notu

SearXNG'yi `0.0.0.0:8888` olarak dışarı **açma**. Açık bir SearXNG örneği
herkesin kullanabileceği bir arama proxy'sine dönüşür; motorlar trafiği
kötüye kullanım sayıp IP'yi engeller — yani bizi tam olarak bu kuruluma
iten sorunu geri getirir.

`app/web/fetch.py`deki SSRF kapısı (kural 22) buradan etkilenmiyor:
`SEARXNG_URL`i **sen** yazıyorsun, model değil. Modelin seçtiği adresler
hâlâ aynı kapıdan geçiyor ve yerel adresler engelli.
