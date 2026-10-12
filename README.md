# ULL-Bot

Lokal yapay zekâ modelleriyle (Ollama) konuşmak için terminal havasında,
köşeli, siyah-beyaz (+ biraz kırmızı) bir sohbet arayüzü. Arkada Japonca
karakterlerle Matrix yağmuru akar.

- **Sohbetler** — sol kenar çubuğunda; her şey kaydedilir. Ara (Ctrl+K),
  sabitle, yeniden adlandır, projeye taşı, sil.
- **Projeler** — sohbetleri klasörler gibi grupla. Her projenin bir
  **bağlam** metni var; projedeki her mesajda modele verilir.
- **Kişilikler (master prompt)** — Ayarlar → Kişilikler'de `+` ile istediğin
  kadar ekle; sohbet kutusunun sol üstündeki `◆ İSİM ▾` ile seç. Kişilik her
  mesajda modele verilir ve kısa bir hatırlatmayla konuşma boyunca korunur.
  Aynı menüdeki **Kişiliksiz** ile hiç sistem promptu gönderilmez; model kendi
  hâliyle konuşur.
- **Model seçici** — sağ üstteki model adına tıkla.
- **Model önerisi** — Ayarlar → Modeller → *Bilgisayarı tara*: RAM'e, ekran
  kartına ve VRAM'e bakıp hangi modelin rahat çalışacağını ve ne işe
  yaradığını söyler; tek tıkla indirir.
- **Görünüm** — yağmuru aç/kapa, hız, yoğunluk, karakter boyu, kırmızı oranı;
  yazı boyutu, kalın yazı, terminal yazı tipi, vurgu rengi, panel opaklığı,
  sohbet genişliği.
- **Ayar dosyaları (prefab)** — *Ayarlar → Görünüm*: **KAYDET** görünümü, model
  ayarlarını ve tüm kişilikleri `prefab/` klasörüne bir `.json` dosyası olarak
  yazar; **YÜKLE** o klasördeki dosyaları listeler, seçtiğini uygular.
- **Model ara & indir** — model adını yazmaya başla (`llam`, `qwen`, `kod`…):
  eşleşen modeller bu bilgisayarda nasıl çalışacakları ve ne işe yaradıklarıyla
  listelenir.
- **Hugging Face'ten ekle** — *Ayarlar → Modeller*: bir Hugging Face model
  bağlantısını yapıştır (ör. `https://huggingface.co/kullanıcı/model-GGUF`).
  Depodaki GGUF sürümleri (Q4_K_M, Q8_0…) boyutları ve bu bilgisayarda nasıl
  çalışacaklarıyla listelenir, en uygunu ★ ile önerilir. Kısa bir ad ver,
  **İNDİR VE EKLE**: Ollama `hf.co/kullanıcı/model:Q4_K_M` olarak indirir,
  ULL-Bot kısa adla kaydeder. Depoda GGUF yoksa aynı modelin GGUF sürümleri
  önerilir.
- **Code modu** — logonun yanındaki `</>` düğmesi: bir klasör seç, ne
  yapılacağını yaz; ULL-Bot dosyaları okur, düzenler (önce farkı gösterip izin
  ister), komut çalıştırır. İstersen gerçek **Claude Code**'u aynı klasörde
  yerel modelle açar. Ayrıntılar aşağıda.
- **İnce ayar (fine-tune)** — *Ayarlar → İnce ayar*: bir model seç
  (Llama 3.1 8B, Llama 3.2 1B/3B, Mistral 7B), soru-cevap JSON dosyasını ver,
  eğitimi başlat. Bitince Ollama'ya yeni bir model olarak eklenir ve seçilir.
  Ayrıntılar aşağıda.
- Model düşünürken üç nokta yerine `■ Cooking… ░▒▓█▓▒░░ 3s`.

## Kurulum

1. [Ollama](https://ollama.com/download)'yı kur ve bir model indir
   (ya da arayüzden *Ayarlar → Modeller*'den indir):

   ```bash
   ollama pull qwen2.5:7b
   ```

2. [uv](https://docs.astral.sh/uv/)'yi kur, sonra proje klasöründe:

   ```bash
   uv sync
   uv run ull-bot
   ```

   Tarayıcı kendiliğinden `http://127.0.0.1:8765` adresini açar.

## Code modu

Kenar çubuğunun üstündeki geçişle **Sohbet** ↔ **Code** arasında gidilir.

1. **Klasör seç** — *+ Yeni kod oturumu* ya da *KLASÖR SEÇ*. Oturumlar kenar
   çubuğunda klasöre göre gruplanır.
2. **Model** — sağ üstten bir kod modeli seç (Code modunun modeli sohbetten
   ayrı tutulur). Model **araç çağırmayı (tools)** desteklemeli:
   `qwen2.5-coder`, `qwen3`, `qwen3-coder`, `llama3.1`, `gpt-oss`…
3. **Yaz** — ajan şu araçlarla çalışır: dosya listele, oku, ara, düzenle, yaz,
   komut çalıştır. Hepsi seçilen klasöre kilitli; dışına çıkamaz.
4. **İzin modu** (yazma kutusunun sol üstü):
   - **SOR** — her düzenleme ve komuttan önce farkı gösterip sorar (1 evet,
     2 hep izin ver, 3 hayır),
   - **DÜZENLEMELER SERBEST** — dosya düzenlemeleri sorulmaz, komutlar sorulur,
   - **TAM YETKİ** — hiçbir şey sorulmaz.

**Claude Code ile aç** — gerçek Claude Code'u o klasörde, seçili yerel modelle
bir terminalde başlatır (Ollama 0.14+ Anthropic API uyumluluğu:
`ANTHROPIC_BASE_URL=<ollama adresi> claude --model <model>`). `claude`
komutu kurulu olmalı; değilse kurulum komutu ve elle çalıştırma komutu
gösterilir. Claude Code büyük bir bağlam ister: Ollama'nın bağlam uzunluğunu
32K–64K yap ve güçlü bir kod modeli seç.

## İnce ayar (fine-tune)

Ollama'nın model dosyaları (GGUF) doğrudan eğitilemiyor. ULL-Bot bu yüzden
modelin orijinal ağırlıklarını Hugging Face'ten indirir, üstüne bir **LoRA
adaptörü** eğitir ve adaptörü Ollama'daki aynı modelin üstüne takar:

```
FROM llama3.1:8b
ADAPTER ./adapter
```

1. **Eğitim ortamı** — *Ayarlar → İnce ayar → ORTAMI KUR*. Ayrı bir Python ortamı
   (`.train-venv`) kurulur: PyTorch (CUDA sürümü sürücüne göre seçilir),
   Unsloth ve PEFT. Birkaç GB indirir; bir kez yapılır.
2. **Veri seti** — `datasets/` klasörüne at ya da *DOSYA YÜKLE*. Biçim:

   ```json
   [
     {"soru": "Adın ne?", "cevap": "Ben ULL-Bot."},
     {"soru": "...", "cevap": "..."}
   ]
   ```

   `question/answer`, `instruction/input/output`, `prompt/completion`,
   `messages` (sohbet) ve JSONL de olur. Örnek: `datasets/ornek-ullbot.json`.
3. **Model ve ayarlar** — temel model, yeni modelin adı, isteğe bağlı sistem
   mesajı, tur sayısı, öğrenme hızı, LoRA rank, en fazla uzunluk.
4. **Eğitimi başlat** — ilerleme, loss, kalan süre ve günlük canlı görünür.
   Bitince `ollama create` ile yeni model oluşturulur ve ULL-Bot ona geçer.

Gereksinimler: **NVIDIA ekran kartı** (8B için ~8 GB VRAM, 4-bit QLoRA),
birkaç GB boş disk, `ollama` komutu PATH'te. GPU yoksa yalnızca 1B model CPU'da
(çok yavaş) denenebilir. Ollama adaptörleri Llama ve Mistral için destekliyor;
Qwen/Gemma şimdilik listede yok.

## Sorun giderme

- **Güncelledim ama eski hali açılıyor** — eski ULL-Bot penceresi hâlâ açık
  olabilir: kapat (Ctrl+C) ve `uv run ull-bot` ile yeniden başlat. Port
  doluysa ULL-Bot bunu söyleyip çıkar. Tarayıcıda bir kez Ctrl+F5 yap.
  Kenar çubuğunun altında sürüm yazar (ör. `ULL-Bot v0.7.0`).
- **İnce ayar: PyTorch ekran kartını görmüyor** (`+cpu` yazıyor, CUDA ✗) —
  *Ayarlar → İnce ayar → PYTORCH'U ONAR*: yalnızca PyTorch'u sürücüne uygun
  CUDA sürümüyle değiştirir. Olmazsa NVIDIA sürücüsünü güncelle.
- **Hugging Face modeli inmiyor** — Ollama yalnızca **GGUF** dosyalarını
  çekebilir; bağlantı `...-GGUF` gibi bir depoya gitmeli. Erişim izni isteyen
  (gated) depolarda Hugging Face'te koşulları kabul et ve Ollama'nın açık
  anahtarını (`~/.ollama/id_ed25519.pub`) HF hesabına ekle.
- **Ollama'ya bağlanamıyor** — başlarken terminalde `Ollama (...): ...`
  satırı sebebi yazar. Ollama açık olmalı (Windows'ta Ollama uygulaması ya da
  `ollama serve`). Farklı adres/port için: *Ayarlar → Modeller → Ollama
  bağlantısı* (TEST ET ile dene) ya da `OLLAMA_URL` / `OLLAMA_HOST`.

## Ortam değişkenleri

| Değişken | Varsayılan | Açıklama |
| --- | --- | --- |
| `OLLAMA_URL` | `http://127.0.0.1:11434` | Ollama adresi (ayarlardan da değiştirilebilir; yoksa `OLLAMA_HOST` okunur) |
| `ULL_DATA_DIR` | `./data` | Sohbetler, projeler, kişilikler ve ayarlar (`ull.db`) |
| `ULL_PREFAB_DIR` | `<proje>/prefab` | Ayar dosyalarının klasörü |
| `ULL_DATASET_DIR` | `<proje>/datasets` | İnce ayar veri setleri |
| `ULL_TRAIN_VENV` | `<proje>/.train-venv` | Eğitim için ayrı Python ortamı |
| `ULL_HOST` | `127.0.0.1` | Sunucunun dinlediği adres |
| `ULL_PORT` | `8765` | Port |
| `ULL_NO_BROWSER` | — | `1` ise tarayıcı otomatik açılmaz |

İlk sürümdeki `data/state.json` varsa kişilikler ilk açılışta otomatik içeri
alınır.

## Geliştirme

```bash
uv run pytest
```

```
app/main.py      FastAPI sunucusu: API, sohbet akışı, prompt kurulumu
app/db.py        SQLite deposu (ayarlar, kişilikler, projeler, sohbetler)
app/hardware.py  donanım taraması (CPU, RAM, GPU/VRAM — Windows/Linux/macOS)
app/catalog.py   önerilen modeller ve donanıma göre uygunluk
app/hf.py        Hugging Face bağlantısı → GGUF sürümleri, öneri
app/finetune.py  ince ayar: veri seti, eğitim ortamı, iş yönetimi, Ollama'ya ekleme
app/train_worker.py  eğitim betiği (.train-venv'de çalışır: Unsloth / PEFT LoRA)
app/coder.py     Code modu: araçlar, ajan döngüsü, izinler, Claude Code başlatıcı
web/             arayüz (index.html, style.css, js/*.js modülleri)
tests/           API testleri (Ollama taklit edilir)
```
