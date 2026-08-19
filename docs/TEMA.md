# Kendi temanı yaz

Arayüzün tamamı CSS değişkenleriyle çiziliyor. Kendi temanı yazmak için
uygulamanın dosyalarına **dokunmana gerek yok** — ayrı bir dosyaya yazıyorsun
ve uygulama onu en son yüklüyor, yani senin kuralların her zaman kazanıyor.

```
~/.config/ull-bot/theme.css
```

Dosya yoksa hiçbir şey olmuyor (uygulama boş CSS alıyor). Başka bir yol
kullanmak istersen `.env` içinde:

```
USER_THEME=/bir/yer/tema.css
```

Değişiklikten sonra pencereyi kapatıp açman yeterli.

**Neden ayrı dosya:** `web/style.css` uygulamayla birlikte güncelleniyor.
Temanı oraya yazarsan bir sonraki güncellemede kaybolur.

## En kısa yol: renkleri değiştir

Tek blokla bütün arayüzün rengi değişir:

```css
:root {
  --bg-0: #000000;   /* en arka zemin */
  --bg-1: #0a0a0a;   /* paneller */
  --bg-2: #111111;   /* kartlar */
  --bg-3: #1a1a1a;   /* rozetler, girdiler */

  --line: #1f1f1f;
  --line-soft: #161616;
  --line-strong: #2a2a2a;

  --text:   #e6e6e6;
  --text-2: #b0b0b0;
  --text-3: #7a7a7a;

  --accent: #00ff9c;      /* vurgu — düğmeler, aktif sekme */
  --accent-2: #00c97b;
  --accent-soft: #00ff9c1f;
  --accent-line: #00ff9c44;

  --ok: #00ff9c;
  --warn: #ffcc00;
  --danger: #ff5555;
  --info: #00bcd4;
}
```

## Köşeler

Varsayılan **kare**. Yuvarlatmak istersen:

```css
:root { --r-sm: 4px; --r: 8px; --r-lg: 12px; }
```

## Yazı tipleri

```css
:root {
  --font: "IBM Plex Sans", system-ui, sans-serif;
  --mono: "JetBrains Mono", ui-monospace, monospace;
}
```

Arayüz etiketleri (rozetler, sekmeler, sayaçlar, bölüm başlıkları) `--mono`
kullanıyor; mail ve sohbet metni `--font`. Her şeyi terminal yapmak istersen:

```css
body, button, input, textarea { font-family: var(--mono); }
```

## Faydalı seçiciler

| Ne | Seçici |
|---|---|
| Sol şerit | `.rail`, `.rail-btn`, `.rail-btn.is-active` |
| Üst bar | `.topbar`, `.topbar-title h1`, `.chip` |
| Sohbet balonları | `.msg-user`, `.msg-bot .body` |
| Araç kartı | `.tool`, `.tool-head`, `.tool-body` |
| Mail üst barı | `.mail-topbar`, `.tab`, `.tab.is-active` |
| Mail listesi | `.mail-item`, `.mail-item.is-unread`, `.mail-item.is-active` |
| Mail kategorisi | `.cat`, `.cat-kod`, `.cat-genel`, `.cat-reklam`… |
| Toplu işlem şeridi | `.mail-bulk`, `.bulk-count` |
| Tablolar | `.md-table`, `.md-table th` |
| Kod kartı | `.code-card`, `.code-value` |

## Örnek: yeşil fosfor terminal

`config/themes/phosphor.css` dosyasını kopyala:

```bash
mkdir -p ~/.config/ull-bot
cp config/themes/phosphor.css ~/.config/ull-bot/theme.css
```

## Dikkat

- Mail gövdesi ayrı bir kum havuzunda (iframe) çiziliyor ve **teman oraya
  geçmez**. Bu bilinçli: mailler gönderenin tasarladığı gibi, beyaz kâğıt
  üzerinde görünüyor (bkz. `web/js/mailbody.js`).
- `[hidden] { display: none !important }` kuralını ezme — ezersen onay
  diyaloğunun kaplaması ekranda takılı kalır.
