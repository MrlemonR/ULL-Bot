# Devir notu — Faz 5'e başlarken oku

Bu dosya yeni bir konuşmanın (ve muhtemelen yeni bir modelin) sıfırdan
başlarken ihtiyacı olan her şeyi tutar. Son güncelleme: **2026-08-16**,
Faz 4 bittikten hemen sonra.

Okuma sırası:

1. **Bu dosya** (durum + kurallar + Faz 5 kapsamı)
2. `ORCHESTRATOR_SPEC.md` §5.5 (local model önerisi) ve §9 (faz tanımları) —
   projenin asıl şartnamesi, Türkçe
3. `DECISIONS.md` — "neden böyle yapılmış" arşivi. Baştan sona okumana gerek
   yok; bir karar tuhaf geldiğinde başlığını orada ara. Faz 4'ten kalanlar:
   "Gemini'nin iki modeli", "Sınıflandırıcı kural tabanlı, LLM değil".
4. `README.md` — kurulum, çalıştırma, güvenlik modeli özeti (İngilizce)

---

## 1. Sistem şu an ne durumda

**Faz 1, 2, 3 ve 4 bitti ve dördü de canlı sistemde doğrulandı.** 247 test
geçiyor (`uv run pytest`). Henüz **hiç git commit atılmadı** — kullanıcı
istemedi.

| Faz | Ne var |
|---|---|
| 1 | LiteLLM proxy (:4000) + FastAPI (:8080) + SQLite + streaming sohbet |
| 2 | Araçlar (`read_file`, `list_dir`, `search_files`, `run_shell`), güvenlik politikası, sandbox, onay diyalogları, dry-run, audit log |
| 3 | Üç sağlayıcı, kota takibi, 429 → cooldown → sessiz sağlayıcı devri, kota paneli |
| 4 | Kural tabanlı router: `classifier.py` + `routing.yaml` görev tipi blokları + `gemini_lite` (flash-lite) |

### Çalışan sağlayıcılar (hepsi ücretsiz, hepsi canlı denendi)

| Sağlayıcı (etiket) | Model | Limit | Kaynak |
|---|---|---|---|
| `groq` | `llama-3.3-70b-versatile` | 30 RPM / 12K TPM / 1000 RPD | doküman |
| `openrouter` | `openai/gpt-oss-20b:free` | 20 RPM / 50 RPD | doküman + canlı probe |
| `gemini` | `gemini-3.5-flash` | 5 RPM / 250K TPM / **20 RPD** | kullanıcının AI Studio paneli |
| `gemini_lite` | `gemini-3.5-flash-lite` | 15 RPM / 250K TPM / **500 RPD** | kullanıcının AI Studio paneli |

`gemini_lite` gerçek bir sağlayıcı değil — aynı `GEMINI_API_KEY`'i kullanan,
sadece kota muhasebesini ayırmak için var olan bir etiket (bkz. `settings.py`
`PROVIDER_KEY_ALIASES`, DECISIONS.md "Gemini'nin iki modeli").

### Görev tipi → zincir (`config/routing.yaml`, `desktop` profili)

| `task_type` | Zincir |
|---|---|
| `trivial` | gemini_lite → groq |
| `tool_use` | groq → openrouter |
| `reasoning` | openrouter → gemini |
| `long_context` | gemini → openrouter |
| `code` | openrouter → groq |
| `vision` | gemini (tek aday — bkz. aşağısı) |
| (eşleşmezse) `default` | groq → openrouter → gemini |

`classifier.py` hiçbir zaman `tool_use` üretmez — bu tip sadece `loop.py`'de
ajan döngüsünün 2. ve sonraki adımlarında (tool sonucu değerlendirme)
kullanılır. İlk adım her zaman `classify_cached()`'in sonucunu kullanır.

### Servisleri başlatma

```bash
cd /home/mrlemon/Projects/ULL-Bot
uv run litellm --config config/litellm.desktop.yaml --port 4000   # 1. terminal
uv run uvicorn app.main:app --port 8080                           # 2. terminal
```

Ayakta mı: `curl localhost:8080/api/config` ve `curl localhost:4000/health/readiness`.
`--reload` yok, o yüzden **kod değiştirince uvicorn'u yeniden başlat**.
Config dosyaları (`quotas.yaml`, `routing.yaml`, `litellm.desktop.yaml`) da
açılışta okunuyor — onları değiştirdiğinde **her iki servisi de** yeniden
başlat (litellm config'i litellm'e, routing/quota Python tarafına ait ama
ikisi de açılışta önbelleğe alınıyor).

Port 8080/4000 "address already in use" derse eski bir süreç hayatta kalmıştır;
`ss -ltnp | grep -E "4000|8080"` ile PID'i bul ve `kill` at (gerekirse `-9`).
Bu birkaç kez oldu.

Tarayıcı açmadan sohbeti sürmek için (WebSocket istemcisi):
`/tmp/claude-1000/.../scratchpad/ws_chat.py` — yoksa 20 satırlık bir
`websockets` istemcisi yazman yeterli, `/ws/chat`'e
`{"type":"user_message","content":"..."}` gönderiyor. `<<classification>>`
olayı hangi `task_type`in seçildiğini, `<<model_switch>>` hangi
sağlayıcının seçildiğini gösteriyor.

---

## 2. Faz 5 kapsamı — Local model (spec §5.5)

Amaç: `trivial` (ve mümkünse sınıflandırmanın kendisi) internet olmadan da
çalışsın; RTX 5060 (8 GB VRAM) üzerinde Ollama ile.

### Zemin zaten hazır (bunları yeniden yazma)

- `routing.yaml`'daki her `task_type` bloğu bir sıralı liste — `local`/`ollama`
  sağlayıcısını en başa eklemek, `selector.choose()`'a hiç dokunmadan çalışır
  (spec §5.4 örneği zaten `local`'i ilk sırada gösteriyor).
- `settings.api_key_for()` `ollama`/`local` için özel durum zaten var:
  anahtar sorulmadan `"local"` döner (spec: yerel model API anahtarı
  istemiyor).
- `quotas.yaml`'da `ollama:` bloğu zaten duruyor (`probe: none, limits: []`)
  — VRAM sınırı kota sistemi dışında ele alınacak.

Yani Faz 5 = **Ollama entegrasyonu** (muhtemelen LiteLLM'in `ollama/` provider
desteği üzerinden, `litellm.desktop.yaml`'a `chat-local` gibi bir model_name
eklenerek — spec §1 "orchestrator sağlayıcıdan habersiz" ilkesini bozmadan) +
**`discover_models.py`** (VRAM'e göre model önerisi) + **`routing.yaml`'a
local'i `trivial` ve `default` zincirlerinin başına eklemek** + **sınıflandırıcının
local'e taşınması** (spec §9 Faz 5 madde 2 — şu an `classifier.py` zaten
LLM'e hiç gitmiyor, bu adımın ne anlama geldiğine dikkatle karar ver, bkz.
aşağısı).

### Dikkat: "sınıflandırıcı local'e taşınsın" maddesi

Spec §9 Faz 5'in ikinci maddesi bu. Faz 4'te sınıflandırıcı **hiç LLM
çağırmıyor** (bkz. DECISIONS.md "Sınıflandırıcı kural tabanlı, LLM değil") —
kural tabanlı yol spec'in kabul kriterini zaten karşılıyor ve token
harcamıyor. Local model gelince bunu değiştirip değiştirmemek bir tasarım
kararı: seçenekler (a) kural tabanlı kalsın, sadece kural karar veremediği
azınlık durumda local'e bir sınıflandırma sorusu eklensin (spec §5.2'nin
orijinal iki aşamalı tasarımı, artık kota maliyeti yok çünkü local ücretsiz/
VRAM sınırlı), (b) hiç değişmesin, "local'e taşınsın" maddesi zaten
gereksizleşmiş sayılsın. Kararı ver, gerekçesiyle DECISIONS.md'ye yaz — spec'i
"unuttum" diye kör kör uygulama.

### Local model önerisi (spec §5.5, hatırlatma)

8 GB VRAM: sınıflandırıcı/trivial için 3B–4B Q4_K_M, genel iş için 7B–8B
Q4_K_M. Aynı anda iki model yükleme; `keep_alive` kısa tut. `discover_models.py`
`nvidia-smi` ile VRAM okuyup öneri versin.

---

## 3. Bozulmaması gereken kurallar

Bunlar spec'ten ve önceki fazların kararlarından geliyor. Faz 5 sırasında
"temizlik" diye bunları bozma:

1. **Model isimlerini ve kota sayılarını uydurma** (spec §12). Bilmiyorsan
   config'de `null` bırak. Dahası: bir model adının dokümanda "aktif"
   görünmesi yetmez, `models.list`'te görünmesi de yetmez — **canlı çağırıp
   dene**. Faz 3'te iki Gemini modeli, Faz 4'te `gemini-3.5-flash-lite`
   (`generateContent` ile HTTP 200) tam bu yüzden önce canlı denendi.
2. **Güvenlik katmanını kısayol geçme.** Bu sistem kullanıcının ana Arch
   kurulumunda çalışıyor ve shell erişimi var.
3. **Blocked komut listesi kullanıcı/UI tarafından düzenlenemez** (spec §7.3).
   Kodda yaşar (`app/safety/policy.py`), config'de değil.
4. **Audit log ajana asla okunabilir/yazılabilir olmamalı.** Data dizini
   deny listesinde, dosya izni 0600.
5. **LiteLLM'in kendi fallback'i kapalı kalmalı** (`router_settings.num_retries: 0`).
   Açılırsa 429 orchestrator'a hiç ulaşmaz, kota takibi kör olur.
6. **`fastapi==0.136.3` pini** `litellm[proxy]` uyumluluğu için — gereksiz yere
   kaldırma.
7. **Commit/push sadece kullanıcı açıkça isterse.** Şu ana kadar hiç commit yok.
8. **`force_first` fallback'i, kullanıcının elle kapattığı sağlayıcıyı
   zorlamaz.** Kota tahmini bizim, kapatma kararı kullanıcının.
9. **Görev tipi seçimi kota/cooldown elemesinin üstüne değil altına eklenir**
   (spec §9 Faz 4 kabul notu, Faz 5'te de geçerli: local eklenirken de aynı
   `evaluate()`/`choose()` yolundan geçmeli, ayrı bir kısayol açılmamalı).
10. **`gemini_lite` gerçek bir sağlayıcı değil, `provider` alanı LiteLLM'e
    hiç gitmiyor** — bunu "temizlik" diye tek bir `gemini` sağlayıcısına geri
    birleştirme, kota muhasebesi bu ayrımla çalışıyor (bkz. DECISIONS.md).

## 4. Bilinçli olarak YAPILMAYANLAR

"Unutulmuş" sanıp Faz 5'e sıkıştırma; her birinin `DECISIONS.md`'de gerekçesi
var ve kendi fazları belli:

- Çöp kutusu / silmeden önce otomatik yedek → yazma araçlarıyla birlikte
  (`write_file`, `edit_file`, `delete_file`)
- Ajanı ayrı bir sistem kullanıcısı altında çalıştırma → Faz 7 (systemd)
- Windows kabuk politikası → `run_shell` Windows'ta bilerek kapalı
- Profil ayrımı desktop/laptop → Faz 6 (YAML'da yer var, mantık yok)
- Görsel ek desteği (`vision` task_type'ının ikinci adayı, gerçek `has_image`
  girişi) → henüz UI/WS protokolünde ek yükleme yok, `classifier.py`
  `has_image` parametresi hep `False` çağrılıyor. Kendi fazı belli değil,
  ama Faz 5'in kapsamı değil.

## 5. Bilinen, düzeltilmemiş gerçekler

Bunlar bizim hatamız değil ve sistem doğru davranıyor — Faz 5'te "bug buldum"
diye peşine düşme:

- **Groq ara sıra `tool_use_failed` döndürüyor** (LiteLLM 500'e çeviriyor).
  Sistem sağlayıcıyı o tur için eleyip devam ediyor.
- **LiteLLM, Groq'un `x-ratelimit-*` header'larını istemciye geçirmiyor**
  (1.97.0'da denendi, `return_response_headers` de işe yaramadı). Groq kotası
  yerel sayaçtan hesaplanıyor, panelde `tahmini` yazıyor.
- **OpenRouter'ın ücretsiz modelleri sık 429/502 veriyor.** Zaten Faz 3'ün
  çözdüğü şey.
- **Gemini'nin tool-call ID'leri devasa** (`__thought__` + base64 imza).
  Çalışıyor, sadece log'da çirkin.
- **`classifier.py`'nin "fiil içeriyor mu" kontrolü tam değil.** Kısa
  (<80 karakter) ama teknik bir istek, fiil ekini heuristik kaçırırsa
  yanlışlıkla `trivial`e düşüp ucuz bir modele gidebilir (canlı testte
  gözlendi). Sohbeti kesmiyor, sadece cevap kalitesini düşürebiliyor —
  bilinçli bir kabul (bkz. DECISIONS.md "Sınıflandırıcı kural tabanlı, LLM
  değil").

## 6. Son kabul testi nasıl göründü

Faz 4'ün kabul kriteri kurgusuz, WebSocket istemcisiyle doğrulandı:

```
"merhaba"                              → trivial (%90) → gemini_lite cevapladı
20K+ karakter metin                    → long_context   → gemini denendi,
                                                            openrouter cooldown'da
                                                            (kota/cooldown süzgeci
                                                             görev tipinin altında
                                                             çalıştığı doğrulandı)
uzun kod/hata ayıklama isteği          → code            → openrouter cevapladı
"ULL-Bot klasöründeki dosyaları        → reasoning (kural karar veremedi)
 listele"                                adım 1: gemini (reasoning zinciri)
                                          adım 2-3: groq (tool_use zinciri —
                                          ara adımlarda otomatik geçiş)
```

Faz 5'ten sonra da bu davranış korunmalı: **local eklenince zincirlerin
başına girer, ama seçim mantığı (kota/cooldown/health) hiç değişmez.**
