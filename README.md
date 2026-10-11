# ULL-Bot

Lokal yapay zekâ modelleriyle (Ollama) konuşmak için terminal havasında,
köşeli bir sohbet arayüzü. Arkada Japonca karakterlerle Matrix yağmuru akar.

- **Sohbet** — cevaplar Ollama'dan canlı akar. Model düşünürken üç nokta yerine
  `■ Cooking… ░▒▓█▓▒░░ 3s` gibi bir gösterge çıkar.
- **Kişilikler (master prompt)** — Ayarlar'da `+` ile istediğin kadar kişilik
  ekle. Seçili kişiliğin promptu her konuşmanın başında modele sistem mesajı
  olarak verilir.
- **Kişilik seçici** — sohbet kutusunun sol üstündeki `◆ İSİM ▾` düğmesi.

## Kurulum

1. [Ollama](https://ollama.com/download)'yı kur ve modeli indir:

   ```bash
   ollama pull qwen2.5:7b
   ```

2. [uv](https://docs.astral.sh/uv/)'yi kur, sonra proje klasöründe:

   ```bash
   uv sync
   uv run ull-bot
   ```

   Tarayıcı kendiliğinden `http://127.0.0.1:8765` adresini açar.

## Ayarlar

Model adı arayüzden (Ayarlar → Model) değiştirilebilir. Geri kalanı ortam
değişkenleriyle:

| Değişken | Varsayılan | Açıklama |
| --- | --- | --- |
| `OLLAMA_URL` | `http://127.0.0.1:11434` | Ollama adresi |
| `ULL_DATA_DIR` | `./data` | Kişiliklerin ve ayarların tutulduğu klasör (`state.json`) |
| `ULL_HOST` | `127.0.0.1` | Sunucunun dinlediği adres |
| `ULL_PORT` | `8765` | Port |
| `ULL_NO_BROWSER` | — | `1` ise tarayıcı otomatik açılmaz |

## Geliştirme

```bash
uv run pytest
```

```
app/main.py    FastAPI sunucusu: arayüzü sunar, sohbeti Ollama'ya aktarır
app/store.py   kişilik + ayar deposu (JSON)
web/           arayüz (index.html, style.css, app.js, rain.js)
tests/         API testleri (Ollama taklit edilir)
```
