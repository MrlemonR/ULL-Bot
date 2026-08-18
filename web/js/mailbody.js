/* HTML mail gövdesini güvenle göster.
 *
 * Maillerin neredeyse tamamı HTML (bu kutuda 200'de 194). Sadece düz metni
 * göstermek pazarlama maillerini okunamaz hâle getiriyordu: tablo düzeni
 * kayboluyor, resimler hiç çıkmıyor, üstelik çoğu gönderici düz metin
 * alternatifine "e-posta istemciniz HTML gösteremiyor" diye bir uyarı
 * koyuyor — kullanıcı haklı olarak uygulamayı bozuk sanıyor.
 *
 * Ama mail HTML'i düşman girdidir. İki katmanlı savunma:
 *
 * 1. **Temizleme (bu dosya).** `<script>`, `<iframe>`, `<object>`, olay
 *    öznitelikleri (`onclick`…) ve `javascript:` adresleri ayrıştırma
 *    aşamasında siliniyor. Regex ile DEĞİL, tarayıcının kendi
 *    ayrıştırıcısıyla (`DOMParser`) — regex tabanlı HTML temizleyiciler
 *    kaçırmakla ünlü.
 * 2. **Kum havuzu.** Gövde `sandbox` nitelikli bir `<iframe srcdoc>` içinde
 *    çiziliyor. `allow-scripts` VERİLMİYOR, yani içeride hiçbir betik
 *    çalışamaz. `allow-same-origin` veriliyor ki ana sayfa iframe'in içine
 *    erişip bağlantı tıklamalarını yakalayabilsin — betik çalışamadığı için
 *    bu kombinasyon güvenli.
 *
 * **Uzak resimler varsayılan olarak ENGELLİ.** Pazarlama maillerindeki
 * resimler çoğu zaman takip pikselidir: yüklenmesi göndericiye "bu kişi
 * maili açtı, şu saatte, şu IP'den" bilgisini verir. Kullanıcı isterse
 * tek mail için açabiliyor.
 */

const BLOCKED_TAGS = new Set([
  "SCRIPT", "IFRAME", "OBJECT", "EMBED", "APPLET", "LINK", "META",
  "BASE", "FORM", "INPUT", "BUTTON", "SELECT", "TEXTAREA", "NOSCRIPT",
]);

// `javascript:` ve `data:` (text/html) adresleri bağlantı olarak kabul edilmez.
const SAFE_URL = /^(https?:|mailto:|tel:|cid:|#)/i;

/**
 * Mail HTML'ini temizle.
 * @param {string} html ham gövde
 * @param {{allowRemoteImages?: boolean}} options
 * @returns {{html: string, blockedImages: number}}
 */
export function sanitizeMailHtml(html, { allowRemoteImages = false } = {}) {
  const doc = new DOMParser().parseFromString(String(html || ""), "text/html");
  let blockedImages = 0;

  // Tehlikeli düğümleri kökten kaldır.
  for (const node of [...doc.querySelectorAll("*")]) {
    if (BLOCKED_TAGS.has(node.tagName)) {
      node.remove();
      continue;
    }

    for (const attr of [...node.attributes]) {
      const name = attr.name.toLowerCase();
      const value = attr.value || "";

      // Olay işleyicileri (onclick, onerror, onload…)
      if (name.startsWith("on")) {
        node.removeAttribute(attr.name);
        continue;
      }
      // `javascript:` vb.
      if ((name === "href" || name === "src" || name === "action") && !SAFE_URL.test(value.trim())) {
        node.removeAttribute(attr.name);
        continue;
      }
      // `style` içindeki uzak kaynaklar (url(...) ile takip pikseli)
      if (name === "style" && !allowRemoteImages && /url\s*\(/i.test(value)) {
        node.setAttribute("style", value.replace(/url\s*\([^)]*\)/gi, "none"));
      }
    }

    // Uzak resimler: varsayılan engelli.
    if (node.tagName === "IMG") {
      const src = node.getAttribute("src") || "";
      const isRemote = /^https?:/i.test(src);
      if (isRemote && !allowRemoteImages) {
        blockedImages += 1;
        // Saydam 1px: `src`i tamamen silmek tarayıcıyı "bozuk resim"
        // moduna sokuyor ve alt metni tam boyutta basıyor — pazarlama
        // maillerinde bu, sayfayı devasa "engellenen resim" yazılarıyla
        // dolduruyordu. Saydam bir yer tutucu düzeni bozmuyor.
        node.setAttribute(
          "src",
          "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"
        );
        node.removeAttribute("srcset");
        node.removeAttribute("alt");
        node.setAttribute("data-blocked", "1");
        node.setAttribute("title", "Uzak resim engellendi");
      }
      node.removeAttribute("loading");
    }

    // Tüm bağlantılar yeni pencerede — ana sayfa yakalayıp sistem
    // tarayıcısına yönlendiriyor (bkz. `mountMailBody`).
    if (node.tagName === "A") {
      node.setAttribute("target", "_blank");
      node.setAttribute("rel", "noopener noreferrer");
    }
  }

  return { html: doc.body ? doc.body.innerHTML : "", blockedImages };
}

/** Iframe içine konacak tam belge — CSP ve okunur bir tipografi ile. */
function buildDocument(bodyHtml, { allowRemoteImages }) {
  // CSP ikinci savunma hattı: temizleme bir şey kaçırsa bile ağ çıkışı yok.
  const imgSrc = allowRemoteImages ? "https: data: cid:" : "data: cid:";
  const csp = [
    "default-src 'none'",
    `img-src ${imgSrc}`,
    "style-src 'unsafe-inline'",
    "font-src data:",
  ].join("; ");

  return `<!doctype html><html><head>
<meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="${csp}">
<style>
  /* Mail gövdesi BEYAZ zeminde çizilir — uygulamanın geri kalanı koyu olsa da.
   *
   * Sebep: gönderenlerin neredeyse tamamı maili beyaz zemin varsayarak
   * tasarlıyor ve renkleri satır içi stillerle dayatıyor. Koyu bir zemine
   * koyduğumuzda Google'ın "Güvenlik uyarısı" maili koyu gri metni koyu
   * zeminde bastı ve HİÇ OKUNMADI. "color-scheme: dark" da bunu düzeltmiyor,
   * çünkü satır içi "color:#202124" gibi değerler her şeyi eziyor.
   *
   * Thunderbird, Gmail ve Apple Mail de aynısını yapıyor: mail, gönderenin
   * tasarladığı gibi görünsün diye kendi beyaz "kağıdında" duruyor. */
  :root { color-scheme: light; }
  html { background: transparent; }
  body {
    margin: 0;
    background: #ffffff;
    color: #202124;
    font: 13.5px/1.65 "Inter", system-ui, -apple-system, "Segoe UI", sans-serif;
    word-break: break-word; overflow-wrap: anywhere;
    padding: 16px 18px;
    /* Pazarlama mailleri sabit genişlikli tablolarla (600-800px) geliyor
     * ve dar bir panelde yatay kaydırma çubuğu açıyorlar. Dış panel zaten
     * dikey kayıyor; bir de yatay çubuk olması okumayı bozuyor. */
    overflow-x: hidden;
  }
  a { color: #1a56db; }
  img { max-width: 100% !important; height: auto !important; }
  /* Engellenen resim: saydam yer tutucu + ince kesikli çerçeve.
   * Yazı basmıyoruz; 30 tanesi yan yana geldiğinde sayfayı doldururdu. */
  img[data-blocked] {
    min-width: 14px; min-height: 14px;
    border: 1px dashed #c3c8d0; border-radius: 3px;
    background: #f1f3f5;
  }
  blockquote {
    margin: 8px 0; padding-left: 12px;
    border-left: 2px solid #dadce0; color: #5f6368;
  }
  /* Sabit genişlik dayatan her şeyi panele sığdır. */
  table, td, th, div, p, blockquote, pre {
    max-width: 100% !important;
  }
  table { border-collapse: collapse; table-layout: auto !important; }
  [width] { max-width: 100% !important; }
  /* Not: uzak arka plan resimlerini ayrıca kısıtlamaya gerek yok —
   * CSP'deki "img-src" zaten ağ çıkışını kapatıyor ve "style" içindeki
   * "url(...)" temizlemede siliniyor. Gönderenin tasarımına dokunmuyoruz. */
  pre { white-space: pre-wrap; }
</style></head><body>${bodyHtml}</body></html>`;
}

/**
 * Mail gövdesini bir kaba yerleştir.
 *
 * @param {HTMLElement} container
 * @param {{body_html?: string, body_text?: string}} message
 * @param {{onOpenLink: (url: string) => void}} handlers
 */
export function mountMailBody(container, message, { onOpenLink }) {
  const html = (message.body_html || "").trim();
  const text = (message.body_text || "").trim();

  // HTML yoksa düz metin — eskisi gibi.
  if (!html) {
    container.className = "mail-body mail-body-text";
    container.textContent = text || "(metin gövdesi yok)";
    return;
  }

  let allowRemote = false;
  container.className = "mail-body";
  container.innerHTML = "";

  const bar = document.createElement("div");
  bar.className = "mail-imgbar";
  const frame = document.createElement("iframe");
  frame.className = "mail-frame";
  // `allow-scripts` YOK: içeride hiçbir betik çalışamaz.
  // `allow-same-origin` VAR: ana sayfa bağlantı tıklamalarını yakalayabilsin.
  frame.setAttribute("sandbox", "allow-same-origin");
  frame.setAttribute("title", "Mail içeriği");

  container.append(bar, frame);

  const render = () => {
    const { html: clean, blockedImages } = sanitizeMailHtml(html, {
      allowRemoteImages: allowRemote,
    });
    frame.srcdoc = buildDocument(clean, { allowRemoteImages: allowRemote });

    bar.innerHTML = "";
    if (blockedImages > 0 && !allowRemote) {
      const note = document.createElement("span");
      note.textContent =
        `${blockedImages} uzak resim engellendi — göndericiye maili açtığını bildirebilirler.`;
      const button = document.createElement("button");
      button.className = "btn btn-ghost btn-sm";
      button.textContent = "Resimleri göster";
      button.addEventListener("click", () => {
        allowRemote = true;
        render();
      });
      bar.append(note, button);
      bar.hidden = false;
    } else {
      bar.hidden = true;
    }
  };

  // Yüksekliği içeriğe göre ayarla ki dış panel doğal biçimde kaysın —
  // iframe'in kendi kaydırma çubuğu olursa fare tekerleği orada takılıyor.
  frame.addEventListener("load", () => {
    try {
      const doc = frame.contentDocument;
      if (!doc) return;
      const resize = () => {
        const height = Math.max(
          doc.body?.scrollHeight || 0,
          doc.documentElement?.scrollHeight || 0
        );
        frame.style.height = `${height + 16}px`;
      };
      resize();
      // Resimler geç yüklenince yükseklik değişir.
      doc.querySelectorAll("img").forEach((img) => {
        img.addEventListener("load", resize);
        img.addEventListener("error", resize);
      });
      setTimeout(resize, 350);

      // Bağlantılar: iframe içinde gezinme yok, sistem tarayıcısına gitsin.
      doc.addEventListener("click", (event) => {
        const anchor = event.target.closest?.("a[href]");
        if (!anchor) return;
        event.preventDefault();
        const href = anchor.getAttribute("href") || "";
        if (/^https?:/i.test(href)) onOpenLink(href);
      });
    } catch {
      /* erişilemezse sadece yükseklik ayarlanamaz, içerik yine görünür */
    }
  });

  render();
}
