# Devir notu — spec'in numaralı fazları bitti, sırada UI var

Bu dosya yeni bir konuşmanın (ve muhtemelen yeni bir modelin) sıfırdan
başlarken ihtiyacı olan her şeyi tutar. Son güncelleme: **2026-08-16**,
Faz 7 (backend kısmı) bittikten hemen sonra.

**Eğer bu konuşma UI kurmak için başladıysa** (kullanıcı Opus 5 ile ayrı bir
konuşmada yapacağını söylemişti): bu dosyayı değil, doğrudan
**[`FAZ7_TESLIM.md`](./FAZ7_TESLIM.md)**'i oku — WebSocket protokolü ve REST
uçlarının tam referansı orada. Bu dosya (`NEXT_PHASE.md`) backend'in
sıradaki adımları için.

Okuma sırası (backend'e devam edecekse):

1. **Bu dosya** (durum + kurallar + sıradaki iş)
2. **[`FAZ7_TESLIM.md`](./FAZ7_TESLIM.md)** — API yüzeyinin tam referansı,
   backend'e dokunacaksan da faydalı (hangi olay/uç neyi bekliyor).
3. `DECISIONS.md` — "neden böyle yapılmış" arşivi. Faz 7'den kalanlar:
   "`remember` aracı: yaz var, ayrı bir recall aracı yok", "Arama: `LIKE`,
   FTS5 değil", "systemd target'ın `Wants=`'ı gerekiyordu", "`install.sh`
   hiçbir şeyi enable/start etmiyor".
4. `README.md` — kurulum, çalıştırma, güvenlik modeli, router, local model,
   profiller, hafıza/geçmiş/kullanım, deployment.

---

## 1. Sistem şu an ne durumda

**Spec'in Faz 1-7'sinin hepsi bitti — UI HARİÇ.** Kullanıcının kararı: UI'ı
ayrı bir konuşmada, Opus 5 ile yapacak; bu fazda "UI hariç her şeyi yap"
istendi. 277 test geçiyor (`uv run pytest`). Henüz **hiç git commit
atılmadı** — kullanıcı istemedi.

| Faz | Ne var |
|---|---|
| 1 | LiteLLM proxy (:4000) + FastAPI (:8080) + SQLite + streaming sohbet |
| 2 | Araçlar, güvenlik politikası, sandbox, onay diyalogları, dry-run, audit log |
| 3 | Üç sağlayıcı, kota takibi, 429 → cooldown → sessiz sağlayıcı devri, kota paneli |
| 4 | Kural tabanlı router: `classifier.py` + `routing.yaml` görev tipi blokları + `gemini_lite` |
| 5 | Local model: Ollama (`chat-local`), `ENABLE_LOCAL`, `trivial`/`tool_use` zincirlerinde |
| 6 | `PROFILE=desktop\|laptop`, `config/litellm.laptop.yaml`, laptop'ta local statik dışlı |
| 7 | Kalıcı hafıza (`remember`), oturum geçmişi+arama, kullanım grafiği verisi, systemd `--user`, `install.sh` — **UI hariç** |

`web/index.html` hâlâ Faz 1'den kalma minimal bir sayfa — kullanıcı yeni
UI'ın "web arayüzü olmadan bir uygulama olarak çalışması" gerektiğini
söyledi, yani muhtemelen bu tamamen değişecek/yerini başka bir şeye
bırakacak. Backend tarafı bundan bağımsız, `FAZ7_TESLIM.md`deki protokolle
konuşulduğu sürece UI'ın tarayıcı mı native bir uygulama mı olduğu
backend'i ilgilendirmiyor.

### Servisleri başlatma

```bash
cd /home/mrlemon/Projects/ULL-Bot
uv run litellm --config config/litellm.desktop.yaml --port 4000   # 1. terminal
uv run uvicorn app.main:app --port 8080                           # 2. terminal
```

**Ya da Faz 7'den beri systemd `--user` ile:**

```bash
./scripts/install.sh                        # bir kere, .env + birimleri kurar
systemctl --user enable --now ull-bot.target
```

Bu konuşmanın sonunda servisler **systemd üzerinden `active (running)`**
bırakıldı (`start` edildi, `enable` EDİLMEDİ — yani şu an çalışıyorlar ama
oturum açılışında otomatik başlamayacaklar; kalıcı otomatik başlatma
kullanıcının kararı). Durum: `systemctl --user status ull-bot.target`.

Ayakta mı: `curl localhost:8080/api/config`, `curl localhost:4000/health/readiness`,
`curl localhost:11434/api/tags` (Ollama, ayrı bir sistem servisi, bununla
ilgisi yok). Kod değiştirince (`--reload` yok): manuel çalıştırıyorsan
uvicorn'u yeniden başlat; systemd ile çalışıyorsa `systemctl --user restart
ull-bot-api.service` (litellm config değiştiyse `ull-bot-litellm.service`i
de). Config dosyaları (`quotas.yaml`, `routing.yaml`, `litellm.*.yaml`)
açılışta okunuyor.

Tarayıcı açmadan sohbeti sürmek için: `/tmp/claude-1000/.../scratchpad/ws_chat.py`
(yoksa `FAZ7_TESLIM.md`deki protokolle 20 satırlık bir `websockets`
istemcisi yaz).

---

## 2. Sıradaki iş — spec'te numaralı bir faz DEĞİL

Spec'in 7 fazı bitti. Kullanıcıyla bu konuşmada geçen, henüz ele alınmamış
iki gerçek konu:

### a) UI (kullanıcı bunu ayrı yapacak)

Bkz. `FAZ7_TESLIM.md`. Bu konuşmanın kapsamı değildi, bilerek dışarıda
bırakıldı.

### b) Çöp kutusu / `write_file` / `edit_file` / `delete_file`

Kullanıcı bunu **bu faz bittikten sonra** ele almak istediğini söyledi
(spec'te de zaten hiçbir numaralı faza atanmamıştı — bkz. Faz 6 devir
notundaki "asıl soru" bölümü, DECISIONS.md). Şu an gerçek yazma/silme
SADECE onaylanan `run_shell` komutlarıyla oluyor, geri alma yok. Bu
konuşulmadan "unutulmuş" sanıp otomatik eklenmemeli — kapsamını (hangi
araçlar, çöp kutusu 30 gün mü tutuyor spec'in dediği gibi, silme onayı nasıl
görünüyor) kullanıcı belirlemeli.

---

## 3. Bozulmaması gereken kurallar

1. **Model isimlerini ve kota sayılarını uydurma** (spec §12). Canlı çağırıp
   dene.
2. **Güvenlik katmanını kısayol geçme.** Shell erişimi var.
3. **Blocked komut listesi kullanıcı/UI tarafından düzenlenemez** (spec §7.3).
4. **Audit log ajana asla okunabilir/yazılabilir olmamalı.** 0600.
5. **LiteLLM'in kendi fallback'i kapalı kalmalı** (`num_retries: 0`).
6. **`fastapi==0.136.3` pini** — kaldırma.
7. **Commit/push sadece kullanıcı açıkça isterse.**
8. **`force_first`, kullanıcının elle kapattığı sağlayıcıyı zorlamaz.**
9. **Görev tipi seçimi kota/cooldown elemesinin üstüne değil altına eklenir.**
10. **`gemini_lite`/`ollama` gerçek birer sağlayıcı değil.**
11. **Laptop'ta local dışlaması statik (`routing.yaml`), bir bayrak değil.**
12. **`litellm.desktop.yaml` ve `litellm.laptop.yaml` aynı modelleri
    tanımlamalı** (`tests/test_config_files.py` bunu test ediyor).
13. **`remember`in ayrı bir "recall" aracı yok — notlar sistem promptuna
    gömülü** (`app/agent/prompts.py` → `_memory_section()`). UI ya da yeni
    bir araç eklerken bunu iki kere yapma (hem prompt'a göm hem ayrı bir
    "hafızayı oku" tool'u ekleme — model zaten görüyor).
14. **`install.sh` servisleri enable/start etmiyor, kasıtlı** (yukarıda
    "Servisleri başlatma"). UI kurulum akışına bunu otomatikleştiren bir
    adım eklerken kullanıcıya sor, sessizce yapma.

## 4. Bilinçli olarak YAPILMAYANLAR

- **Çöp kutusu / write tool'ları** → yukarıda "sıradaki iş b" — kullanıcı
  bilerek bu fazdan sonraya bıraktı.
- **UI** → kullanıcı bilerek bu fazdan çıkardı, Opus 5 ile ayrı yapacak.
- **Ajanı ayrı bir sistem kullanıcısı altında çalıştırma** → hiçbir fazda
  yok, systemd `--user` birimi (Faz 7) bunun YERİNE geçmiyor.
- **Windows kabuk politikası** → `run_shell` Windows'ta bilerek kapalı.
- **Görsel ek desteği** → `classifier.py`nin `has_image`i hep `False`,
  WS mesaj şemasında görsel alanı yok.
- **Sınıflandırıcının LLM'e (local dahil) taşınması** → Faz 5'te bilinçli
  ertelendi.
- **LAN üzerinden masaüstü Ollama'sı** → kod hazır, açık değil, test edilmedi.
- **`/api/quota`nın tüm sağlayıcıları (gemini_lite, ollama dahil) göstermesi**
  → bilinen boşluk, Faz 4'ten beri var, kimse kapatmadı (aşağısı).
- **Tam metin arama (FTS5)** → `LIKE` yeterli görüldü (bkz. DECISIONS.md).

## 5. Bilinen, düzeltilmemiş gerçekler

- **Groq ara sıra `tool_use_failed` döndürüyor.**
- **LiteLLM, Groq'un `x-ratelimit-*` header'larını istemciye geçirmiyor.**
- **OpenRouter'ın ücretsiz modelleri sık 429/502 veriyor.**
- **Gemini'nin tool-call ID'leri devasa.**
- **`classifier.py`'nin "fiil içeriyor mu" kontrolü tam değil.**
- **`chat-local`in ilk isteği yavaş** (~2-5 sn model yükleme).
- **`GET /api/quota` `gemini_lite`/`ollama`yı hiç göstermiyor** —
  `describe_chain()` task_type'sız (yani `default`) çağrılıyor.
- **LAN üzerinden Ollama erişimi test edilmedi.**
- **Bir sağlayıcı yayına başlayıp (bazı `token` olayları gönderip) sonra
  aynı adımda başarısız olabiliyor** (canlı gözlendi: groq metne başladı,
  `tool_use_failed`e benzer bir hatayla düştü, ollama sıfırdan yeni bir
  cevap üretti). Sunucu tarafında zararsız (yarım içerik hiçbir yere
  yazılmıyor) ama İSTEMCİ o token'ları zaten almış oluyor —
  `FAZ7_TESLIM.md` §4 madde 7'de UI için nasıl ele alınacağı yazılı
  (aynı adımda gelen bir `model_switch`, token arabelleğini sıfırlama
  sinyali sayılmalı).

## 6. Son kabul testi nasıl göründü

Faz 7'nin backend kısmı canlı doğrulandı:

```
remember aracı: "adımı Limon olarak hatırla" → qwen2.5:3b-instruct (local)
  remember(key="username", value="Limon") çağırdı → kaydedildi
  YENİ bir oturumda "adım ne?" → openrouter → "Adın Limon." (hafıza kalıcı)

GET /api/sessions, /api/sessions/{id}/messages, /api/search?q=Limon,
GET /api/usage/graph, GET /api/memory, DELETE /api/memory/{key}
  hepsi gerçek veriyle denendi, doğru döndüler

systemd: ./scripts/install.sh çalıştırıldı, birimler ~/.config/systemd/user/e
  yazıldı, systemd-analyze --user verify hatasız, `start ull-bot.target`
  ikisini de `active (running)` yaptı, gerçek bir chat isteği cevap verdi
```

Sıradaki adım (UI ya da çöp kutusu, hangisi önce ele alınırsa) hangisi
olursa olsun bu davranış korunmalı: **backend'in API yüzeyi (`FAZ7_TESLIM.md`)
UI kurulana kadar sabit kalmalı** — değiştirmek gerekirse önce o dosya
güncellenmeli, UI onu okuyacak.
