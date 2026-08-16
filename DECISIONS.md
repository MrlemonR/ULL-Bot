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

**Sıradaki adım: Faz 3 — Çoklu sağlayıcı ve kota** (spec §9). Yeni bir
konuşmada buradan devam edilecekse:

1. Önce bu dosyayı ve `README.md`'yi oku, sonra `ORCHESTRATOR_SPEC.md` §4
   (Sağlayıcılar ve Kota Takibi) ve §10.
2. Faz 3 kapsamı: Groq + Gemini'yi `config/litellm.desktop.yaml`'a ekle,
   `quota/tracker.py` (her istekte `usage_events`'e yaz — tablo Faz 1'den beri
   hazır ve hâlâ boş), `quota/probes.py` (OpenRouter `GET /api/v1/key`, Groq
   cevap header'ları), 429 yakalama + `provider_state.cooldown_until`,
   kota paneli UI.
3. Faz 3 için hazır zemin: `app/agent/llm.py` şu an sadece streaming yapıyor,
   token sayımı/latency ölçümü yok — `usage_events` yazımı oraya girecek.
   Ajan döngüsü zaten her adımı `audit.py`'ye yazıyor, kota sayacı ayrı.
4. **Faz 2'de bilinçli olarak yapılmayanlar** (aşağıda gerekçeleriyle var,
   Faz 3'te "unutulmuş" sanılmasın): çöp kutusu/otomatik yedek, ajanı ayrı
   sistem kullanıcısı altında çalıştırma, Windows kabuk politikası.
5. Ücretsiz model gerçeği: test sırasında `openai/gpt-oss-20b:free` iki kez
   upstream 502/429 verdi (sağlayıcı kapasitesi doldu). Kod bunu düzgün
   yakalayıp kullanıcıya hata olarak gösteriyor — zaten Faz 3'ün çözeceği şey.
6. LiteLLM proxy (port 4000) ve FastAPI (port 8080) arka planda çalışıyor
   olabilir; `curl localhost:8080/api/config` ile kontrol et. FastAPI
   `--reload` olmadan çalıştırıldıysa kod değişince yeniden başlatılmalı.
7. `litellm[proxy]` uyumluluğu için `fastapi==0.136.3` pinli — bu pin'i
   gereksiz yere kaldırma (aşağıda "fastapi sürüm pini").
8. Henüz git commit atılmadı (kullanıcı henüz istemedi) — commit/push
   sadece açıkça istenirse yapılmalı.


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

## Veritabanı şeması (Faz 1)

**Karar:** Spec §4.4'teki tüm tablolar (`usage_events`, `provider_state`,
`sessions`, `messages`, `memory_notes`) Faz 1'de oluşturuluyor, ama sadece
`sessions` ve `messages` bu fazda gerçekten kullanılıyor (temel sohbet
geçmişi). `usage_events` ve `provider_state` şeması hazır duruyor; bu
tablolara yazma mantığı Faz 3'ün (`quota/tracker.py`) sorumluluğu — şimdiden
kısmi/tahmini bir sayaç mantığı eklenmedi ki Faz 3 tasarımıyla çakışmasın.
