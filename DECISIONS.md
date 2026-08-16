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

**Sıradaki adım: Faz 5 — Local model** (spec §5.5/§9). Yeni bir konuşmaya
başlıyorsan bu dosyayı değil, **[`NEXT_PHASE.md`](./NEXT_PHASE.md)** dosyasını
oku — orada sistemin anlık durumu, kapsam ve bozulmaması gereken kurallar var.
Bu dosya "neden böyle yapılmış" arşivi; oradan buraya link veriliyor.


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
