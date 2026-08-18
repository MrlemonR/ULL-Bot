# Kararlar

## Faz durumu (yeni konuşmaya buradan devam et)

**Faz 1 tamamlandı ve tarayıcıda doğrulandı** (2026-08-16). LiteLLM proxy
(`chat-default` → `openrouter/openai/gpt-oss-20b:free`) + FastAPI streaming +
SQLite'a `sessions`/`messages` yazımı + gerçek tarayıcı testi.

**Faz 2 tamamlandı ve çalışan sistemde doğrulandı** (2026-08-16). Kapsam:
tool registry + `read_file`/`list_dir`/`search_files`/`run_shell`, ajan
döngüsü (adım limiti + devam onayı), `safety/policy.py` (blocked liste,
obfuscation çözme), `safety/sandbox.py` (allow/deny yolları, traversal ve
symlink koruması), WebSocket üzerinden onay diyalogları, dry-run, audit log.
183 test geçiyor (`uv run pytest`).

Spec §9'daki kabul kriterleri gerçek modelle, tarayıcı yerine WebSocket
istemcisiyle sürülerek doğrulandı:

- "~/Downloads'ta hangi dosyalar var, kaç pdf var?" → ajan `list_dir` çağırdı
  (risk `safe`, onay sorulmadı), gerçek listeyi aldı, "0 adet PDF" dedi.
- "Sistemime vim kur, pacman kullan" → model `run_shell("pacman -V")` çağırdı,
  politika `blocked` dedi, **onay bile sorulmadı**, araç REDDEDİLDİ döndü.
- `touch ...` → risk `confirm`, WebSocket'ten onay diyaloğu geldi, onaylandı,
  dry-run açık olduğu için komut çalıştırılmadı ve dosya oluşmadı.
- Audit log'da tüm zincir görünüyor (`turn_start` → `tool_call` → `tool_blocked`
  / `tool_approval` → `tool_result` → `turn_end`), dosya izni 0600.

**Faz 3 tamamlandı ve üç sağlayıcıyla canlı doğrulandı** (2026-08-16). Kapsam: `config/quotas.yaml` + `config/routing.yaml`, `app/quota/`
(`models.py` pencere matematiği, `state.py` cooldown/health, `tracker.py`
`usage_events` yazımı, `probes.py` OpenRouter key endpoint + Groq header'ları),
`app/router/selector.py` (aday zinciri, eleme sebepleri, `NoProviderAvailable`),
`llm.py`'de 429 → `RateLimited` + `stream_options.include_usage` ile token
sayımı, `loop.py`'de tur içi sağlayıcı devri, `/api/quota` uçları ve kota
paneli UI. 234 test geçiyor (`uv run pytest`).

Spec §9'un Faz 3 kabul kriteri ("bir sağlayıcı tükenince sistem sessizce
diğerine geçsin, sebebi UI'da görünsün") gerçek anahtarlarla, **kurgusuz**
doğrulandı. Tek bir turda organik olarak yaşandı (audit log 18:27:31–18:27:57):

```
groq seçildi (kota uygun)
  → provider_error: LiteLLM 500 'tool_use_failed'
openrouter seçildi (groq elendi: bu turda zaten denendi)
  → rate_limited (429), cooldown 60 sn
gemini seçildi (groq: bu turda zaten denendi; openrouter: cooldown 59 sn kaldı)
  → list_dir aracını çağırdı, sonuç döndü
adım 2: groq seçildi (kota uygun)   ← cooldown bitmiş sağlayıcı geri döndü
  → cevap kullanıcıya ulaştı
```

Kullanıcı hiç hata görmedi, sohbet kesilmedi; her devir `model_switch`
olayıyla sebebiyle birlikte UI'ya ve audit log'a yazıldı.

Ayrıca canlı doğrulanan:

- Üç sağlayıcıda da tool-calling LiteLLM üzerinden çalışıyor (aynı araç
  şemasıyla üçü de doğru `tool_calls` üretti).
- `/api/quota` üç sağlayıcı için doğru; OpenRouter probe'u
  (`is_free_tier=true` → `funded=false` → günlük tavan 50).
- Sayaçlar: groq 1/30 dk · 1/1000 gün, openrouter 3/50 gün, gemini 2/20 gün.
  Reset zamanları doğru.
- Elle devre dışı bırakma: OpenRouter kapatılıp sohbet denendiğinde istek hiç
  gönderilmedi, "kullanıcı devre dışı bıraktı" sebebiyle hata döndü.

Bu tur sırasında bulunan ve aşağıda ayrı bölümleri olan üç gerçek: Gemini
model adı (`2.0-flash` ve `2.5-flash` ikisi de çağrılamıyor), LiteLLM'in Groq
rate-limit header'larını geçirmemesi, Groq'un ara sıra `tool_use_failed`
döndürmesi.

**Gemini kotası dolduruldu**: kullanıcı AI Studio panelinden okudu, 5 RPM /
250K TPM / **20 RPD**. `/api/quota` artık Gemini için de oran hesaplıyor
(doğrulandı: gün penceresi `2/20`, %90). Aşağıda "Gemini limitleri".

**Faz 4 tamamlandı ve canlı doğrulandı** (2026-08-16). Kapsam:
`app/router/classifier.py` (kural tabanlı, LLM aşaması yok — gerekçe aşağıda
"Sınıflandırıcı kural tabanlı, LLM değil"), `config/routing.yaml`'a
`trivial`/`tool_use`/`reasoning`/`long_context`/`code`/`vision` blokları,
`loop.py`'nin ilk adımda turun sınıflandırmasını, sonraki adımlarda
(ara adım/tool sonucu değerlendirme) hep `tool_use`'u kullanması, `gemini_lite`
sanal sağlayıcısı (flash-lite, ayrı kota muhasebesi — gerekçe aşağıda
"Gemini'nin iki modeli"). 247 test geçiyor (`uv run pytest`).

Spec §9 Faz 4 kabul kriteri gerçek sağlayıcılarla, WebSocket istemcisiyle
(`ws_chat.py`) kurgusuz doğrulandı:

- "merhaba" → `trivial` (%90 güven, "kısa ve fiil içermiyor") → `gemini_lite`
  seçildi, cevap geldi.
- 20K karakterden uzun metin → `long_context` (uzunluk kesin sinyal) →
  `gemini` seçildi (OpenRouter cooldown'daydı, sıradaki denendi — kota/cooldown
  süzgeci görev tipi seçiminin ALTINDA çalıştığı doğrulandı).
- Kod/hata ayıklama anahtar kelimeli uzun mesaj → `code` → `openrouter`
  seçildi.
- "ULL-Bot klasöründeki dosyaları listele" (kural karar veremedi →
  `reasoning`e düştü) → adım 1 `reasoning` zincirini kullandı (gemini),
  adım 2 ve 3 (tool sonucu değerlendirme) `tool_use` zincirine geçti (groq) —
  aynı turda görev tipinin adım adım değiştiği canlı görüldü.

**Faz 5 tamamlandı ve canlı doğrulandı** (2026-08-16). Kapsam: Ollama kuruldu
(`ollama-vulkan` GPU backend'i — gerekçe aşağıda "Ollama GPU backend'i"),
`config/litellm.desktop.yaml`'a `chat-local` (qwen2.5:3b-instruct) ve
`chat-local-big` (qwen2.5:7b-instruct, henüz bir `task_type`e bağlanmadı),
`config/routing.yaml`'da `desktop.trivial`de önde ve `desktop.tool_use`de son
çare, `settings.py`'de `ENABLE_LOCAL`/`OLLAMA_HOST`, `selector.evaluate()`'e
`ENABLE_LOCAL` kapısı, `scripts/discover_models.py` (VRAM önerisi +
Faz 1'den beri ertelenen OpenRouter ücretsiz model keşfi). Sınıflandırıcı
LLM'e taşınmadı — gerekçe aşağıda "Sınıflandırıcı yerelde de LLM olmayacak".
253 test geçiyor (`uv run pytest`).

Canlı doğrulanan: `ollama_chat/qwen2.5:3b-instruct` ve `ollama_chat/qwen2.5:7b-instruct`
ikisi de LiteLLM üzerinden tool-calling yapıyor (`list_dir` şemasıyla doğru
`tool_calls` üretti), `ollama ps` ikisinin de `100% GPU`de çalıştığını
gösterdi (Vulkan backend kurulduktan sonra — öncesinde `100% CPU`ydu),
`keep_alive: "5m"` `ollama ps`'in `UNTIL` sütununda doğrulandı.

**Faz 6 tamamlandı ve canlı doğrulandı** (2026-08-16). Kapsam:
`config/litellm.laptop.yaml` (desktop'un birebir aynı model listesi — aynı
hesaplar, cihazdan bağımsız), `tests/test_config_files.py` (iki litellm
config'inin ve `routing.yaml`'ın birbirinden sapmadığını doğrulayan 3 test).
`routing.yaml`'ın `laptop` blokları zaten Faz 5'ten beri `ollama`sızdı,
buraya dokunulmadı. 256 test geçiyor (`uv run pytest`).

Spec §9 Faz 6 kabul kriteri ("Laptop'ta `PROFILE=laptop` ile hiçbir hata
olmadan çalışsın") canlı doğrulandı: ayrı portlarda (`:4001`/`:8081`) ikinci
bir LiteLLM + FastAPI çifti `PROFILE=laptop` ve `litellm.laptop.yaml` ile
başlatıldı, "merhaba" gönderildi, hatasız `gemini_lite`ye yönlendi. Sonra
`ENABLE_LOCAL=true` zorlanarak tekrar denendi — yine `ollama` hiç aday
olmadı, çünkü dışlama `routing.yaml`'da statik (bkz. aşağıda "Laptop'ta
local: bayrak değil, statik dışlama"). LAN üzerinden masaüstü Ollama'sına
bağlanma (spec'in "opsiyonel" dediği kısım) kod olarak hazır ama gerçek
ikinci bir cihaz olmadan uçtan uca doğrulanamadı — "test edildi" diye
iddia edilmiyor (spec §12), gerekçesi ve önkoşulu (Ollama şu an sadece
`127.0.0.1`de dinliyor) README'de ve aşağıda yazılı.

**Faz 7 tamamlandı — UI HARİÇ** (2026-08-16). Kullanıcının kararı: UI'ı ayrı
bir konuşmada Opus 5 ile yapacak, bu fazda backend'in tamamı istendi. Kapsam:
kalıcı hafıza (`memory_notes` + `remember` aracı + sistem promptuna gömme),
oturum geçmişi + arama (`GET /api/sessions`, `GET /api/sessions/{id}/messages`,
`GET /api/search`), kullanım grafiği verisi (`GET /api/usage/graph`,
`quota/tracker.py` → `usage_by_day`), systemd `--user` birimleri
(`systemd/*.service` + `ull-bot.target`), kurulum scripti
(`scripts/install.sh`). Çöp kutusu/`write_file`/`edit_file`/`delete_file`
bilinçli olarak DIŞARIDA bırakıldı — kullanıcı bunu bu faz bittikten sonra
ayrıca ele almak istedi (spec'te zaten hiçbir faza atanmamıştı, bkz. Faz 6
notları). 277 test geçiyor (`uv run pytest`).

Gerekçe detayları aşağıda ayrı başlıklarda: "`remember` aracı: yaz var, ayrı
bir recall aracı yok", "Arama: `LIKE`, FTS5 değil", "systemd target'ın
`Wants=`'ı gerekiyordu — `enable` etmeden çalışmadı", "`install.sh` hiçbir
şeyi enable/start etmiyor".

Ayrıca **[`FAZ7_TESLIM.md`](./FAZ7_TESLIM.md)** yazıldı — bu, UI'ı
kuracak konuşma (Opus 5) için hazırlanmış, backend'in tüm API yüzeyini
(WebSocket protokolü + REST uçları) tek dosyada özetleyen bir teslim
belgesi. `NEXT_PHASE.md`den farkı: `NEXT_PHASE.md` "bir sonraki faza nasıl
devam edilir" diye devir notu, `FAZ7_TESLIM.md` ise "UI'ı kuracak birinin
backend'i baştan okumadan bilmesi gereken her şey" diye bir referans.

**Faz 8 tamamlandı — UI + mail + takvim** (2026-08-17). Spec'te numaralı bir
faz değil; kullanıcının UI konuşmasında istediği kapsam. Üç parça:

1. **Masaüstü uygulaması.** `app/desktop/` — pywebview + WebKitGTK ile native
   pencere, açılışta LiteLLM ve FastAPI'yi çocuk süreç olarak başlatan,
   kapanışta durduran bir süpervizör. Kullanıcının isteği birebir buydu:
   "servisleri uygulama açılınca açılıp uygulama kapanınca kapanmasını
   istiyorum". systemd yolu (Faz 7) silinmedi, ikisi bir arada çalışıyor.
2. **Mail (IMAP).** Google API/OAuth **yok** — kullanıcının açık kararı.
   `app/mail/` (imap_client / parser / classify / store / service / secrets),
   7 ajan aracı, kural tabanlı kategori + isteğe bağlı LLM ikinci geçişi.
3. **Takvim (uygulamanın kendisi).** Google Calendar/CalDAV yok.
   `app/calendar/` (ics / store / service), 6 ajan aracı, mailden toplantı
   çıkarma (ICS varsa kesin, yoksa Türkçe/İngilizce metin tahmini),
   hatırlatmalar OS'un kendi bildirim sistemine (dunst) gidiyor.

`web/` tamamen yeniden yazıldı: sol şerit + Sohbet/Mail/Takvim/Kota/Geçmiş/
Ayarlar, Mail ve Takvim'de yanda bağlamlı sohbet kutusu. 429 test geçiyor.

Gerekçe detayları aşağıda ayrı başlıklarda: "Masaüstü kabuğu: pywebview",
"Süpervizör: benimseme ve kalıntı temizliği", "Mail: IMAP, Google API değil",
"Takvim: kendi takvimimiz", "Mail kategorisi: kural önce, LLM sonra",
"Faz 8 kabul testi".

Ayrıca **[`FAZ8_TESLIM.md`](./FAZ8_TESLIM.md)** yazıldı — yeni API yüzeyi,
araçlar ve UI yapısının referansı. `FAZ7_TESLIM.md` **değişmedi**, WebSocket
protokolü aynı.

**Sıradaki adım:** bkz. **[`NEXT_PHASE.md`](./NEXT_PHASE.md)**. Yeni bir
konuşmaya başlıyorsan bu dosyayı değil onu ve `FAZ8_TESLIM.md`i oku. Bu dosya
"neden böyle yapılmış" arşivi; oradan buraya link veriliyor.


Bu dosya, spesifikasyonda belirtilmeyen ama uygulama sırasında verilen kararları
ve gerekçelerini tutar. Amaç: sonradan "neden böyle yapılmış" sorusuna hızlı cevap.

---

## Platformlar arası taşınabilirlik (tüm fazlar)

**Karar:** Geliştirme Linux'te (Arch) yapılıyor, ama Windows'a taşınacağı baştan
biliniyor. Bu yüzden faz 1'den itibaren:

- Dosya yolları her yerde `pathlib.Path` ile; string birleştirme yok.
- Kalıcı veri dizini (`db_path` vb.) `platformdirs` kütüphanesiyle hesaplanıyor.
  Linux'te bu zaten XDG'ye denk düşüyor (`~/.local/share/ai-orchestrator`),
  yani spec §12'deki Arch/XDG gereksinimiyle çakışmıyor; Windows'ta ise
  `%LOCALAPPDATA%\ai-orchestrator` gibi doğru yere düşecek.
- LiteLLM proxy'si geliştirme sırasında Docker'a **bağımlı değil** —
  `litellm[proxy]` paketiyle doğrudan `uv run litellm ...` ile çalıştırılıyor.
  Docker Compose dosyası duruyor (dağıtım/izolasyon için), ama Windows'ta
  Docker Desktop kurulu olmasa bile sistem çalışabilsin diye zorunlu değil.
- Dosya araçları ve sandbox tamamen `pathlib` üzerinden, platformdan bağımsız.
  Kabuk politikası ise POSIX'e özgü kaldı ve Windows'ta `run_shell` kapatıldı —
  gerekçe aşağıda, "Windows'ta `run_shell` kapalı (Faz 2)".

## Ortam ve paket yönetimi

**Karar:** `uv` ile Python 3.11+ sanal ortamı. Sistem pacman değil, proje
paketleri `uv sync` ile `.venv` içine kurulur — bu Windows'ta da aynı şekilde
çalışır (`uv` Windows'u native destekler).

## Web arayüzü (Faz 1)

**Karar:** Spec htmx'i tercih ediyordu ama Faz 1'de tek bir etkileşim var
(mesaj gönder → stream cevap). htmx'in SSE extension'ını eklemek bu kadar
basit bir akış için gereksiz bağımlılık. Bunun yerine vanilla JS +
`fetch` + `ReadableStream` kullanıldı. Faz 7'de UI büyüyüp (onay diyalogları,
kota paneli, katlanabilir tool blokları) daha fazla etkileşim gerektiğinde
htmx'e geçmek yeniden değerlendirilebilir — spec zaten "Claude Code hangisi
daha temizse seçsin" diyor.

## Varsayılan model (Faz 1)

**Karar:** `openrouter/openai/gpt-oss-20b:free` — 2026-08-16 tarihinde
OpenRouter'ın `GET /api/v1/models` uç noktasından canlı çekilen, `pricing.prompt
== "0"` olan modeller arasından seçildi (uydurulmadı). Bu liste sık değişiyor;
başka bir ücretsiz model tercih edilirse `config/litellm.desktop.yaml`'daki
`model` alanı değiştirilmesi yeterli — orchestrator kodu `chat-default` gibi
soyut bir `model_name` ile konuşuyor, provider/model string'i sadece LiteLLM
config'inde yaşıyor (spec §1'deki "orchestrator sağlayıcılardan habersiz
kalır" ilkesi).

## fastapi sürüm pini (Faz 1)

**Karar:** `fastapi` `0.136.3`'e pinlendi. Sebep: `litellm[proxy]==1.97.0`
(PyPI'daki en güncel sürüm) kendi internal proxy kodunda
`fastapi.dependencies.utils.get_flat_dependant` fonksiyonuna dayanıyor;
bu fonksiyon fastapi'nin en güncel sürümünde (`0.141.1`) kaldırılmış.
litellm'in kendi bağımlılık aralığı (`fastapi>=0.136.3,<1.0`) bu kırılmayı
yakalamıyor (upstream'in gözden kaçırdığı bir uyumsuzluk). `0.136.3` test
edilip fonksiyonun hâlâ var olduğu doğrulandı — litellm proxy bu sürümle
sorunsuz açılıyor. İleride `litellm` güncellendiğinde bu pin gevşetilebilir;
önce `uv run litellm --config ... --port 4000` ile açılıp açılmadığı
kontrol edilmeden pin kaldırılmasın.

## Komut sınıflandırma modeli (Faz 2)

**Karar:** `safe` için **allowlist**, `blocked` için **denylist**, ikisi
arasında kalan her şey `confirm`.

Gerekçe: tersi (her şey safe, tanıdığım kötüler blocked) bir kalıbı unutunca
sistemi bozar. Bu yönde ise bir kalıbı unutmanın cezası "kullanıcıya sorulur"
olur. `SAFE_COMMANDS` sadece salt okunur komutları içerir (`ls`, `cat`, `grep`,
`find`, `git status` ...); `npm install`, `touch`, `mv` gibi bilinen ama yazan
komutlar bilinçli olarak allowlist'te **değil**.

Ek kural: **statik olarak analiz edilemeyen komut = blocked.** Komut ikamesi
(`$(...)`, backtick), `${...}` genişletme, `$'...'` ANSI-C tırnak, süreç
ikamesi, alt kabuk/grup `( ) { }`, kapanmamış tırnak — bunların ne
çalıştıracağı çalışma anında belli olur, o yüzden reddedilir. Bu aynı zamanda
fork bomb (`:(){ :|:& };:`) gibi klasikleri de kapatıyor.

**Blocked listesi config'de değil, `app/safety/policy.py` içinde sabit.**
Spec §7.3 "blocked listesi UI'dan değiştirilemesin" diyor; config dosyasına
koymak, dosya araçlarına sahip bir ajanın kendi kısıtlarını gevşetebilmesi
demekti. Kullanıcının düzenleyebileceği şey (`config/workspace.yaml`) sadece
hangi dizinlere erişileceği.

## Obfuscation savunması: tokenizer (Faz 2)

**Karar:** Kontroller ham metinde değil, `shlex(posix=True,
punctuation_chars=True)` ile çözümlenmiş token'lar üzerinde yapılıyor.

Spec §10 iki örnek istiyordu: `"su""do"` ve `sudo`. POSIX shlex ikisini de
tek bir `sudo` token'ına indiriyor (tırnak birleştirme ve backslash kaçışı
kabuğun kendi kurallarıyla aynı). Homoglyph (`ѕudo`, kiril 's') ise farklı bir
yolla ele alınıyor: **çalıştırılabilir adı** ASCII ve düz olmak zorunda
(`_resolvable_name`), değilse blocked. Argümanlarda Unicode serbest — `grep
"merhaba dünya" x.txt` çalışmalı, kısıtlama sadece komut adında.

Bir de sarmalayıcı kontrolü var: `env sudo ...`, `xargs sudo`, `timeout 5 sudo`
gibi çağrılarda iç komut ayrıca sınıflandırılıyor, yoksa denylist tek satırla
atlanabilirdi.

## Salt okunur komutların yol argümanları (Faz 2)

**Karar:** `cat` allowlist'te olsa bile `cat ~/.ssh/id_rsa` çalışmaz.
Allowlist'teki bir komutun yol argümanları da `sandbox`'tan geçiriliyor:
yasaklı yol → `blocked`, çalışma alanı dışı → `confirm`, `$VAR` içerdiği için
doğrulanamıyor → `confirm`.

Gerekçe: "salt okunur" komutun zararsız olduğu varsayımı yanlış — asıl risk
dosyanın silinmesi değil, SSH anahtarının okunup modele (yani sağlayıcıya)
gönderilmesi. "Safe" olmanın şartı zararsızlık değil, **doğrulanabilirlik**.

Aynı sebeple `env` allowlist'te değil: hem sarmalayıcı hem de API anahtarlarını
ekrana döken bir komut. Alt süreçlerin ortamından da `*KEY*`, `*TOKEN*`,
`*SECRET*`, `*PASSWORD*` eşleşen değişkenler temizleniyor.

## Yol doğrulama: önce çöz, sonra bak (Faz 2)

**Karar:** `sandbox.resolve_path()` her yolu önce `Path.resolve()` ile
normalize ediyor (sembolik bağlar dahil), sonra deny → allow sırasıyla
kontrol ediyor.

Böylece path traversal (`../../../etc/passwd`) ve symlink kaçışı
(`~/Projects/link -> /etc`) **aynı noktada** yakalanıyor; iki ayrı kontrol
yazıp birini unutma riski yok. Deny her zaman allow'u yener. `denied_paths`'e
config'dekilere ek olarak sistem dizinleri ve `settings.data_dir` (audit log +
DB) kodda sabit ekleniyor — spec §6.3'ün "audit log ajan tarafından
okunabilir/yazılabilir olmasın" maddesi.

Bilinen sınır: TOCTOU. Kontrol ile açma arasında bir symlink değiştirilirse
yakalanmaz. Tek kullanıcılı, yerel bir sistem için kabul edildi.

## Dry-run'ın anlamı (Faz 2)

**Karar:** `DRY_RUN=true` iken salt okunur komutlar **normal çalışır**, sadece
`safe` olmayan (yazma potansiyeli olan) komutlar çalıştırılmayıp raporlanır.

Alternatif "hiçbir şey çalışmasın" olurdu, ama o zaman ajan dosya bile
okuyamaz ve dry-run modu pratikte kullanılamaz olurdu. Spec §6.3 "hiçbir yazma
işlemi gerçekleşmez" diyor — okuma yasağı değil.

## Prompt injection: işaretleme + risk yükseltme (Faz 2)

**Karar:** Araç çıktıları modele `<tool_result untrusted="true">` içinde
gidiyor ve sistem promptunda açık kural var. Buna ek olarak **taint takibi**:
bağlama güvenilmeyen içerik (dosya okuması) girdikten sonra `run_shell`
çağrıları `safe` olsa bile `confirm`'e yükseliyor.

Spec §6.4 bu yükseltmeyi web içeriği için istiyor; Faz 2'de web aracı henüz
yok, o yüzden aynı mekanizma dosya içeriğine uygulandı. Yükseltme bilinçli
olarak **sadece kabuk çağrılarını** kapsıyor: her `read_file`'ı da onaya
düşürmek, "birkaç dosya oku" gibi sıradan bir işi onay yağmuruna çevirirdi.
Kabuk zaten tehlikeli olan taraf.

Test edilen şey modelin kanıp kanmadığı değil (model deterministik değil),
**kansa bile zarar oluşmadığı**: `tests/test_prompt_injection.py` içinde model
bilerek zehirli talimata uyuyor ve `rm -rf <home>` çağırıyor — politika
durduruyor, canary dosya duruyor.

## HTTP `/chat` yerine WebSocket (Faz 2)

**Karar:** Faz 1'in `POST /chat` ucu kaldırıldı, yerine `/ws/chat` geldi.

Onay diyalogları çift yönlü iletişim gerektiriyor (sunucu sorar, kullanıcı
cevaplar, ajan bekler) — tek yönlü HTTP stream'de bu yapılamaz. Aynı sokette
hem sohbet hem onay akıyor; ajan döngüsü ayrı bir task'ta çalışıyor ki soket
onay cevabını alabilsin. Cevapsız kalan onay `APPROVAL_TIMEOUT_SECONDS` (300s)
sonunda **reddedilmiş** sayılır (fail-closed).

UI hâlâ vanilla JS (Faz 1 kararı korundu) — WebSocket + katlanabilir araç
blokları + inline onay kutusu htmx gerektirmedi.

## Oturum geçmişi: `tool` mesajları taşınmıyor (Faz 2)

**Karar:** `memory/store.load_history()` sadece `user` ve `assistant` metin
mesajlarını döndürüyor, `tool` satırlarını atlıyor.

Sebep teknik: bir `tool` mesajı ancak kendisini doğuran `tool_calls`'lu asistan
mesajıyla birlikte geçerli; yarısını geçmişe koymak OpenAI-uyumlu API'de hata
verir. Önceki turların araç sonuçları zaten asistanın o turdaki özetinde
duruyor. (Faz 1'de geçmiş hiç gönderilmiyordu, sohbetin hafızası yoktu — bu
fazda düzeldi.)

## Windows'ta `run_shell` kapalı (Faz 2)

**Karar:** `sys.platform == "win32"` ise `run_shell` doğrudan `blocked` döner.
Dosya araçları (`read_file`, `list_dir`, `search_files`) platformdan bağımsız
çalışmaya devam eder.

Faz 1'de "Windows eşdeğerleri Faz 2'de eklenecek" denmişti. Uygulama sırasında
verilen karar: PowerShell/cmd için **yarım yamalak** bir politika yazmak, hiç
yazmamaktan tehlikeli. `Remove-Item -Recurse -Force`, `Set-ExecutionPolicy`,
UAC tetikleyen çağrılar, cmdlet takma adları (`rm` → `Remove-Item`), farklı
tırnak/kaçış kuralları — POSIX tokenizer'ı bunları yanlış sınıflandırır ve
tehlikeli bir komuta `safe` diyebilir. Fail-closed davranmak doğru olan.
Gerçek Windows desteği ayrı bir iş: PowerShell AST parser'ı (`Language.Parser`)
ile ayrı bir sınıflandırıcı.

## Faz 2'de bilinçli olarak yapılmayanlar

Spec §6.3'te olup bu fazda **uygulanmayan** iki madde — atlanmadı, ertelendi:

1. **Çöp kutusu / otomatik yedek.** Spec "değiştirilen/silinen dosyanın kopyası
   `trash/<timestamp>/` altına alınsın" diyor. Bu, dosyayı silen aracın içinde
   yapılabilir — ama Faz 2'nin araç listesinde (`spec §9`) yazma aracı yok:
   `write_file`, `edit_file`, `delete_file` sonraki fazlarda. `run_shell` ile
   onaylanmış bir `rm` ise dışarıdan yakalanamaz (rastgele bir kabuk komutunun
   hangi dosyalara dokunacağı önceden bilinemez).
   **Sonuç — kullanıcının bilmesi gereken şey:** dry-run kapatıldıktan sonra
   onayladığın `rm` gerçekten siler, geri alınamaz. `settings.trash_dir` yolu
   hazır duruyor, yazma araçları gelince ilk iş orası.
2. **Ajanı ayrı sistem kullanıcısı (`aiagent`) altında çalıştırma.** Bu bir kod
   kararı değil, kurulum kararı: kullanıcı oluşturma, ACL/bind-mount, servis
   dosyası. Faz 7'de systemd user service yazılırken doğal yeri orası. Faz 2
   süreç *içi* savunmaları veriyor (politika, sandbox, onay, audit); ayrı
   kullanıcı bunların yerine değil, üstüne gelen bir katman.

## Kota sayıları config'de, canlı kaynaktan (Faz 3)

**Karar:** `config/quotas.yaml`'daki her sayı 2026-08-16'da sağlayıcının canlı
dokümanından çekildi ve kaynak linki dosyada duruyor. Kodda tek bir sabit
limit yok (spec §12).

- **OpenRouter:** 20 istek/dakika, 50 istek/gün; hesap ömür boyu 10$ kredi
  aldıysa günlük limit 1000. (`is_free_tier` alanı hangisi olduğunu söylüyor.)
- **Groq:** limitler **model başına**. `llama-3.3-70b-versatile` için
  30 RPM / 1000 RPD / 12K TPM / 100K TPD. Model değişirse o blok da değişmeli.
- **Gemini:** Google ücretsiz katman sayılarını **artık yayınlamıyor**;
  hesaba özel ve AI Studio'dan okunuyor. Bu yüzden `null` bırakıldı. Uydurulmuş
  bir sayı, sayı olmamasından daha tehlikeli olurdu: sistem "kotan var" diye
  yanlış bir güvenle o sağlayıcıyı seçerdi.
  Doğrulanan tek şey reset zamanı: "RPD quotas reset at midnight Pacific time".

Limiti bilinmeyen sağlayıcı **elenmez** (`free_ratio` 1.0 döner) — bilmediğimiz
bir limit yüzünden çalışan bir sağlayıcıyı devre dışı bırakmak yerine, 429
gelene kadar kullanmayı deniyoruz.

## OpenRouter reset saati bilinmiyor — ve bu kritik değil (Faz 3)

OpenRouter günlük limitin ne zaman sıfırlandığını dokümante etmiyor. UTC gece
yarısı varsayıldı (`reset_verified: false` ile işaretli). Bu varsayımın yanlış
olması sistemi bozmuyor, çünkü mimari zaten spec §4.2'nin hibrit modelini
uyguluyor: **sağlayıcının kendi söylediği rakam otoritedir ve yerel sayacı
düzeltir.** Varsayım sadece hiçbir canlı veri yokken kullanılıyor.

## OpenRouter probe'u ne söyler, ne söylemez (Faz 3)

`GET /api/v1/key` **kredi** kullanımını döndürüyor, ":free" modellerin günlük
istek sayacını değil. Yani "50 isteğin kaçı kaldı" sorusunun cevabı orada yok.
Bu yüzden probe'tan alınan tek kritik bilgi `is_free_tier` → günlük limitin 50
mi 1000 mü olduğu. İstek sayımı yerel sayaçtan geliyor. Panelde her pencere
için `canlı` / `tahmini` etiketi bu ayrımı kullanıcıya gösteriyor (spec §7.2).

Groq'ta durum farklı: her cevabın header'ları gerçek kalan kotayı veriyor.
Dikkat edilecek incelik — Groq'ta `x-ratelimit-*-requests` **günlük**,
`x-ratelimit-*-tokens` **dakikalık** limite ait. Tek cevap iki farklı pencereye
bilgi taşıyor, o yüzden `probe_payload.live` bir liste.

## LiteLLM'in kendi fallback'i kapalı (Faz 3)

**Karar:** `router_settings`'te fallback tanımlanmadı, `num_retries: 0`.

Spec §8 "LiteLLM'in fallback'i ile orchestrator'ın seçim mantığı çakışmasın"
diyor. Uygulamada bu "kapat" demek: LiteLLM fallback açık olsaydı 429'u kendisi
yakalayıp sessizce başka modele geçerdi, orchestrator 429'u **hiç görmezdi** —
cooldown işaretlenmez, kota sayacı yanlış kalır, panel yalan söylerdi. Sağlayıcı
devri tek bir yerde, orchestrator'da (`agent/loop.py::_call_model`).

## Sağlayıcı devri ve "sessizce geçme" (Faz 3)

Spec §9'un kabul kriteri "sistem sessizce diğerine geçsin ve UI'da sebebi
görünsün". İkisi çelişkili görünüyor; şöyle çözüldü:

- **Sessizce** = kullanıcıya hata gösterilmez, sohbet kesilmez, cevap gelir.
- **Sebebi görünür** = `model_switch` olayı gönderilir, UI'da küçük bir rozet
  çıkar (`groq → openrouter · sebep: cooldown (58 sn kaldı, sebep: 429)`),
  aynı açıklama audit log'a da yazılır.

429 dışında sağlayıcıya özgü hatalar (502, model bulunamadı) da aynı yolu
kullanıyor: o sağlayıcı bu tur için elenir ve sıradaki denenir. Faz 2 testinde
ücretsiz modelden iki kez 502/429 gelmişti — Faz 3 tam olarak bunu çözüyor.

Elenme sebebi hesaplanırken sıralama önemli: bir sağlayıcı hem "bu turda zaten
denendi" hem "cooldown'da" olabilir. Kullanıcıya gösterilen **gerçek** sebep
(cooldown/kota), bizim iç bookkeeping'imiz değil.

## `force_first` kullanıcının kapattığı sağlayıcıyı zorlamaz (Faz 3)

`fallback_behaviour: force_first`, tüm adaylar elendiğinde "hiç cevap
vermemektense kotaya rağmen dene" diyor. Ama bu, paneldeki **devre dışı bırak**
düğmesiyle kapatılmış sağlayıcıya uygulanmıyor (`state.health == "down"` olanlar
son çare turunda da atlanır).

Ayrım şu: kota tahmini bizim (yerel sayaç yanılabilir, o yüzden zorlamak
mantıklı), kapatma kararı kullanıcının. İkincisini ezersek düğme yalan söylemiş
olur. Hepsi elle kapalıysa `NoProviderAvailable` fırlar ve kullanıcı sebebi
görür. Canlı doğrulandı: OpenRouter kapatılıp sohbet denendiğinde
"Uygun sağlayıcı kalmadı — … openrouter: kullanıcı devre dışı bıraktı" hatası
geldi, istek gönderilmedi.

Cooldown (429) bundan farklı: süresi var, kullanıcı kararı değil, o yüzden
son çare turunda zorlanabilir.

## Gemini model adı: listede olmak çağrılabilir olmak değil (Faz 3)

İlk yazımda `gemini/gemini-2.0-flash` konmuştu ama doğrulanmamıştı. Üç aşamada
düzeldi ve her aşama bir ders:

1. **Doküman kontrolü**: `gemini-2.0-flash` Google'ın model sayfasında
   "Previous models → (Shut down)". `gemini-2.5-flash` ile değiştirildi.
2. **Canlı istek**: `gemini-2.5-flash` de 404 verdi —
   *"no longer available to new users"*. Yani doküman "aktif" dese bile yeni
   hesaplar o modeli çağıramıyor.
3. **`models.list` de yetmedi**: Anahtarın `GET /v1beta/models` çıktısı hem
   `gemini-2.5-flash`'i hem `gemini-2.5-flash-lite`'ı listeliyor, ama ikisi de
   `generateContent`'te 404. **Listelenmiş olmak çağrılabilir olmak değil.**

Sonuç: adaylar tek tek `generateContent` ile denendi.
`gemini-3.7-flash` → 503 (high demand), `gemini-3.5-flash` → **çalışıyor**
(tool-calling dahil). Config'e o yazıldı. Alias (`gemini-flash-latest`) yerine
sabit sürüm tercih edildi — Google'ın kendi tavsiyesi; alias sessizce değişip
davranışı kaydırabilir.

Ders: model adı uydurmamak yetmiyor (spec §12) — adın hâlâ *yaşadığını* ve
*bu anahtarla çağrılabildiğini* de kontrol etmek gerekiyor. Groq tarafı aynı
gün kontrol edildi: `llama-3.3-70b-versatile` production listesinde, üstelik
kapatılan altı modelin önerilen yerine geçeni.

## Gemini limitleri: günde 20 istek (Faz 3)

Google ücretsiz katman sayılarını yayınlamadığı için `quotas.yaml`'da `null`
bırakılmıştı. Kullanıcı kendi AI Studio panelinden okudu (2026-08-16) ve
sayılar girildi. Limitler **model başına**:

| Model | RPM | TPM | RPD |
|---|---|---|---|
| `gemini-3.5-flash` (bizim `chat-gemini`) | 5 | 250K | **20** |
| `gemini-3.7-flash` | 5 | 250K | 20 |
| `gemini-3.5-flash-lite` | 15 | 250K | **500** |

Günde 20 istek çok dar — `reserve_ratio: 0.1` ile Gemini 18. istekten sonra
eleniyor. Zincirde üçüncü sırada olduğu için bu şu an sorun değil (son çare),
ama tek başına iş yapacak bir sağlayıcı değil.

**Flash-lite'a geçmek bilinçli olarak ERTELENDİ.** 500 RPD, 20'nin 25 katı;
ama kalite daha düşük. Doğru çözüm modeli değiştirmek değil, Faz 4'te ikisini
birden zincire koymak: `trivial` görevler `flash-lite`'a, `reasoning`
görevleri `flash`'a. `routing.yaml` zaten görev tipi bloklarını destekliyor,
sadece yazılmadı. Yani bu, Faz 4'ün ilk somut kazancı.

## Gemini'nin iki modeli: `gemini_lite` sanal sağlayıcısı (Faz 4)

Yukarıdaki ertelemenin uygulanışı. Sorun: kota muhasebesi (`quotas.yaml`,
`usage_events`, `provider_state`) her şeyi **sağlayıcı adına** göre tutuyor,
model adına göre değil (spec §4.3 tasarımı böyle — tek sağlayıcı tek model
varsayımıyla yazıldı). `chat-gemini` (flash, 5 RPM/20 RPD) ve `chat-gemini-lite`
(flash-lite, 15 RPM/500 RPD) aynı `gemini` etiketi altında sayılsaydı, ikisi
birbirinin kotasını yerdi ve tek bir limit çifti (hangisi?) uygulanırdı.

**Karar:** `gemini_lite` diye gerçek olmayan, sadece bizim muhasebemizde var
olan ikinci bir "sağlayıcı" tanımlandı:

- `quotas.yaml` → `providers.gemini_lite` kendi limitleriyle (15 RPM/500 RPD),
  kendi `provider_state` satırı, kendi cooldown'u.
- `settings.py` → `PROVIDER_KEY_ALIASES = {"gemini_lite": "gemini"}`:
  `api_key_for("gemini_lite")` gerçek `GEMINI_API_KEY`'e bakar, ayrı bir
  `.env` girdisi gerekmez.
- `litellm.desktop.yaml` → `chat-gemini-lite` model_name'i gerçek LiteLLM
  yönlendirmesini yapıyor (`gemini/gemini-3.5-flash-lite`); `provider` etiketi
  zaten sadece bizim kota/loglama katmanımız için var, LiteLLM'e hiç gitmiyor
  (bkz. `llm.py` docstring: "İstemcinin sağlayıcı hakkında bildiği tek şey...
  kota sayacına ve 429 işaretine yazmak için gerekiyor").

Bu yüzden selector/quota/tracker kodlarına hiç dokunulmadı — "provider" zaten
soyut bir etiketti, `gemini_lite` de öyle bir etiket. Model adı canlı denendi
(2026-08-16, `generateContent` ile HTTP 200) — spec §12 gereği.

## Sınıflandırıcı kural tabanlı, LLM değil (Faz 4)

Spec §5.2 iki aşama tanımlıyor: kural tabanlı ön eleme, sonra kural karar
veremezse bir modele "bu mesaj ne tipte?" diye tek satırlık bir soru.
**İkinci aşama bilerek yazılmadı.**

Gerekçe: Faz 5'e kadar local model yok (spec §5.5), yani sınıflandırma
aşaması da bir API sağlayıcısını çağırmak zorunda kalırdı — her kullanıcı
isteği iki LLM çağrısına çıkardı (bir sınıflandırma + bir gerçek cevap).
Gemini'nin günde 20 istek gibi dar bir kotası varken (bkz. yukarısı) bu kabul
edilemez bir maliyet: sınıflandırma tek başına kotanın yarısını "reasoning"
cevabına gelmeden tüketebilir. NEXT_PHASE.md §2 bu tuzağı zaten işaretlemişti.

Kural tabanlı sinyaller (uzunluk, anahtar kelime, kısa+fiilsiz mesaj) spec'in
kendi kabul kriterini ("merhaba" → `trivial`, uzun döküman → `long_context`)
token harcamadan karşılıyor; kural karar veremediğinde zaten spec'in
"confidence < 0.5 ise reasoning'e düş" güvenli varsayılanına düşülüyor —
LLM aşaması olmadan da aynı sonuca varılıyor, sadece daha ucuza.

Bilinen sınır: Türkçe "fiil içeriyor mu" kontrolü (`classifier.py`
`_has_verb`) tam bir morfoloji analizcisi değil, kelime sonu eki eşleşmesi.
Kısa ama teknik bir istek ("bu fonksiyonda bug var, düzeltir misin" gibi,
80 karakter altında ve fiil ekini kaçıran bir çekimle) yanlışlıkla `trivial`e
düşebilir — canlı testte gözlendi. Kabul edilebilir: en kötü ihtimalle ucuz
bir modele (flash-lite/groq) gidiyor, cevap kalitesi düşer ama sohbet kesilmez;
gerçek NLP olmadan bu heuristiğin sıfır yanlış pozitifle çalışması zaten
beklenmiyordu. Faz 5'te local model gelince ikinci aşama (LLM sınıflandırıcı)
eklenip bu sınır kapatılabilir.

## Sınıflandırıcı yerelde de LLM olmayacak (Faz 5)

Yukarıdaki kararın açık bıraktığı soruyu kapatıyor. NEXT_PHASE.md Faz 5'e
girerken bunu bilerek soru olarak bıraktı: local model artık var, spec §5.2'nin
orijinal iki aşamalı tasarımını (kural → karar veremezse local'e sor) şimdi
kurmalı mıyız?

**Karar: hayır, kural tabanlı kalıyor.** Gerekçe:

- Faz 4'ün kural tabanlı yaklaşımı zaten spec'in kabul kriterini karşılıyor
  ("merhaba" → trivial, uzun döküman → long_context) — local'in çözdüğü tek
  şey artık "LLM çağırmak kota harcıyor" sorunuydu, ama bu sorunu ortadan
  kaldırmak yeni bir sorun açmadan olmuyor: her mesaja bir sınıflandırma
  çağrısı eklemek gecikme ekler (local modelin cevap süresi + varsa model
  yükleme gecikmesi), üstelik tam da `trivial` mesajların hız kazanması
  gereken yerde.
- Kural tabanlı sınıflandırmanın bilinen sınırı (kısa+teknik istek → yanlışlıkla
  `trivial`) zaten "sohbeti kesmiyor, sadece kaliteyi düşürüyor" diye kabul
  edilmişti (yukarısı). Bunu düzeltmek için 3B'lik bir modele güvenmek
  belirsiz bir kazanç için kesin bir maliyet (gecikme + karmaşıklık) demek.
- İki aşamalı tasarım asıl `local`ın *kendisi de bir "sağlayıcı"* olduğu bir
  dünya için mantıklıydı (spec §5.1 tablosu: `classification: local (her
  zaman)`) — ama bizim mimarimizde sınıflandırma `choose()`'dan ÖNCE,
  hangi zincirin kullanılacağını belirlemek için çalışıyor; sınıflandırmanın
  kendisi bir `task_type` zincirinden geçmiyor. Local'i sınıflandırma için
  kullanmak, `classifier.py`'ye ayrı bir LiteLLM çağrısı gömmek demek —
  `selector`/`routing.yaml` altyapısının hiç dokunmadığı yeni bir yol.

Faz 5'in gerçek katkısı sınıflandırmaya değil, sınıflandırmanın SONUCUNA:
`trivial` (ve `tool_use`'un son çaresi) artık gerçekten internetsiz
çalışabiliyor. Bu, spec'in Faz 5 kabul kriterini ("internet kapalıyken bile
trivial işler çalışsın") tam karşılıyor. Karar geri alınabilir — local model
yeterince hızlı/güvenilir hâle gelirse ve gerçek bir ölçümle gecikme
maliyetinin kabul edilebilir olduğu gösterilirse iki aşamalı tasarıma
dönülebilir; şu an için varsayımsal bir kazanç için mimari eklemiyoruz.

## Ollama GPU backend'i (Faz 5)

Arch'ın `ollama` paketi CPU-only — GPU desteği ayrı paketlerde
(`ollama-cuda`, `ollama-rocm`, `ollama-vulkan`). İlk kurulumda `qwen2.5:3b-instruct`
canlı denendiğinde `ollama ps` `100% CPU` gösterdi; `journalctl -u ollama`
sadece `id=cpu library=cpu` keşfetmiş, RTX 5060 hiç görünmüyordu.

**Karar: `ollama-vulkan`, `ollama-cuda` değil.**

- `ollama-cuda` tam CUDA toolkit'ini de çekiyor (`Depends On: ... cuda`,
  indirme 2.2 GiB + kurulum 4.7 GiB = paketin kendisiyle toplam ~3 GiB indirme
  / ~5.7 GiB disk).
  `ollama-vulkan` 7 MiB indirme, zaten kurulu `vulkan-icd-loader`'ı kullanıyor
  (`nvidia_icd.json` NVIDIA sürücüsüyle zaten geliyordu).
- llama.cpp'nin Vulkan backend'i CUDA'ya yakın performans veriyor (genel
  bilgi, bu makinede karşılaştırmalı benchmark koşulmadı) — RTX 5060 gibi
  yeni bir kart için CUDA'nın "daha olgun" avantajı da mimarinin (Blackwell)
  ne kadar yeni olduğu düşünülürse garanti değil.
- Kurulumdan sonra `journalctl -u ollama` `id=0 library=Vulkan name=Vulkan0
  description="NVIDIA GeForce RTX 5060" total="8.0 GiB" available="5.9 GiB"`
  gösterdi; `ollama ps` `100% GPU`ya döndü. Hem 3B hem 7B model canlı test
  edildi, ikisi de GPU'da, tool-calling dahil çalıştı.

Not: entegre GPU (Ryzen 5 7600'ün dahili grafik birimi) Ollama tarafından
otomatik elendi (`dropping integrated GPU; to enable, set
OLLAMA_IGPU_ENABLE=1`) — doğru davranış, RTX 5060 zaten tercih edilen kart.

## Ollama entegrasyonu: `chat-local` / `chat-local-big` (Faz 5)

Model seçimi spec §5.5'in "3B-4B sınıflandırıcı/trivial, 7B-8B genel iş"
tavsiyesini birebir izliyor: `qwen2.5:3b-instruct` (Q4_K_M, ~2.2 GiB) ve
`qwen2.5:7b-instruct` (Q4_K_M, ~4.7 GiB) — ikisi de `ollama show` ile
`tools` yeteneğini (function calling) doğruladı, ikisi de canlı test edildi
(`generateContent` değil ama eşdeğeri: `litellm.completion(model="ollama_chat/...",
tools=[...])` gerçek bir `list_dir`/`get_weather` tool_call'u üretti).

`model:` alanı `ollama/qwen2.5:3b-instruct` DEĞİL, `ollama_chat/qwen2.5:3b-instruct`
— LiteLLM'in iki Ollama provider'ı var, `ollama/` ham completion uç noktasına
gidiyor (tool-calling'i düzgün desteklemiyor), `ollama_chat/` chat-completion
uç noktasına gidiyor. Bu ayrım canlı denenerek doğrulandı, dokümandan değil.

**`repeat_penalty: 1.15` + `max_tokens: 512` sonradan eklendi.** Kullanıcı
canlı kullanırken `chat-local` bir mesaja "HAHAHA..." diye 438 token boyunca
aynı tokenı tekrarlayan bozuk bir cevap üretti. Kök sebep: Ollama'nın bu
modeldeki varsayılan `repeat_penalty`si 1.0 (sunucu loglarında doğrulandı,
`llama-server` slot ayarları) — yani tekrar cezası hiç uygulanmıyor, küçük/
quantize modellerde bilinen bir dejenerasyon modu. `repeat_penalty=1.15` ile
birkaç canlı denemede (aynı, önceden bozulan istek dahil) tutarlı, makul
cevaplar geldi. `max_tokens: 512` ayrı bir güvenlik ağı: ceza yine de işe
yaramazsa döngü bağlamı doldurana kadar sürmesin. Stokastik bir örnekleme
sorunu olduğu için %100 önlendiği iddia edilmiyor — sadece olasılığı ve en
kötü durumdaki maliyeti (token/gecikme) düşürüyor.

`keep_alive: "5m"` litellm_params'a doğrudan eklendi (spec §11: "16 GB RAM +
oyun/Blender ile VRAM çakışması" → kısa keep_alive). Ollama'nın kendi
`OLLAMA_KEEP_ALIVE` ortam değişkenini (systemd servisi için, sudo gerektirir)
değiştirmek yerine bu tercih edildi çünkü (a) per-model ayar imkânı veriyor —
ileride farklı modeller farklı keep_alive isteyebilir, (b) sistem servisine
dokunmadan, sadece bizim config dosyamızdan yönetilebiliyor.

**"VRAM baskısında otomatik API'ye düşme" (spec §9 Faz 5) için ayrı kod
YAZILMADI** — mevcut mekanizma zaten bunu karşılıyor: Ollama VRAM'e sığmayan
bir isteği reddederse, LiteLLM proxy bunu bir HTTP hata koduna çevirir,
`llm.py` bunu `LLMError` olarak fırlatır, `loop.py`'nin `_call_model`'ı bunu
zaten her sağlayıcı hatası gibi yakalayıp sıradaki adaya geçiyor (Faz 3'ten
beri var olan genel mekanizma). Local'e özel bir "VRAM kontrolü" eklemek,
zaten var olan sağlayıcı-hata-elemesini tekrar etmek olurdu.

**`chat-local-big` (7B model) hiçbir `task_type` zincirine bağlanmadı.**
NEXT_PHASE.md'nin Faz 5 kapsamı açıkça `trivial` ve `default`i işaret etmişti
(ikincisinden sonra vazgeçildi, aşağıya bak); mevcut altı görev tipinden
hiçbiri "yerel ama büyük" bir katmana ihtiyaç duymuyor — `reasoning`/`code`
zaten bulut modellerine (openrouter/gemini) gidiyor ve onlar 7B'den daha
yetenekli, `trivial` zaten küçük modelle yetiyor. 7B modeli config'e koymanın
tek amacı: spec'in önerisini canlı doğrulamak (yukarıdaki test) ve ileride
(örn. internet tamamen yokken `reasoning` için bir "yerel de olsa en iyisi"
seçeneği gerekirse) hazır olması. Zorla bir yere bağlamak yapay bir kazanç
olurdu.

**`default` zincirine local EKLENMEDİ** — NEXT_PHASE.md ilk yazıldığında bunu
planlamıştı ama Faz 4 bittiğinde `default` artık ulaşılamaz bir yol: altı
`task_type` de (`trivial`/`tool_use`/`reasoning`/`long_context`/`code`/`vision`)
`routing.yaml`'da tanımlı olduğu için `classify()` hiçbir zaman `default`'a
düşmüyor (`RoutingConfig.chain` sadece blok YOKSA `default`'a düşüyor).
`default` sadece testlerdeki `task_type="default"` zorlaması ve gelecekte
tanımsız bir görev tipi gelirse diye duruyor. Onu değiştirmek gerçek trafiği
etkilemezdi, sadece test/geriye-dönük-uyumluluk yolunu bulandırırdı.

## Laptop'ta local: bayrak değil, statik dışlama (Faz 6)

`ENABLE_LOCAL=false` (Faz 5) ve "laptop profilinde local yok" (spec §5.1) iki
ayrı sorunu çözüyor, kasıtlı olarak birleştirilmedi. `ENABLE_LOCAL` kullanıcının
elle kapattığı bir anahtar (VRAM'i oyuna bırakmak gibi, geçici); laptop'ta
local'in yokluğu ise **donanımsal bir gerçek** — laptopta muhtemelen ayrık GPU
yok ya da 8 GB VRAM yok, `ENABLE_LOCAL=true` yapılsa bile Ollama'nın çalışacak
bir şeyi olmayabilir. Bu yüzden dışlama `routing.yaml`'ın `laptop` bloklarında
statik: `ollama` o zincirlerde hiç YOK, bir bayrağa bakılmıyor.

Canlı doğrulandı: `PROFILE=laptop ENABLE_LOCAL=true` ile başlatılan bir
instance'a "merhaba" gönderildiğinde `gemini_lite` seçildi, `ollama` hiç aday
bile olmadı (bkz. yukarıdaki "Faz 6 tamamlandı" notu). Eğer bu iki mekanizma
birleştirilseydi (`laptop` profilinde `enable_local`ı kod içinde `False`'a
zorlamak gibi), kullanıcı gelecekte "laptopumda da aslında güçlü bir GPU var,
local'i açmak istiyorum" dediğinde `ENABLE_LOCAL=true` yetmez, kod
değiştirmesi gerekirdi — statik `routing.yaml` yaklaşımı bunu bir config
değişikliğine indiriyor (`laptop.trivial`/`laptop.tool_use`ye `{provider:
ollama, model: chat-local}` eklemek yeterli).

## `config/litellm.laptop.yaml`: desktop'un kopyası, ayrı doğrulama yok (Faz 6)

Groq/OpenRouter/Gemini için laptop'ta ayrı hesap ya da ayrı model seçimi yok
— aynı `.env`deki aynı API anahtarları, aynı model adları. Bu yüzden
`litellm.desktop.yaml`'da tek tek canlı denenen model adları (spec §12)
laptop dosyasında TEKRAR denenmedi — aynı sağlayıcı/model/anahtar üçlüsü,
sonuç değişmez. `chat-local`/`chat-local-big` girdileri de kopyalandı (aynı
`repeat_penalty`/`max_tokens` dahil) ama `routing.yaml`'ın `laptop` blokları
onlara hiç referans vermiyor — bkz. yukarısı. Amaç: LAN özelliği açılırsa
(aşağısı) litellm config'ine dokunmaya gerek kalmasın, sadece `routing.yaml`
ve `.env` değişsin.

**İki dosyanın birbirinden sapmaması `tests/test_config_files.py` ile
zorlanıyor** (`model_name` kümeleri eşit olmalı) — elle bakılan iki kopya
dosya, biri güncellenip diğeri unutulursa sessizce bozulur; bu spec §12'nin
"config'lerde tutarsızlık da bir tür uydurma kadar tehlikeli" ilkesinin bir
uzantısı olarak test'e bağlandı.

## LAN üzerinden masaüstü Ollama'sı: kod hazır, doğrulanmadı (Faz 6)

Spec §5.1 bunu "opsiyonel, varsayılan kapalı" diye tanımlıyor. Kod tarafı
zaten hazırdı (Faz 5'te `settings.ollama_host` bir env var, herhangi bir
adrese çevrilebilir) — Faz 6'nın yapması gereken tek şey `routing.yaml`'ın
`laptop` bloklarına `ollama`yı eklemek ve dokümante etmekti, kod değişikliği
gerekmiyor.

**Neden hâlâ kapalı ve neden uçtan uca test edilmedi:** `ss -ltnp` bu
makinenin Ollama'sının `127.0.0.1:11434`de dinlediğini gösterdi — yani LAN'dan
şu an erişilemez. Açmak için `systemctl edit ollama` ile `OLLAMA_HOST=0.0.0.0:11434`
gibi bir override gerekir; bu, masaüstünün yerel ağdaki her cihaza (misafir
Wi-Fi dahil, ağ segmentasyonu yoksa) bir LLM endpoint'i açması demek —
kullanıcının bilerek onaylaması gereken bir karar, otomatik yapılmadı. Ayrıca
bu makinede test edecek ikinci bir cihaz (gerçek laptop) yok; `PROFILE=laptop`
testi bu yüzden aynı makinede ayrı portlarla simüle edildi (yukarıdaki "Faz 6
tamamlandı" notu), LAN kısmı simüle edilemedi. "Test edildi" denmiyor —
spec §12 bunu yasaklıyor. Adımlar README'ye ve NEXT_PHASE.md'ye yazıldı.

## `remember` aracı: yaz var, ayrı bir recall aracı yok (Faz 7)

Spec §6.2 tablosu tek bir hafıza aracı listiyor: `remember` — "kalıcı nota
yaz", risk `safe`. Okuma tarafı için ayrı bir araç (`recall`, `list_notes`
gibi) tanımlamıyor. Bunu boşluk sanıp kendiliğinden eklemedik: bunun yerine
`list_notes()` her `system_prompt()` çağrısında (yani her turun başında)
notları promptuna gömüyor — model "hatırladıklarını" bir araç çağırmadan,
tıpkı `workspace`/`cwd` gibi ambient bir bağlam olarak görüyor. Bu hem
spec'e sadık kalıyor (sadece `remember` var) hem de daha az tool-call
turu demek (model her seferinde "ne hatırlıyordum?" diye sormak zorunda
değil).

`remember`in riski `safe` (spec'in kendi tablosu böyle diyor) ve `dry_run`a
bakmıyor — `writes: bool` ClassVar'ı (spec §6.1 "yazma yapabilir mi" alanı)
`Remember`de `False` bırakıldı, çünkü `dry_run`ın koruduğu şey kullanıcının
GERÇEK sistemi (dosyalar, kabuk); `memory_notes` bizim kendi SQLite'ımız,
oraya yazmak kullanıcının makinesinde hiçbir şeyi değiştirmiyor. Canlı
denendi: `qwen2.5:3b-instruct` (local model) bile "adımı Limon olarak
hatırla" isteğine `remember(key="username", value="Limon")` çağırdı, bir
SONRAKİ oturumda "adım ne?" sorusuna doğru cevap verdi — hafızanın turlar
VE oturumlar arası kalıcılığı böyle doğrulandı.

Silme (`DELETE /api/memory/{key}`) bir API uç noktası, araç değil — spec bir
"forget" aracı tanımlamıyor ve modelin kendi kendine "bu notu unutayım" diye
karar vermesi istenen bir davranış değil; yanlış/eskimiş bir notu silmek
kullanıcının (ya da ileride UI'ın) yönetim işlemi.

## Arama: `LIKE`, FTS5 değil (Faz 7)

`GET /api/search` düz bir `content LIKE '%...%'` sorgusu. SQLite'ın FTS5 tam
metin arama motoru daha iyi sıralama/skor verirdi ama bakım maliyeti var:
ayrı bir virtual table, `messages` tablosuyla senkron tutmak için trigger'lar
(`INSERT`/`UPDATE`/`DELETE` üçü de), migration karmaşıklığı. Karşılığında
kazanılan şey — tek kullanıcının kişisel sohbet geçmişinde (muhtemelen
binlerce, on binlerce mesaj, milyonlarca değil) "daha hızlı/daha alakalı
arama" — bu ölçekte gerçek bir sorun değil. `LIKE` bir tam tablo taraması
ama SQLite'ta bu boyutta veri için gözle görülür bir gecikme yaratmıyor.
Veri hacmi gerçekten büyürse (örn. Faz 7 sonrası aylarca kullanım) FTS5'e
geçmek küçük bir migration — şimdiden karmaşıklık eklemenin gerekçesi yok.

Kullanıcının sorgusundaki `%`/`_` LIKE joker karakterleri kaçırılıyor
(`search_messages` içinde) — yoksa "%" arayan biri her satırla eşleşirdi,
şaşırtıcı bir davranış olurdu.

## systemd target'ın `Wants=`'ı gerekiyordu (Faz 7)

İlk yazımda `ull-bot.target` sadece `[Install] WantedBy=` ile servislere
bağlıydı (spec'in "systemd user service" maddesi başka detay vermiyor,
standart bir target/service ayrımı denendi). Canlı test edildi: `systemctl
--user start ull-bot.target` her iki servisi de `inactive (dead)` bıraktı —
çünkü `[Install] WantedBy=`, birim `enable` EDİLMEDEN hiçbir şey yapmıyor
(sadece `enable` sırasında bir symlink oluşturmak için var, `start`ın
kendisiyle ilgisi yok). `ull-bot-litellm.service`/`ull-bot-api.service`i
ayrıca `enable` etmek gerekirdi.

**Düzeltme:** `ull-bot.target`a `Wants=ull-bot-litellm.service
ull-bot-api.service` eklendi (`[Unit]` bölümünde). Bununla `enable`e hiç
gerek kalmadan `start ull-bot.target` ikisini de başlatıyor — canlı
doğrulandı (`systemctl --user status` ikisini de `active (running)` gösterdi,
gerçek bir chat isteği cevap verdi). `enable` hâlâ mümkün (kalıcı otomatik
başlatma için, `[Install] WantedBy=default.target` duruyor) ama artık
zorunlu değil.

## `install.sh` hiçbir şeyi enable/start etmiyor (Faz 7)

Script `uv sync` yapıyor, `.env` yoksa oluşturuyor, systemd birimlerini
`~/.config/systemd/user/`e kopyalayıp `daemon-reload` çalıştırıyor —
`systemctl --user enable`/`start` ETMİYOR, sadece ekranda komutu yazdırıyor.
Bilinçli: bu iki komut kalıcı arka plan servisleri başlatmak/oturum açılışına
bağlamak demek, kullanıcının fiilen "şimdi çalışsın" demesi gereken bir an —
kurulum scriptinin kendiliğinden yapacağı bir şey değil (spec §12'nin
"arkasında ne olduğunu bilmeden bir şey açma" ruhu, sudo/pacman kararlarında
zaten aynı ilkeyle hareket edildi, bkz. Faz 5 "Ollama GPU backend'i").
Script'in kendisi test edildi (bu makinede fiilen çalıştırıldı, birimler
`~/.config/systemd/user/`e yazıldı, `systemd-analyze --user verify` hatasız
döndü) — ama `enable --now`ı kullanıcı ayrıca çalıştırdı/çalıştıracak.

## Groq'un `x-ratelimit-*` header'ları proxy'den geçmiyor (Faz 3)

`quotas.yaml`'da Groq'un `probe: response_headers` yazıyor ve
`probes.parse_groq_headers` bunun için yazılmıştı. Canlı denendi (2026-08-16,
LiteLLM 1.97.0): **proxy bu header'ları istemciye geçirmiyor.** Cevapta sadece
`x-litellm-*` var; ne `x-ratelimit-remaining-requests`, ne `llm_provider-`
önekli hâli. `litellm_settings: return_response_headers: true` da denendi,
değiştirmedi — o yüzden ayar config'de bırakılmadı (işe yaramayan ayar,
sonradan bakanı yanıltır; yerine ne denendiğini anlatan yorum kondu).

Sonuç: Groq penceresi yerel sayaçtan hesaplanıyor, panelde `tahmini` yazıyor.
Bu kabul edilebilir çünkü:

- Yerel sayaç zaten her isteği yazıyor; tek kaybettiğimiz, aynı anahtarı başka
  bir uygulamanın da kullanması durumunda görünmeyen tüketim.
- Gerçek limit aşılırsa 429 yine gelir, cooldown yine işler — yani güvenlik
  ağı header'lara bağlı değil.

`parse_groq_headers` kodu silinmedi: LiteLLM bunu ileride geçirirse ya da
proxy'siz doğrudan çağrıya geçilirse yol hazır, ve birim testleri onu koruyor.

## Groq bazen `tool_use_failed` döndürüyor (Faz 3)

Canlı testte Groq, araçlı bir istekte LiteLLM üzerinden 500 verdi:
`invalid literal for int() with base 10: 'tool_use_failed'`. Sebep bizde değil:
model bozuk bir tool-call üretiyor, Groq bunu `tool_use_failed` koduyla
reddediyor, LiteLLM de kodu `int()`'e çevirmeye çalışıp patlıyor.

Bu bilerek "düzeltilmedi" — çünkü sistem zaten doğru davrandı: hata sağlayıcıya
özgü sayıldı, Groq o tur için elendi, sıradaki sağlayıcıya geçildi, kullanıcı
kesintisiz cevap aldı. Faz 3'ün varlık sebebi tam olarak bu. Aynı model düz
(araçsız) isteklerde ve çoğu araçlı istekte sorunsuz çalışıyor, yani modeli
değiştirmeyi gerektirecek kadar sık değil.

## Kotayı ne tüketir (Faz 3)

`usage_events.status` üç değer alıyor ve sayım buna göre:

- `ok` → sayılır
- `rate_limited` → **sayılır** (istek sağlayıcıya gitti ve reddedildi)
- `error` → sayılmaz (bizim tarafımızda oluşan hata sağlayıcının kotasını
  tüketmez)

Ayrıca **en dar pencere kazanır**: dakikalık limit dolduysa günlük kota boş
olsa bile sağlayıcı elenir.

## Token sayımı: `stream_options.include_usage` (Faz 3)

Streaming yanıtta token sayısı normalde gelmiyor. `stream_options:
{"include_usage": true}` ile son chunk'ta `usage` bloğu isteniyor; desteklemeyen
sağlayıcıda LiteLLM'in `drop_params: true` ayarı parametreyi düşürüyor, o zaman
token sayısı 0 kalıyor (istek sayısı yine doğru). Canlı doğrulandı: OpenRouter
üzerinden `prompt_tokens=877, completion_tokens=56` geldi.

## `selector.py` neden `router/` altında (Faz 3)

Spec §3 dizin yapısında `selector.py` `router/` altında, `quota/` altında değil.
Faz 3'te sınıflandırıcı henüz yok ama seçici gerekiyordu; ileride taşımamak için
doğrudan spec'teki yerine kondu. `choose()` şimdiden `task_type` parametresi
alıyor ve `routing.yaml`'da sadece `default` zinciri var — Faz 4 bu dosyaya
`trivial`, `reasoning` vb. blokları eklemekten ibaret olacak, kod değişmeyecek.

Hepsi elendiğinde davranış `fallback_behaviour: force_first`: kotaya rağmen ilk
aday denenir. Gerekçe: hiç cevap vermemektense 429 riskini almak daha iyi.
`error` yapılırsa sistem hata döndürür (config'den değiştirilebilir).

## `provider_state.note` sütunu ve migration (Faz 3)

Spec §4.4'teki şemada cooldown'un **sebebini** tutacak alan yoktu; panelde
"neden kapalı?" sorusuna cevap vermek için `note` sütunu eklendi. Var olan
veritabanları için `db/connection.py` içinde küçük, idempotent bir migration
mekanizması yazıldı (`PRAGMA table_info` ile sütun var mı diye bakıp
`ALTER TABLE` uyguluyor) — `CREATE TABLE IF NOT EXISTS` var olan tabloya sütun
eklemediği için gerekiyordu.

## Veritabanı şeması (Faz 1)

**Karar:** Spec §4.4'teki tüm tablolar (`usage_events`, `provider_state`,
`sessions`, `messages`, `memory_notes`) Faz 1'de oluşturuluyor, ama sadece
`sessions` ve `messages` bu fazda gerçekten kullanılıyor (temel sohbet
geçmişi). `usage_events` ve `provider_state` şeması hazır duruyor; bu
tablolara yazma mantığı Faz 3'ün (`quota/tracker.py`) sorumluluğu — şimdiden
kısmi/tahmini bir sayaç mantığı eklenmedi ki Faz 3 tasarımıyla çakışmasın.

---

## Masaüstü kabuğu: pywebview (Faz 8)

**Karar:** Native pencere `pywebview` + sistemin WebKitGTK'sı ile açılıyor.
Electron ve Tauri değerlendirildi, ikisi de alınmadı.

**Gerekçe:** Kullanıcı "web arayüzü olmadan bir uygulama olarak çalışsın"
dedi — yani adres çubuğu, sekme, tarayıcı menüsü olmayan bir pencere. Üç
seçenek vardı:

- **Electron:** node + npm bağımlılığı, ~150 MB Chromium, ayrı bir build
  adımı. Proje şu ana kadar saf Python/uv; ikinci bir paket yöneticisi
  eklemek her kurulum ve her CI adımını ikiye katlardı.
- **Tauri:** Rust toolchain'i ve bir build adımı ister; arka planda yine
  WebKitGTK kullanıyor, yani bu makinede pywebview'le aynı motoru daha
  fazla kurulum maliyetiyle elde ederdik.
- **pywebview:** `uv add pywebview`, derleme yok, WebKitGTK zaten kurulu
  (`webkit2gtk-4.1`, doğrulandı). Süreç yönetimi de Python'da kalıyor —
  süpervizör aynı süreçte, aynı dilde.

**Bedeli:** `gi` (PyGObject) bir sistem paketi ve venv'de görünmüyor
(`include-system-site-packages = false`). PyPI'dan kurmak derleme
bağımlılıkları isterdi; onun yerine `launcher._ensure_system_gi()` sistem
`site-packages`ını `sys.path`e ekliyor. `uv sync` bunu bozmuyor çünkü venv'e
hiçbir şey yazılmıyor.

## GDK arka ucu: sınayıp seç (Faz 8)

**Karar:** Pencere açılmadan önce, ayrı bir süreçte, gerçek bir WebView
kurulumu sınanıyor (`launcher.configure_backend()`); geçmezse `GDK_BACKEND=x11`
ile tekrar sınanıyor.

**Gerekçe:** Canlı testte uygulama Hyprland/Wayland'de `Gdk-Message: Error 71
(Protocol error) dispatching to Wayland display` verip **öldü** — üstelik
kapanış temizliğimizi çalıştırmadan, yani servisler yetim kaldı. Teşhis:
sorun GTK3'ün Wayland desteği değil, **WebKit'in DMA-BUF renderer'ıymış**;
`WEBKIT_DISABLE_DMABUF_RENDERER=1` ile native Wayland sorunsuz çalışıyor.

Sınama neden düz bir GTK penceresiyle değil, WebView'lı: düz bir GTK3
penceresi bu makinede Wayland'de sorunsuz açılıyordu; hata ancak içine bir
`WebKit2.WebView` konunca çıkıyor. Sınama gerçek yapılandırmayı kurmazsa
yanlış cevap verir.

Kullanıcı `GDK_BACKEND`i kendi ayarladıysa dokunulmuyor.

## Süpervizör: benimseme ve kalıntı temizliği (Faz 8)

**Karar:** Süpervizör (a) zaten dinlenen bir portu görürse o servisi
"benimser" — başlatmaz ve kapanışta **durdurmaz**; (b) başlattığı servisleri
`data_dir/services.json`a yazar ve bir sonraki açılışta sahipsiz kalanları
toplar.

**Gerekçe (a):** Faz 7'nin systemd birimleri hâlâ duruyor ve kullanıcı onları
`enable` etmiş olabilir. Uygulamayı kapatmak, kullanıcının arka planda
sürekli çalışmasını istediği servisleri öldürmemeli. Kural tek cümle:
**açmadığımız şeyi kapatmayız.**

**Gerekçe (b):** Nazik kapanış her zaman çalışmaz. GDK protokol hatasında
süreci doğrudan `exit()` ile bitiriyor, `kill -9` de aynısını yapıyor;
ikisinde de Python'un `finally` bloğu çalışmıyor ve çocuk süreçler (kendi
süreç gruplarında oldukları için) hayatta kalıyor. Bu canlı olarak yaşandı.
Durum dosyası bunun ağını kuruyor.

PID geri dönüşümüne karşı `/proc/PID/cmdline` kontrol ediliyor — kayıttaki
numara artık bambaşka bir programa ait olabilir ve onu öldürmek kabul
edilemez.

**`PR_SET_PDEATHSIG` neden kullanılmadı:** Linux'ta bu sinyal, çocuğu
oluşturan **thread** öldüğünde tetikleniyor, süreç öldüğünde değil.
Süpervizörü pywebview'in boot thread'i çağırıyor ve o thread açılıştan hemen
sonra bitiyor — servisler daha uygulama açılırken ölürdü.

## Mail: IMAP, Google API değil (Faz 8)

**Karar:** Mail erişimi IMAP üzerinden; Gmail API / OAuth yok.

**Gerekçe:** Kullanıcının açık talebi ("google ile bi bağlantısı olmasın imap
ile gidebiliriz"). Pratik sonucu: kurulum Google Cloud Console'da proje +
OAuth client oluşturmayı değil, tek bir uygulama parolası girmeyi gerektiriyor.
Bedeli: Gmail etiketleri yerine IMAP klasörleri, ve kategoriler bizim
tarafımızda kalıyor (telefondaki Gmail'e yansımıyor).

**Parola nerede durur:** Sistem anahtarlığı (libsecret/`secret-tool`), o
yoksa `data_dir` altında 0600 bir dosya. SQLite'a **asla** yazılmıyor —
`mail_accounts.secret_backend` sadece "parola nerede" der. Böylece
veritabanını yedeklemek/kopyalamak parolayı sızdırmıyor.

**Neden yerel önbellek:** UI ve ajan araçları hep SQLite'tan okuyor, IMAP'e
gitmiyor. Liste görünümü ağ beklemiyor, sunucu erişilemezken de geçmiş
maillere bakılabiliyor, ve kategori/özet gibi bizim ürettiğimiz alanların
yaşayacağı bir yer oluyor (IMAP'te böyle bir alan yok).

**Yazma yönü:** Okundu işaretleme ve taşıma **önce IMAP'e**, başarılı olursa
önbelleğe. Ters sırada yapılsaydı sunucuya ulaşılamayan bir anda UI gerçekte
olmayan bir durumu gösterirdi.

## Mail kategorisi: kural önce, LLM sonra (Faz 8)

**Karar:** Gelen her mail kural tabanlı sınıflandırıcıdan geçiyor
(`app/mail/classify.py`); LLM sadece kuralın kararsız kaldıklarına ve sadece
kullanıcı isteyince (UI'da ayrı bir düğme) çağrılıyor.

**Gerekçe:** Gelen her mail için model çağırmak kotanın en aptalca harcanma
yolu. Maillerin çoğu başlıklardan **kesin** olarak sınıflanıyor:
`text/calendar` eki varsa toplantı davetı, `List-Unsubscribe` varsa bülten,
`no-reply@` göndericiyse otomatik bildirim. Bu, `app/router/classifier.py`nin
sohbet için kurduğu düzenin aynısı (bkz. "Sınıflandırıcı kural tabanlı, LLM
değil").

Kural **sırası** kilitli ve testlerle korunuyor (`tests/test_mail_classify.py`):
takvim eki her şeyi yener, konudaki "fatura" gövdedeki "toplantı"yı yener,
otomatik gönderici kontrolü fatura/toplantıdan **sonra** gelir (yoksa
`noreply@stripe.com`den gelen fatura "bildirim" olurdu).

## Takvim: kendi takvimimiz (Faz 8)

**Karar:** Etkinlikler kendi SQLite'ımızda; Google Calendar/CalDAV
entegrasyonu yok. Dışarıyla alışveriş ICS ile.

**Gerekçe:** Kullanıcının kararı ("takvim için uygulamanın kendi takvim
bölümü olucak"). Telefona senkron şimdilik yok, sonraya bırakıldı.

**`icalendar` paketi neden eklenmedi:** İhtiyacımız olan alt küme küçük
(VEVENT, DTSTART/DTEND/DURATION, SUMMARY, DESCRIPTION, LOCATION, ATTENDEE,
UID, VALARM'ı atlamak). Elle yazılan ayrıştırıcı ~200 satır ve tamamı
testlerle kapalı. RRULE (tekrarlayan etkinlik) **desteklenmiyor** — bir davet
RRULE taşıyorsa ilk oluşumu alınıyor ve kullanıcıya bunun bir seri olduğu
söyleniyor.

**Zaman biçimi:** Her şey ofsetli ISO8601 olarak saklanıyor, UTC'ye
çevrilmiyor. Hatırlatıcı ve takvim ızgarası yerel saatle çalışıyor; ofseti
korumak yaz saati geçişlerinde de doğru sonucu veriyor.

## Takvim araçları neden `safe` (Faz 8)

**Karar:** `create_event`/`update_event`/`mail_to_event` riski `safe`,
`delete_event` ise `confirm`. `move_mail` de `confirm` ve dry-run'a uyuyor.

**Gerekçe:** Dry-run ve onay katmanının koruduğu şey "ajanın **kullanıcının
makinesini** değiştirmesi" (spec §6). Uygulamanın kendi SQLite'ına bir satır
yazmak o kategoride değil — bu, `remember` aracıyla birebir aynı durum ve o
da `safe` (bkz. `tools/memory.py`). Her takvim eklemesinde onay diyaloğu
açmak akışı, kullanıcının asıl istediği şeyi ("meetingler için calendara
meeting eklicek") kullanılmaz hâle getirirdi.

`confirm` olanlar farklı: `delete_event` geri alınamaz (çöp kutusu henüz yok
— bkz. NEXT_PHASE.md), `move_mail` ise **dış** bir sistemde (IMAP sunucusu)
gerçekleşiyor.

## Mail içeriği düşman girdidir (Faz 8)

**Karar:** `read_mail`/`list_mail` çıktısı **her zaman** `untrusted=True`
dönüyor; özetleme istemi de maili `<email untrusted="true">` bloğuna sarıyor.

**Gerekçe:** Faz 2'de kurulan prompt injection savunmasının (bkz. "Prompt
injection: işaretleme + risk yükseltme") doğal devamı. Bir dosyanın içeriği
düşman olabilirdi; bir mailin içeriği **tanım gereği** düşmandır — onu yazan
kişi kullanıcı değil. Ajan döngüsü bu çıktıyı görünce oturumu `tainted`
işaretliyor ve sonraki kabuk çağrıları bir seviye sıkılaşıyor.

## Faz 8 kabul testi (canlı, 2026-08-17)

Gerçek modellerle, gerçek servislerle sürüldü. **İki gerçek hata bu testte
yakalandı ve düzeltildi:**

1. **Model, önizleme aracını çağırıp "ekledim" dedi.** `inspect_mail_meeting`
   hiçbir şey kaydetmiyor ama model iki toplantı için sadece onu çağırıp
   kullanıcıya "her ikisi de takvime eklendi" yanıtını verdi. Araç
   açıklamasını sertleştirmek tek başına yetmedi; **çıktının kendisine** ne
   YAPMADIĞI ve sıradaki çağrının ne olduğu yazıldı
   ("ÖNİZLEME — bu araç takvime HİÇBİR ŞEY EKLEMEDİ … şimdi
   `mail_to_event(mail_id=N)` çağır"). Tekrarda model doğru zinciri kurdu.
   Kilitleyen test: `test_inspect_araci_kaydetmedigini_ve_sonraki_adimi_soyler`.
2. **Model mail id'sini tahmin etti.** "Fatura mailini özetle" denince
   `list_mail` çağırmadan `summarize_mail(id=2)` dedi ve yanlış maili
   özetledi. Araç açıklamalarına ve sistem promptuna "id'yi asla uydurma,
   önce `list_mail`" eklendi.

Ayrıca birim testleri sırasında yakalanan iki kod hatası:

3. **`VALARM` etkinliğin açıklamasını eziyordu.** ICS ayrıştırıcısı iç
   bileşenleri atlamıyordu; gerçek Google Calendar davetlerinde VEVENT'in
   içinde her zaman bir VALARM var ve onun `DESCRIPTION`ı etkinliğinkinin
   üstüne yazılıyordu. Ayrıştırıcıya iç içe derinlik sayacı eklendi.
4. **Türkçe'de saat kayboluyordu.** "20.08.2026 **tarihinde saat** 14:00"
   biçiminde tarih ile saat arasındaki köprü kalıba girmiyordu; saat
   düşüyor ve etkinlik varsayılan 09:00'a kayıyordu — kullanıcı bunu ancak
   yanlış saatte bildirim gelince fark ederdi. Ortak bir `_CONNECTOR` kalıbı
   yazıldı, 9 gerçek yazım varyantı testle sabitlendi.

Doğrulanan davranışlar:

```
servis yaşam döngüsü: start_all → :4000 ve :8080 açıldı (2.9 sn),
  stop_all → ikisi de kapandı (0.2 sn), YETİM SÜREÇ YOK
benimseme: elle başlatılmış bir :8080 varken start_all onu benimsedi,
  stop_all ona DOKUNMADI, kendi başlattığı litellm'i durdurdu
bildirim: 2 dakika sonrasına 5 dk önceden hatırlatmalı etkinlik →
  hatırlatıcı döngüsü dunst bildirimini gönderdi ve `reminded_at` yazdı;
  gelecekteki etkinlik tetiklenmedi
UI: headless tarayıcıda 29 kontrol, JS hatası YOK — takvim ızgarası 42 hücre,
  mail 4 filtre, kota 3 sağlayıcı kartı, ayarlar 4 panel, hesap diyaloğu
  Gmail ön ayarı, ctrl+1 kısayolu
sohbet → takvim: "Yarın saat 16:30'a Dişçi randevusu ekle, 20 dk önceden
  hatırlat" → create_event(starts_at=2026-08-18T16:30, reminder_minutes=20)
sohbet → mail: list_mail → inspect_mail_meeting → mail_to_event zinciri,
  iki toplantı da takvime eklendi; ICS'li olan %100 (okundu), düz metinli
  olan %95 (tahmin) güvenle
özet: fatura maili özetlendi → "249,90 TL, son ödeme 28.08.2026" (doğru)
```

## `hidden` niteliği ve `display` bildirimi çakışması (Faz 8, kullanıcı bildirdi)

**Belirti:** Uygulama açıldığında arayüzün tamamı sönük görünüyordu ve
hiçbir şeye tıklanmıyordu. Kullanıcı ekran görüntüsüyle bildirdi.

**Sebep:** Onay diyaloğunun kaplaması (`<div class="modal-backdrop"
id="modal-backdrop" hidden>`) hiç gizlenmiyordu. HTML'in `hidden` niteliği
UA stylesheet'teki `[hidden] { display: none }` kuralıyla çalışır ve bu
kural author stylesheet'teki **her** `display` bildirimi tarafından ezilir.
`style.css`teki `.modal-backdrop { display: grid; ... }` tam olarak bunu
yapıyordu. Sonuç: `rgba(5,7,11,0.8)` bir örtü tüm sayfayı kaplıyor
(`z-index: 90`), yani

- her renk `0.2 × asıl + 0.8 × (5,7,11)` oluyordu — ölçüldü: zemin
  `#0b0e14` (11,14,20) yerine (6,8,13), metin (230,…) yerine (50,…);
- ve örtü tüm tıklamaları yutuyordu, uygulama kullanılamaz haldeydi.

Ekran görüntüsündeki "gizemli yatay çizgi" de bunun parçasıydı: boş
`#modal` kutusunun 2px kenarlığı (`--line-strong`, `width: min(560px,100%)`).

Aynı tuzak `.dock-context` (`display: flex`, JS'ten `hidden` ile gizleniyor)
için de geçerliydi.

**Düzeltme:** `style.css`in en başına tek bir kural:

```css
[hidden] { display: none !important; }
```

`!important` burada doğru araç — amacın kendisi sonraki tüm `display`
bildirimlerini yenmek.

**Neden testler yakalamadı — asıl ders.** Faz 8'in UI doğrulaması headless
tarayıcıda 51 kontrol çalıştırdı ve hepsi geçti. Çünkü hepsi
`element.click()` ile JS'ten tetikliyordu: bu, DOM olayını doğrudan hedefe
gönderir ve **hit-testing'i atlar**. Sayfayı kaplayan görünmez bir örtü
böyle bir testte hiç görünmez.

Bunun üzerine iki koruma eklendi:

1. `tests/test_web_assets.py` — `[hidden]` kuralının varlığını, kaplamanın
   `hidden` ile başladığını, `.empty` yerleşimini ve CSS değişkenlerinin
   tanımlılığını statik olarak kontrol ediyor (tarayıcı gerektirmiyor).
2. Canlı doğrulamada artık `Input.dispatchMouseEvent` ile **gerçek fare
   olayı** ve `document.elementFromPoint` ile ulaşılabilirlik kontrolü
   yapılıyor. Düzeltme sonrası: sol şerit düğmeleri, besteci, gönder
   düğmesi ve ipuçları "ULAŞILABİLİR", gerçek tıklamayla Mail paneline
   geçilip bir mail açıldı.

## Boş durum dikeyde dağılıyordu (Faz 8, aynı raporla)

`.empty` kuralı `display: grid; place-items: center; height: 100%` idi.
`place-items` öğeleri kendi grid alanlarının ortasına koyar, ama örtük
satırlar kabın yüksekliğini eşit böler — ölçüldü: `gridTemplateRows:
162px 146px 163px`. Yani ikon, başlık ve metin 8px gap yerine ~150px
arayla duruyordu; ekranda sayfa yarım yüklenmiş gibi görünüyordu.

Düzeltme: `align-content: center` (satırları içeriğe göre paketleyip
topluca ortalar). Sonrası: `40px 23px 40px`, aralar 10px.

Aynı turda küçük metinlerin kontrastı da ölçüldü ve `--text-3` (#6d7789)
koyu zeminde **3.97:1** çıktı — WCAG AA küçük metin için 4.5:1 istiyor ve
sol şerit etiketleri gerçekten okunmuyordu. `--text`/`--text-2`/`--text-3`
bir tık açıldı; ölçülen yeni oranlar 5.83:1 ile 17.20:1 arasında.

## Hesap ekleme Google ile karşılıyor (Faz 8b, kullanıcı isteği)

**İstek:** "hesap ekle dediğimiz zaman orda direk google butonu çıksın,
google kısmına gidelim hesabı seçelim o gerisini halletsin."

**Yapılan:** Diyalog artık OAuth yapılandırılmışsa **Google düğmesiyle
açılıyor** — e-posta yazmak gerekmiyor. IMAP formu altta katlı duruyor
(`Başka bir hesap ekle (IMAP) ▾`). Yapılandırma yoksa sıra tersine
dönüyor: düğme kapalı, nedeni yazılı ve IMAP formu açık başlıyor.

"Gerisini halletsin" tarafı zaten backend'de hazırdı: `add_google_account()`
adresi `userinfo`dan okuyor, sunucuyu `imap.gmail.com:993` olarak sabitliyor,
yenileme jetonunu anahtarlığa yazıyor **ve kaydetmeden önce jetonun IMAP'te
gerçekten çalıştığını doğruluyor** — Workspace yöneticisi IMAP'i kapatmışsa
bu ancak orada anlaşılır; başarısızsa jeton geri alınıyor, yarım hesap
bırakılmıyor.

**Cloud Console kurulumu canlı doğrulandı** (2026-08-17): üretilen
yetkilendirme adresi sunucu tarafından Google'a GET'lendi; Google isteği
kabul edip hesap seçici sayfasına yönlendirdi. Yani `client_id`, kayıtlı
yönlendirme adresi (`http://127.0.0.1:8080/api/mail/oauth/callback`) ve
kapsamlar tutarlı. Bu kontrol `redirect_uri_mismatch` / `invalid_client`
gibi hataları kullanıcı düğmeye basmadan önce görmenin ucuz yolu.

### Hata sonucu da pencereye taşınıyor

Kullanıcı akış sırasında iki ayrı yerde: onay ekranı **tarayıcıda**,
uygulama penceresi ise sonucu yokluyor. İlk sürümde `exchange_code()`
yalnızca BAŞARIYI `pending.result`a yazıyordu; bir hata olsa tarayıcıda
sayfa görünüyor ama uygulama penceresi sonsuza kadar "Bekleniyor…" diyordu.
`set_pending_result()` eklendi, callback hem başarıyı hem hatayı yazıyor,
UI hatayı gösterip düğmeyi tekrar açıyor. Ayrıca 5 dakikalık bir tavan var.

## Testler `.env`e bağlı olmamalı — autouse izolasyon (Faz 8b)

Kullanıcı `.env`e gerçek `GOOGLE_CLIENT_ID`/`SECRET` ekleyince **dört test
kırıldı**: "OAuth yapılandırılmamışken şöyle davranmalı" diyen testler
gerçek ayarları okuyordu. `conftest.py` bu ilkeyi zaten sağlayıcı
anahtarları için savunuyordu (bkz. `workspace` fixture'ının docstring'i) ama
liste eksikti.

Çözüm: `conftest.py`ye **autouse** bir `_izole_ortam` fixture'ı. İki şeyi
birden kapatıyor:

- `google_client_id`/`secret` boşaltılıyor → testler geliştiricinin
  makinesinde de CI'da da aynı sonucu veriyor. OAuth'un açık olmasını
  isteyen testler değeri kendi fixture'larında set ediyor.
- `db_path` tmp'ye çevriliyor → fixture kurmayı unutan bir test bile
  kullanıcının gerçek `orchestrator.db`'sine yazamıyor.

İkincisi bir varsayımı doğrulamak için "kanarya" yöntemiyle sınandı: gerçek
veritabanına bir satır yazıldı, tüm paket çalıştırıldı, satır yerinde kaldı.
(Faz 8 sırasında görülen veri kaybının kaynağı testler değil, geliştirme
sırasında elle çalıştırılan temizlik komutlarıydı.)

## Google OAuth, kişisel Gmail için pratik değil (Faz 8b, canlı bulgu)

**Ne oldu:** Kullanıcı Cloud Console kurulumunu tamamlayıp "Google ile
bağlan"a bastı ve `403: access_denied` aldı — "ULL-Bot, Google doğrulama
sürecini tamamlamadı. Yalnızca geliştirici tarafından onaylanan test
kullanıcıları erişebilir." Kullanıcının cevabı: "bunu her kullanıcı için
tek tek izin vermek istemiyorum."

**Araştırma sonucu (Google dokümanı, 2026-08 itibarıyla doğrulandı):**

| Onay ekranı durumu | Kim bağlanabilir | Yenileme jetonu |
|---|---|---|
| Testing + External | yalnızca elle eklenen test kullanıcıları (≤100) | **7 günde bir iptal** |
| In production + External | herkes | süresiz — ama bu kapsam için Google DOĞRULAMASI şart |
| Internal (Workspace) | yalnızca organizasyon üyeleri | süresiz, test listesi yok |

Kritik ayrıntı: 7 günlük iptal, yalnızca ad/e-posta/profil isteyen
uygulamalar için geçerli DEĞİL — biz `https://mail.google.com/` istiyoruz
(Gmail IMAP'i daha darını kabul etmiyor) ve o "restricted" bir kapsam.
Yani **test kullanıcısı eklemek kalıcı bir çözüm değil**: hesap haftada bir
kopar. "Production"a almak ise bu kapsam için güvenlik denetimi (CASA)
gerektiriyor — kişisel bir araç için pratik değil.

**Sonuç:** Kişisel Gmail adresleri için doğru mekanizma **uygulama
parolası**dır; OAuth'un buradaki sürtünmesi Google'ın kısıtlı Gmail
kapsamları için bilinçli politikası. Workspace adresleri için OAuth
kullanılabilir hâle geliyor — ama yalnızca Cloud projesi o organizasyona
aitse ve onay ekranı `Internal` yapılırsa.

**Kodda yapılan:** OAuth kaldırılmadı (kullanıcı "ikisi de olsun" demişti,
ve Internal senaryosunda gerçekten çalışıyor). Bunun yerine hata yolu
kullanıcıyı çıkmazda bırakmayacak hâle getirildi:

- `oauth.explain_error()` ham kodu yapılabilir bir açıklamaya çeviriyor.
  `access_denied` özellikle iki farklı sebebi ayırıyor ve **7 gün tuzağını
  açıkça söylüyor** — bunu söylemezsek kullanıcı bir hafta sonra "mail
  neden durdu" diye geri döner.
- Hata hem tarayıcı sayfasına hem uygulama penceresine gidiyor; pencerede
  doğrudan "Uygulama parolasıyla ekle (önerilen)" düğmesi çıkıyor ve IMAP
  formunu açıyor.
- Ayarlar'daki OAuth paneli bu tuzağı **denemeden önce** uyarıyor.

Dersin genel hâli: bir dış servisin politikası bizim kod kalitemizle
çözülemiyorsa, doğru davranış onu gizlemek değil, kullanıcıya maliyetini
zamanında ve açıkça söylemek.

## HTML mail gövdesi: kum havuzu + engelli uzak resimler (Faz 8c)

**Belirti:** Kullanıcı "resimler görünmüyo" dedi ve gönderdiği ekran
görüntüsünde mail, "Unfortunately, your email client cannot display HTML"
uyarısıyla dolu düz metin olarak duruyordu.

**Sebep:** `body_html` kaydediliyordu ama UI yalnızca `body_text` gösteriyordu.
Bu kutuda **200 mailin 194'ünde HTML gövdesi var** — yani neredeyse her mail
bozuk görünüyordu, üstelik göndericilerin düz metin alternatifine koyduğu
uyarı yüzünden kullanıcı haklı olarak uygulamayı kusurlu sanıyordu.

**Çözüm (`web/js/mailbody.js`):** İki katmanlı savunmayla HTML render.

1. **Temizleme.** `DOMParser` ile (regex DEĞİL — regex tabanlı HTML
   temizleyiciler kaçırmakla ünlü): `<script>`, `<iframe>`, `<object>`,
   `<form>`, `on*` öznitelikleri ve `javascript:` adresleri siliniyor.
2. **Kum havuzu.** Gövde `sandbox` nitelikli bir `<iframe srcdoc>` içinde.
   `allow-scripts` **verilmiyor**, yani içeride hiçbir betik çalışamaz.
   `allow-same-origin` veriliyor ki ana sayfa iframe'e erişip bağlantı
   tıklamalarını yakalayabilsin (sistem tarayıcısına yönlendirmek için) —
   betik çalışamadığı için bu kombinasyon güvenli. İkisi BİRLİKTE
   verilseydi mail içindeki bir betik ana sayfaya erişebilirdi; test
   bunu kilitliyor.

**Uzak resimler varsayılan ENGELLİ.** Pazarlama maillerindeki resimler çoğu
zaman takip pikselidir: yüklenmesi göndericiye "bu kişi maili şu saatte
açtı" bilgisini verir. Thunderbird ve Gmail de aynısını yapıyor. Kullanıcı
tek tıkla açabiliyor; CSP'deki `img-src` o zaman genişliyor.

Engellenen resim, `src`i silinmek yerine **saydam 1px** ile değiştiriliyor:
`src`siz bir `<img>` tarayıcıyı "bozuk resim" moduna sokup alt metnini tam
boyutta basıyor ve 30 tanesi sayfayı dolduruyordu.

## `.stage`de eksik `min-height: 0` — kaydırmayı tamamen bozuyordu (Faz 8c)

**Belirti:** "buralarda kaydıramıyorum."

**Ölçüm:** Mail detay paneli `scrollHeight=25041 clientHeight=25041` —
yani panel kısıtlanmak yerine içeriğine göre 25.000 piksele **büyümüştü**,
bu yüzden kayacak bir şey yoktu; içerik ekranın altından taşıp kayboluyordu.

**Sebep:** `.stage` bir grid öğesi ve grid öğelerinin varsayılan
`min-height`ı `auto`dur — **içeriklerinin altına küçülmeyi reddederler.**
Uzun bir HTML mail açılınca `.stage` şişti, altındaki `height: 100%`
zincirinin tamamı o şişmiş boyu miras aldı. Zincirdeki diğer sekiz halkada
(`.views`, `.view`, `.split`, `.split-main`, `.mail-layout`, …) bu satır
zaten vardı; eksik olan tek yer `.stage`di, o yüzden hata gözle
bulunamıyordu.

`tests/test_web_assets.py` artık zincirdeki her halkayı tek tek kontrol
ediyor — bu sınıf bir daha sessizce geri gelmesin.

## Spam sürgünü (Faz 8c, kullanıcı isteği)

> "spam mailleri tümü kısmında görünmesin, en altta spam olarak ayrı görünsün"

`spam` kategorisi eklendi ama sıradan bir kategori değil: `HIDDEN_FROM_ALL`
kümesinde ve **hiçbir genel görünüme karışmıyor** — ne "Tümü"ye, ne
"Okunmamış"a, ne aramaya. Yalnızca kendi kategorisi seçilince görünüyor ve
şeritte "Ayrılanlar" başlığı altında, en altta duruyor.

`counts()` de buna uyuyor: `total`/`unread` spam'i saymıyor (liste 200
gösterip rozet 307 deseydi kullanıcı haklı olarak "eksik mail var" derdi),
ama kategori kırılımı sayıyor çünkü spam satırının kendi sayısı oradan
geliyor.

Spam kaynakları: (a) sunucunun spam klasörü — `is_spam_folder()` ve RFC 6154
`\Junk` bayrağı, (b) `X-Spam-Flag` gibi sunucu başlıkları, (c) kullanıcının
elle işaretlemesi ("🚫 Spam" düğmesi). Sunucunun kararı bizim kurallarımızın
**önünde** geliyor: Gmail'in spam filtresi bizimkinden iyi ve bir spam
mailini "fatura" diye sınıflayıp listeye sokmak kullanıcının tam da
istemediği şey.

Bir tuzak: senkron sırasında bilinen mesajların bayrakları tazelenirken
`include_hidden=True` gerekiyor, yoksa spam'e alınmış mailler sunucuda
okunsa bile burada okunmamış kalırdı.

## Google OAuth kaldırıldı (Faz 8c)

Kullanıcı "google iptal, sadece imap ile ilerleyelim … google ile ilgili
kullanılmayan şeyleri kaldır" dedi. Kaldırılanlar: `app/mail/oauth.py`,
`/api/mail/oauth/*` uçları, XOAUTH2 kimlik doğrulama yolu,
`GOOGLE_CLIENT_ID`/`SECRET` ayarları, UI'daki "Google ile bağlan" düğmesi ve
OAuth paneli, `tests/test_mail_oauth.py`.

**Kalanlar** (bunlar kullanılıyor): uygulama parolası sayfasına `authuser`
ile doğrudan link, Workspace sunucu düzeltmesi (`imap.gmail.com`), kullanıcı
adı kilidi, parola boşluk temizleme, ve `POST /api/open-external` (maildeki
ve takvimdeki bağlantılar da bunu kullanıyor).

`mail_accounts.auth_type` sütunu duruyor ama hep `'password'`: SQLite'ta
sütun silmek tabloyu yeniden yazmayı gerektiriyor ve bu alanın maliyeti yok.

Gerekçenin tamamı bir önceki başlıkta ("Google OAuth, kişisel Gmail için
pratik değil"): kısıtlı `mail.google.com` kapsamı yüzünden doğrulanmamış
uygulamaların yetkilendirmeleri 7 günde bir iptal ediliyor.


## Web arastirma: arama + sayfa okuma (Faz 9, kullanici istegi)

> "internette arastirma yapmasini istiyorum ... '4000TL butcem var, bana
> alabilecegim kulakliklari karsilastir' dedigimde bir kiyaslama versin"

Eklenen: `app/web/` (arama + getirme), `app/agent/tools/web.py`
(`web_search`, `fetch_url`), ve arayuzde **markdown tablo** destegi.

### Arama motoru: DuckDuckGo, anahtarsiz

Brave/Google CSE daha kararli ama kullanicidan bir hesap daha acmasini
istiyor; projenin "ucretsiz katmanla calis" ilkesine (spec 1) aykiri.
DDG'nin `html.duckduckgo.com` uc noktasi anahtarsiz calisiyor.

**Bedeli iki tane ve ikisi de canli testte cikti:**

1. **Hiz siniri.** Olculdu: iki hizli sorgudan sonra DDG **HTTP 202** ve
   icinde "anomaly" gecen, sonucsuz bir sayfa donduruyor. 202 bir hata
   kodu olmadigi icin ilk surum bunu "sonuc yok" saniyordu -- ve model
   "demek ki bulamadim" deyip aramayi TEKRARLIYORDU, her tekrar siniri
   daha da sikiyordu. Simdi: kendi kendimize 2.5 sn aralik koyuyoruz,
   202/429/"anomaly" hiz siniri olarak taniniyor, ve hata mesaji modele
   ne YAPMAMASI gerektigini soyluyor ("ayni sorguyu art arda tekrarlama").
   Ayrica 5 dakikalik sorgu onbellegi var.
2. **Bicim kirilganligi.** Resmi API degil, HTML kazima. Iki uc nokta
   (`html` ve `lite`) ve iki ayri ayristirici var; biri bos donerse
   digeri deneniyor. Hicbiri tutmazsa **hata firlatiliyor** -- sessizce
   bos liste donmek modelin uydurmaya baslamasi demek.

### SSRF savunmasi sart

`fetch_url`'un adresini **model** seciyor ve model, az once okudugu bir
sayfanin metninden etkilenmis olabiliyor. Yani karar dolayli olarak bir
yabancinin etkisi altinda. Savunmasiz birakilsaydi bir sayfa modele
`http://127.0.0.1:8080/api/mail/messages` okutup kullanicinin maillerini
kendi ciktisina tasiyabilirdi.

`app/web/fetch.py`: yalnizca http/https, yalnizca genel IP'ler (ozel,
loopback, link-local, reserved hepsi kapali), **her yonlendirme adimi
yeniden denetleniyor** (acik yonlendirme kapiyi atlamasin), 2 MB tavan.

Bir incelik: `javascript:alert(1)` icinde `://` yok. Ilk surum "sema yok"
sanip basina `https://` ekliyordu ve `https://javascript:alert(1)` sema
denetiminden geciyordu. Artik sema `^scheme:` kalibiyla once taniniyor.

### Markdown tablosu -- ve icinden cikan iki sessiz hata

Karsilastirma cevabinin tasiyicisi tablo; desteklenmeden model dogru
cevabi verse bile ekranda boru isaretleriyle dolu bir metin yigini
gorunuyordu. Tablo destegi eklenirken `markdown()` icinde **iki eski hata**
ortaya cikti:

1. Blok yer tutucusu " BLOCK0 " boskuga bagliydi; paragraf kontrolu
   `trim()` edilmis metne bosluklu kalipla bakiyordu. Sonuc: fenced kod
   bloklari ekranda **"BLOCK0" yazisi** olarak cikiyordu. Bastan beri
   boyleymis, modeller nadiren kod blogu urettigi icin fark edilmemis.
2. Yer tutucu kendi paragrafinda kalmayinca tablo bir `<p>` icine
   sariliyordu -- `<div>` bir `<p>` icinde gecersiz, tarayici paragrafi
   erken kapatip duzeni bozuyor.

Ikisi de "gozle bakinca calisiyor gibi" duran turden. `markdown()` artik
gorunmez bir yer tutucu kullaniyor ve davranisi
`tests/markdown_check.mjs` ile (pytest'ten Node cagrilarak) 22 iddiayla
kilitli.

## Mail govdesi beyaz zeminde cizilir (Faz 9)

Kullanici "resimler gorunmuyor" derken gonderdigi ekran goruntusunde
Google'in "Guvenlik uyarisi" maili **koyu gri metni koyu zeminde** basmis
ve hic okunmuyordu. Sebep: gonderenlerin neredeyse tamami maili beyaz
zemin varsayarak tasarliyor ve renkleri satir ici stillerle dayatiyor;
`color-scheme: dark` bunu duzeltmiyor cunku `color:#202124` gibi satir ici
degerler her seyi eziyor.

Cozum, her gercek mail istemcisinin yaptigi sey: mail kendi **beyaz
kagidinda** duruyor, uygulamanin geri kalani koyu kaliyor.

## Gmail'in Spam klasoru senkronlaniyor (Faz 9)

> "maile spam olarak gelenleri otomatik algilamiyo, gmail sitesinde
> otomatik spama dusuyodu, burda da o sekilde olmasi gerek"

Dogru teshis: yalnizca `INBOX` senkronlaniyordu, Gmail'in spam kutusuna
dusenler hic gelmiyordu. Artik klasor verilmezse gelen kutusu **ve** spam
klasoru senkronlaniyor; spam klasorunun gercek adi RFC 6154 `\Junk`
bayragindan bulunuyor (Gmail'de bu ad hesabin diline gore degisiyor).

O klasorden gelen her mail `spam` isaretleniyor ve surgun kurallarina
takiliyor. Kendi kural setimizi Gmail'in filtresinin onune koymuyoruz --
onunki daha iyi ve zaten kararini vermis.

Canli dogrulandi: `[Gmail]/Spam`'den 8 mail geldi, hepsi `spam` oldu,
"Tumu" sayaci onlari saymadi.


## Kisir dongu korumasi (Faz 9, canli testte yakalandi)

Canli testte zayif bir model `web_search`i **bos sorguyla** cagirdi, guvenlik
katmani "reddedildi" dedi, model **ayni cagriyi** tekrar yapti -- ve bu adim
limiti dolana kadar surdu. Kullanici dakikalarca cevap bekledi; her adim bir
model cagrisi oldugu icin kota da bosa gitti.

Reddedilen bir cagri kendi basina turu durdurmuyor ve durdurmamali da: model
baska bir yol denemek isteyebilir. Durduran sey **ayni cagrinin** ust uste
basarisiz olmasi. `AgentLoop` artik `(arac_adi, argumanlar)` imzasi basina
basarisizlik sayiyor; 3'e ulasinca tur `stopped` olayiyla bitiyor ve
kullaniciya ne oldugu soyleniyor.

Sayac araç adina degil **ad + arguman** imzasina bakiyor: farkli sorgularla
denemek "takilmak" degil, model yol ariyor demektir.

Ayrica `web_search`/`fetch_url`un bos-arguman hatasi artik modele ne yapmasi
gerektigini soyluyor (ornek cagri + "ayni cagriyi tekrarlama"), cunku
"REDDEDILDI" tek basina modeli yonlendirmiyordu.

## Arama guvenilirligi: kazima yetmiyor, Brave opsiyonel (Faz 9)

Canli olculdu: DDG iki hizli sorgudan sonra HTTP **202** + "anomaly" sayfasi,
Mojeek dogrudan **Captcha** donduruyor. Yani ucretsiz kazima tek basina bir
arastirma turunu tasiyamiyor.

Eklenen savunmalar (hepsi kazima yolunda):

- Kendi kendimize 2.5 sn aralik (`MIN_INTERVAL`).
- 5 dakikalik sorgu onbellegi -- model bir turda benzer sorgulari
  tekrarliyor ve her tekrar siniri sikistiriyor.
- Hiz siniri tespiti: 202/429/captcha/anomaly.
- **Bir kez otomatik bekle-tekrar dene.** Ilk surumde modele "20 sn bekleyip
  tekrar dene" diyorduk; model bekleyemiyor, hemen tekrar deniyor ve engeli
  sikistiriyordu. Beklemeyi artik biz yapiyoruz.
- Iki farkli DDG uc noktasi (`html` ve `lite`), iki ayri ayristirici.

Ve bir cikis yolu: **`BRAVE_API_KEY`**. Resmi API, ucretsiz katmani ayda
2000 sorgu, kredi karti istemiyor. Anahtar varsa arama ONCE Brave'i dener,
o duserse kazimaya doner. Anahtar yoksa hicbir sey degismiyor.

Bu, projenin "ucretsiz katmanla calis" ilkesini (spec 1) bozmuyor: anahtarsiz
kurulum calismaya devam ediyor, anahtar yalnizca guvenilirligi artiriyor --
saglayici anahtarlariyla ayni model.
