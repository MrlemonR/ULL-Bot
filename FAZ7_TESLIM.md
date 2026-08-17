# Faz 7 teslim notu — UI'ı kuracak konuşma için

Bu dosya **UI hariç her şeyin bittiği** noktada yazıldı (2026-08-16). Amacı
`NEXT_PHASE.md`den farklı: o "bir sonraki faza nasıl devam edilir" diye bir
devir notu, bu ise **backend'in tüm dış yüzeyini** — WebSocket protokolü ve
REST uçları — tek bir dosyada toplayan bir referans. UI'ı kuracak kişi/model
(kullanıcı bunu Opus 5 ile ayrı bir konuşmada yapacak) kod tabanını baştan
okumadan bu dosyayla başlayabilmeli.

Kısaca: **backend bitti, UI hiç yok.** `web/index.html` şu an minimal, elle
yazılmış bir sayfa (Faz 1'den kalma) — kullanıcı "web arayüzü olmadan bir
uygulama olarak çalışması gerek" dedi, yani muhtemelen bu HTML sayfası
tamamen değişecek ya da yerini masaüstü/native bir uygulamaya bırakacak. Bu
dosyadaki protokol her iki durumda da geçerli: WebSocket ve REST API,
tarayıcıdan da native bir uygulamadan da aynı şekilde konuşulur.

---

## 1. Mimari — tek cümlede

İki süreç: **LiteLLM proxy** (`:4000`, sağlayıcılara giden tek kapı) ve
**FastAPI orchestrator** (`:8080`, iş mantığı — router, güvenlik, kota, hafıza).
Aralarında bir de **Ollama** var (`:11434`, sadece FastAPI'nin bilgisi
dahilinde, LiteLLM üzerinden). UI, `:8080`'e konuşur — LiteLLM'i hiç görmez.

```
Tarayıcı/App ──WebSocket /ws/chat──► FastAPI :8080 ──HTTP──► LiteLLM :4000 ──► Groq/OpenRouter/Gemini/Ollama
             ──REST /api/*────────►
```

Servisleri başlatma (detay: README "Running it" ve "Deployment"):

```bash
uv run litellm --config config/litellm.desktop.yaml --port 4000
uv run uvicorn app.main:app --port 8080
# ya da: systemctl --user start ull-bot.target (Faz 7'de kuruldu)
```

`GET http://localhost:8080/` şu an `web/index.html`'i döndürüyor — bunu yeni
UI ile değiştireceksin. FastAPI tarafında değişmesi gereken tek şey muhtemelen
`app/main.py`'deki `@app.get("/")` route'u (yeni bir statik dosya sunacaksa)
ya da hiçbir şey (UI ayrı bir süreç/uygulama olacaksa, sadece `:8080`'e
bağlanacaksa).

---

## 2. WebSocket — `/ws/chat`

Sohbetin tamamı burada. Tek soket, çift yönlü, JSON mesajlar. Bağlantı
açıldığında hiçbir şey göndermene gerek yok — ilk mesajı sen atarsın.

### İstemciden sunucuya

**Mesaj gönder:**
```json
{"type": "user_message", "content": "merhaba", "session_id": "isteğe bağlı"}
```
`session_id` verilmezse sunucu yeni bir tane üretir ve `session` olayıyla
geri gönderir — ilk mesajda göndermezsen, gelen `session_id`yi sonraki
mesajlarda kullanarak aynı konuşmaya devam edersin. **Aynı soket üzerinde bir
tur bitmeden ikinci bir `user_message` göndermeye çalışırsan `error` alırsın**
("Önceki istek hâlâ çalışıyor") — UI bunu, tur bitene kadar gönder kutusunu
kilitleyerek önlemeli.

**Onay/devam cevabı:**
```json
{"type": "approval_response", "id": "<request'teki id>", "approved": true}
{"type": "continue_response", "id": "<request'teki id>", "approved": true}
```
İkisi de aynı şekilde işleniyor sunucu tarafında (aynı `pending` future'ı
çözüyorlar) — ayrı tiplerde tutulmalarının sebebi UI'ın iki farklı diyalog
göstermesi (biri "bu komutu çalıştırayım mı", diğeri "adım limitine ulaştım,
devam edeyim mi").

### Sunucudan istemciye

Her olay bir `type` alanı taşır. Sırayla, bir turun akışı:

| `type` | Ne zaman | Alanlar |
|---|---|---|
| `session` | Her `user_message`den hemen sonra, bir kere | `session_id` |
| `classification` | Sınıflandırma bitince, ilk model çağrısından önce | `task_type`, `confidence` (0-1), `reason` |
| `step` | Her ajan döngüsü adımının başında | `step` (1'den başlar) |
| `model_switch` | Sağlayıcı değiştiğinde (ilk seçimde de bir kere gelir) | `provider`, `model`, `previous`, `reason`, `forced` (bool), `rejected` (liste: `{provider, reason}`), `explanation` (hepsinin okunabilir özeti) |
| `token` | Model metin üretirken, akış hâlinde | `content` (bir sonraki parça, biriktir) |
| `tool_call` | Model bir araç çağırdığında | `id`, `name`, `args`, `risk` (`safe`/`confirm`/`blocked`), `reason` |
| `approval_request` | `risk: confirm` ise, çalışmadan önce | `id`, `tool`, `args`, `risk`, `reason`, `summary`, `paths` (liste), `detail`, `dry_run` (bool) |
| `approval_timeout` | `approval_request`e `APPROVAL_TIMEOUT_SECONDS` (varsayılan 300 sn) içinde cevap gelmezse | `id`, `message` — bu durumda istek REDDEDİLMİŞ sayılır, ayrıca `approval_response` göndermene gerek yok |
| `tool_result` | Araç çalıştıktan (ya da reddedildikten) sonra | `id`, `name`, `ok` (bool), `output`, `ms`, `risk` |
| `continue_request` | Adım limiti (`MAX_AGENT_STEPS`, varsayılan 15) dolunca | `id`, `steps`, `limit`, `summary` |
| `stopped` | Kullanıcı `continue_request`e hayır dediğinde | `message` |
| `done` | Model tool çağırmadan düz metin döndürünce, tur biter | `steps`, `model`, `provider`, `tokens`, `ms` |
| `error` | Model/sağlayıcı hatası, ya da protokol hatası (bilinmeyen mesaj tipi, meşgul soket) | `message` |

**Önemli akış detayları:**

- Bir tur `session` ile başlar, ya `done` ya `stopped` ya da `error` ile
  biter — UI "tur bitti, girdi kutusunu aç" kararını bu üç tipten birine
  bakarak versin.
- `token` olayları birikerek asistanın cevabını oluşturur — `done`
  geldiğinde ayrıca "final metni" veren bir alan YOK, UI zaten `token`larla
  biriktirdiğini gösteriyor olmalı.
- `tool_call` → (varsa `approval_request`/`approval_timeout`) → `tool_result`
  üçlüsü, BİR araç çağrısı için. Model tek adımda birden fazla araç
  çağırabilir — o zaman bu üçlü art arda birkaç kez tekrarlanır, hepsi aynı
  `step` numarası altında.
- `model_switch`in `rejected` listesi "neden bu model?" sorusunun cevabı
  (spec §5.3) — UI bunu bir tooltip/rozet açıklaması olarak gösterebilir.
  Aynı sağlayıcı arka arkaya adımlarda değişmiyorsa `model_switch` tekrar
  gelmez (gereksiz gürültü yapılmıyor).
- `dry_run: true` olan bir `approval_request`i onaylarsan araç yine de
  ÇALIŞMAZ, sadece "ne yapacaktım" raporu döner (`tool_result.output` içinde
  `[dry-run]` etiketiyle) — UI bunu ayırt edip göstermeli, yoksa kullanıcı
  bir şeyin gerçekten olduğunu sanabilir.

### Örnek bir turun tam akışı (bu dosya yazılırken canlı yakalandı, 2026-08-16)

```
> {"type":"user_message","content":"ULL-Bot klasöründeki dosyaları listele"}

< {"type":"session","session_id":"25a544a9-..."}
< {"type":"classification","task_type":"reasoning","confidence":0.4,"reason":"kural karar veremedi, güvenli varsayılana düşüldü"}
< {"type":"step","step":1}
< {"type":"model_switch","provider":"openrouter","model":"chat-openrouter","previous":"","reason":"kota uygun","forced":false,"rejected":[],"explanation":"openrouter seçildi (kota uygun)"}
< {"type":"model_switch","provider":"gemini","model":"chat-gemini","previous":"openrouter","reason":"sıradaki uygun sağlayıcı","forced":false,"rejected":[{"provider":"openrouter","reason":"cooldown (59 sn kaldı, sebep: 429)"}],"explanation":"..."}
  # openrouter seçildi ama İLK isteğin kendisi 429 döndürdü (cooldown'a
  # yeni girdi), o yüzden aynı step içinde ikinci bir model_switch geldi —
  # openrouter için hiç tool_call/token olayı YOK, hiç içerik üretmedi.
< {"type":"tool_call","id":"call_...","name":"list_dir","args":{"path":"/home/mrlemon/Projects"},"risk":"safe","reason":"Yol çalışma alanı içinde."}
< {"type":"tool_result","id":"call_...","name":"list_dir","ok":true,"output":"...","ms":0,"risk":"safe"}
< {"type":"step","step":2}
< {"type":"model_switch","provider":"groq","model":"chat-groq","previous":"gemini","reason":"kota uygun","forced":false,"rejected":[],"explanation":"groq seçildi (kota uygun)"}
< {"type":"token","content":"ULL-Bot klasöründeki dosyaları listelemek için ..."}
  # ... birkaç token daha, groq içerik ÜRETMEYE BAŞLADI ...
< {"type":"model_switch","provider":"ollama","model":"chat-local","previous":"groq","reason":"sıradaki uygun sağlayıcı","forced":false,"rejected":[{"provider":"groq","reason":"bu turda zaten denendi"},{"provider":"openrouter","reason":"cooldown (55 sn kaldı, sebep: 429)"}],"explanation":"..."}
  # groq YAYINA BAŞLADIKTAN SONRA başarısız oldu (bilinen tool_use_failed
  # gerçeği, bkz. NEXT_PHASE.md §5) — az önce gelen token'lar YARIM KALDI.
< {"type":"token","content":"Klasöre bakacağım, ve burada buldum:\n\n- LemonRice\n..."}
  # ollama SIFIRDAN yeni bir cevap üretiyor, groq'un yarım kalan metniyle
  # hiç ilgisi yok.
< {"type":"done","steps":2,"model":"chat-local","provider":"ollama","tokens":1394,"ms":2297}
```

**Bu örnekten çıkan gerçek bir davranış kuralı (bir sonraki bölüme de
eklendi):** `token` olayları biriktirilirken, aynı adımda araya bir
`model_switch` girerse UI o adımın metin arabelleğini SIFIRLAMALI. Yoksa
groq'un yarım kalan "ULL-Bot klasöründeki dosyaları listelemek için..."
cümlesiyle ollama'nın tamamen farklı cevabı yan yana/birleşik görünür —
kullanıcı tutarsız, yarım bir metin okur. Sunucu tarafında bunu ayıran bir
alan yok (`token` olayının kendisi hangi sağlayıcıdan geldiğini söylemiyor),
o yüzden UI bunu `model_switch` olayının kendisini bir "arabelleği sıfırla"
sinyali olarak kullanarak çözmeli.

---

## 3. REST uçları

Hepsi `http://localhost:8080` altında. Hiçbiri auth istemiyor (spec bunu
tek kullanıcılı, kendi makinesinde çalışan bir araç olarak tasarladı).

### Yapılandırma ve durum

```
GET /api/config
→ {"profile": "desktop", "model": "chat-default", "dry_run": true,
   "workspace_root": "/home/x/Projects", "max_agent_steps": 15}
```

```
GET /api/quota?probe=false
→ {
    "providers": [
      {
        "provider": "groq", "model": "chat-groq", "available": true,
        "reason": null, "health": "ok", "note": null,
        "cooldown_seconds": 0, "last_probe": null,
        "probe_kind": "response_headers", "reset_policy": "rolling",
        "windows": [
          {"window": "minute", "requests": 3, "tokens": 900,
           "max_requests": 30, "max_tokens": 12000,
           "remaining_requests": 27, "remaining_tokens": 11100,
           "free_ratio": 0.9, "known": true, "source": "local",
           "resets_at": "2026-08-16T21:05:00+00:00"},
          {"window": "day", ...}
        ],
        "configured": true
      },
      ...
    ],
    "profile": "desktop", "reserve_ratio": 0.1, "fallback_behaviour": "force_first"
  }
```
`?probe=true` sağlayıcılardan canlı veri çeker (OpenRouter key endpoint'i) —
her açılışta otomatik çağırma, kullanıcı "canlı sorgula" dediğinde çağır.

**BİLİNEN BOŞLUK:** Bu uç `describe_chain()`i `task_type` VERMEDEN çağırıyor,
yani `default` zincirini okuyor. `gemini_lite` ve `ollama` `default`
zincirinde YOK (bkz. `config/routing.yaml`), o yüzden bu listede hiç
görünmüyorlar — panel şu an sadece groq/openrouter/gemini'yi gösteriyor.
UI'da tüm sağlayıcıları göstermek istiyorsan ya bu uca `task_type` parametresi
eklemen (birden fazla zincirden birleştirip tekilleştirerek) ya da backend'i
değiştirmen gerekecek — şu an bu, Faz 7'nin çözmediği bilinen bir gerçek
(bkz. DECISIONS.md → "Faz 6 tamamlandı" notundaki liste).

```
POST /api/quota/{provider}/disable
POST /api/quota/{provider}/enable
→ {"ok": true, "provider": "groq", "health": "down" | "ok"}
```
Manuel sağlayıcı kapatma/açma düğmesi. `provider` değerleri:
`groq`, `openrouter`, `gemini`, `gemini_lite`, `ollama`.

### Oturum geçmişi ve arama (Faz 7)

```
GET /api/sessions?limit=50
→ {"sessions": [
    {"id": "...", "created_at": "2026-08-16 20:22:51",
     "title": "benim adımın Limon olduğunu hatırla...",
     "message_count": 3, "last_message_at": "2026-08-16 20:22:54"},
    ...
  ]}
```
`title` `NULL`sa ilk kullanıcı mesajının ilk 60 karakteri kullanılıyor
(sorguda hesaplanıyor, DB'ye yazılmıyor). En yeni oturum en üstte
(`last_message_at`e göre, o da yoksa `created_at`e göre).

```
GET /api/sessions/{session_id}/messages
→ {"session_id": "...", "messages": [
    {"id": 1, "role": "user", "content": "merhaba", "tool_name": null,
     "model": null, "ts": "..."},
    {"id": 2, "role": "assistant", "content": "Merhaba!", "tool_name": null,
     "model": "chat-groq", "ts": "..."},
    ...
  ]}
```
`role`: `user` | `assistant` | `tool`. Bu, modele geri verilen (tool
mesajları hariç tutulan) özet DEĞİL — tam kayıt, geçmiş görüntüleme için.

```
GET /api/search?q=pdf&limit=50
→ {"query": "pdf", "results": [
    {"session_id": "...", "message_id": 42, "role": "user",
     "content": "...", "ts": "...", "session_title": ""},
    ...
  ]}
```
Düz `LIKE` araması (FTS5 değil — bkz. DECISIONS.md). `q` boşsa boş liste
döner, hata vermez.

### Kullanım grafiği (Faz 7)

```
GET /api/usage/graph?days=14
→ {"days": 14, "points": [
    {"day": "2026-08-16", "provider": "groq", "requests": 18,
     "tokens": 20045, "rate_limited": 0, "errors": 7},
    {"day": "2026-08-16", "provider": "ollama", "requests": 18,
     "tokens": 20778, "rate_limited": 0, "errors": 0},
    ...
  ]}
```
Gün × sağlayıcı kırılımında `usage_events` toplamı. Grafiği çizmek UI'ın işi
— bu sadece veri.

### Kalıcı hafıza (Faz 7)

```
GET /api/memory
→ {"notes": [{"key": "preferred_shell", "value": "fish",
              "updated_at": "2026-08-16 ..."}]}

DELETE /api/memory/{key}
→ {"ok": true, "key": "preferred_shell"}   # ya da ok:false, key yoksa
```
Yazma yolu yok — notlar sadece `remember` aracıyla (modelin kendisi,
sohbet sırasında) yazılıyor. UI sadece görüntüleyip silebilir.

---

## 4. UI'ın bilmesi gereken davranışsal kurallar

Bunlar kod değil, ürün davranışı — UI bunları yanlış yaparsa kullanıcı
deneyimi spec'in istediğinden farklı olur:

1. **Onaylar bloklayıcı.** `approval_request` gelince ajan bekler — UI
   diyalogu ne kadar geç gösterirse/kapatırsa o kadar geç devam eder.
   300 saniye (varsayılan) içinde cevap yoksa otomatik reddedilir
   (`approval_timeout`).
2. **`risk: "blocked"` için hiç `approval_request` gelmez.** Doğrudan
   `tool_result` ile `ok: false` gelir, çıktıda "REDDEDİLDİ" yazar. UI bunu
   bir hata gibi değil, "politika engelledi" gibi göstermeli (kullanıcının
   onaylayabileceği bir şey değil).
3. **`dry_run: true` her yerde görünür olmalı.** `/api/config`te global
   bayrak, her `approval_request`te de tekrar var (o anki oturumun context'i
   için — ikisi teorik olarak farklı olabilir, testlerde `dry_run` override
   edilebiliyor). UI muhtemelen sürekli görünen bir "KURU ÇALIŞMA" rozeti
   istiyor.
4. **`model_switch` sessiz bir hata değil, bilgi.** Spec'in "sessizce geç,
   ama UI'da sebebi görünsün" ilkesi (Faz 3 kabul kriteri) — kullanıcıya
   `error` gösterme, ama `model_switch.explanation`ı bir yerde (rozet,
   tooltip, log paneli) göster.
5. **Aynı anda tek tur.** Soket meşgulken ikinci `user_message` `error`
   döner — UI gönder düğmesini `done`/`stopped`/`error` gelene kadar
   kilitlemeli.
6. **`classification` bilgilendirici, aksiyon gerektirmiyor** ama "neden bu
   model?" sorusunun ilk yarısı (görev tipi) burada, ikinci yarısı
   (`model_switch.explanation`) orada — ikisini birlikte gösterirsen tam
   resim çıkar.
7. **Aynı adımda `model_switch` ile karşılaşırsan o adımın `token`
   arabelleğini sıfırla.** Bir sağlayıcı yayına başlayıp yarıda başarısız
   olabiliyor (yukarıdaki örnekte groq tam bunu yaptı) — o sağlayıcının
   ürettiği kısmi metin sunucu tarafında zaten atılıyor (mesaj geçmişine hiç
   yazılmıyor), UI da aynısını yapmalı, yoksa yarım kalan bir cümleyle yeni
   cevap yan yana görünür.

---

## 5. Ne YOK (UI tasarlarken varsaymayın)

- **Dosya yazma/silme aracı yok.** `write_file`/`edit_file`/`delete_file`
  hiç yazılmadı — sadece `run_shell` (onaylanmış komutlarla) dosya
  değiştirebiliyor. UI'da "dosyayı düzenle" gibi bir özellik varsa, bu
  arkada bir `run_shell` çağrısı (örn. bir editör açmak ya da `sed`) olarak
  modele bırakılıyor demektir, ayrı bir tool değil.
- **Çöp kutusu/geri alma yok.** Onaylanan bir `rm` gerçekten siler.
- **Görsel ek desteği yok.** `classifier.py`'nin `has_image` parametresi hep
  `False` — mesaja resim ekleme UI'da olsa bile backend'de bunu taşıyan bir
  alan yok (`user_message`'ın `content`i düz string). Eklemek istersen hem
  WS mesaj şemasına hem `classify_cached()` çağrısına yeni bir alan lazım.
- **`/api/quota` tüm sağlayıcıları göstermiyor** (yukarıda anlatıldı).
- **Ayrı bir "recall" ucu/aracı yok** — hafıza sistem promptuna gömülü,
  UI'ın `/api/memory`den okuduğu liste zaten modelin de gördüğü liste.

---

## 6. Daha fazla bağlam

- `README.md` — kurulum, güvenlik modeli, router, local model, profiller.
- `DECISIONS.md` — her kararın "neden" arşivi, başlıklarla arayabilirsin.
- `NEXT_PHASE.md` — backend'in "sıradaki adım"ı (spec numaralı fazları
  bitti, ama çöp kutusu/write tool'ları gibi spec'te numaralı faza hiç
  atanmamış işler var).
- `ORCHESTRATOR_SPEC.md` §7 — spec'in UI için yazdığı orijinal bölüm
  (sohbet ekranı, kota paneli, ayarlar) — bağlayıcı değil, sadece ilk niyeti
  gösteriyor.
