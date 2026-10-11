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
- **Model seçici** — sağ üstteki model adına tıkla.
- **Model önerisi** — Ayarlar → Modeller → *Bilgisayarı tara*: RAM'e, ekran
  kartına ve VRAM'e bakıp hangi modelin rahat çalışacağını ve ne işe
  yaradığını söyler; tek tıkla indirir.
- **Görünüm** — yağmuru aç/kapa, hız, yoğunluk, karakter boyu, kırmızı oranı;
  yazı boyutu, kalın yazı, terminal yazı tipi, vurgu rengi, panel opaklığı,
  sohbet genişliği.
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

## Ortam değişkenleri

| Değişken | Varsayılan | Açıklama |
| --- | --- | --- |
| `OLLAMA_URL` | `http://127.0.0.1:11434` | Ollama adresi |
| `ULL_DATA_DIR` | `./data` | Sohbetler, projeler, kişilikler ve ayarlar (`ull.db`) |
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
web/             arayüz (index.html, style.css, js/*.js modülleri)
tests/           API testleri (Ollama taklit edilir)
```
