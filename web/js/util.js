/* Ortak yardımcılar: kaçış, biçimlendirme, toast, modal.
 *
 * `escapeHtml` bu dosyanın en önemli fonksiyonu: mail gövdeleri, araç
 * çıktıları ve model cevapları hep dışarıdan gelen metin. Hiçbiri innerHTML'e
 * kaçırılmadan girmemeli — mail içeriğinin bir <script> ya da <img onerror>
 * taşıması tamamen mümkün.
 */

export function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

export function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

export function qs(selector, root = document) {
  return root.querySelector(selector);
}

/* --- markdown (küçük ve güvenli alt küme) --------------------------------
 * Önce HTML'i kaçırıyoruz, SONRA kendi etiketlerimizi ekliyoruz. Ters sıra
 * yapılsaydı model çıktısındaki bir `<img onerror=...>` çalışırdı.
 */
/* Blok yer tutucusu.
 *
 * Kod blokları ve tablolar, satır içi biçimlendirmeden korunmak için önce
 * metinden çıkarılıp yerlerine bir işaret bırakılıyor. Bu işaret ESKİDEN
 * ` BLOCK0 ` (boşluklu) idi ve iki yerde birden kırılıyordu: paragraf
 * kontrolü `trim()` edilmiş metne boşluklu kalıpla bakıyor, geri yazma da
 * boşluk arıyordu. Sonuç: fenced kod blokları ekranda "BLOCK0" olarak
 * çıkıyordu. Görünmez bir karakter kullanmak bu sınıfı tamamen kapatıyor —
 * `trim()` ona dokunmuyor ve kullanıcı metninde bulunması imkânsız.
 */
const MARK = "\u0000";
// Yer tutucu KENDİ paragrafında dursun: etrafına boş satır koyuyoruz.
// Yoksa tablodan hemen sonra gelen bir satır ("Kaynak: …") aynı parçaya
// düşüyor, parça "yalnızca yer tutucu" sayılmıyor ve tablo bir <p>
// içine sarılıyor — <div> bir <p> içinde geçersiz, tarayıcı paragrafı
// erken kapatıp düzeni bozuyor.
const placeholder = (index) => `\n\n${MARK}B${index}${MARK}\n\n`;
const PLACEHOLDER = new RegExp(`${MARK}B(\\d+)${MARK}`, "g");
const PLACEHOLDER_ONLY = new RegExp(`^${MARK}B\\d+${MARK}$`);

/* Markdown tablosu: en az iki satır, ikincisi `|---|---|` ayracı.
 * Karşılaştırma isteklerinin ("şu ürünleri karşılaştır") çıktısı bu —
 * tablo desteği olmadan model doğru cevabı verse bile ekranda boru
 * işaretleriyle dolu bir metin yığını görünüyordu. */
const TABLE_BLOCK = /(?:^\|.*\|[ \t]*\n)(?:^\|[ \t]*:?-+:?[ \t]*(?:\|[ \t]*:?-+:?[ \t]*)*\|[ \t]*\n)(?:^\|.*\|[ \t]*\n?)*/gm;

function splitRow(line) {
  return line
    .trim()
    .replace(/^\||\|$/g, "")
    .split("|")
    .map((cell) => cell.trim());
}

/** Satır içi biçimlendirme: kod, kalın, italik, resim, bağlantı, çıplak adres.
 *
 * Ayrı bir fonksiyon çünkü TABLO HÜCRELERİNE de uygulanması gerekiyor.
 * Eskiden değildi: tablolar satır içi kurallardan önce yer tutucuya
 * alınıyordu ve yer tutucu içeriği bir daha işlenmiyordu. Sonuç, kullanıcının
 * ekranında `**Razer BlackShark V2 Pro**` ve
 * `[İnceleme Videosu](https://www.youtube.com/watch?v=…)` diye ham metin
 * görünmesiydi — üstelik YouTube bağlantısı tıklanamıyordu.
 *
 * Girdi ZATEN kaçırılmış (`escapeHtml`) olmalı; burada yeni bir kaçış yok.
 */
function inline(text) {
  return text
    .replace(/`([^`\n]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[\s(])\*([^*\n]+)\*/g, "$1<em>$2</em>")
    // Resim: `![alt](url)`. Bağlantı kuralından ÖNCE çalışmalı, yoksa
    // parantez içindeki adres önce bağlantıya dönüşür.
    .replace(
      /!\[([^\]]*)\]\((https?:\/\/[^\s)]+)\)/g,
      (_, alt, url) =>
        `<a href="${url}" target="_blank" rel="noopener noreferrer">` +
        `<img class="md-img" src="${url}" alt="${alt}" loading="lazy"></a>`
    )
    // Bağlantı: `[metin](url)`
    .replace(
      /\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g,
      '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>'
    )
    // Çıplak adres. Yalnızca http(s) — `javascript:` şemasına kapı açmıyoruz.
    .replace(
      /(^|[\s(])(https?:\/\/[^\s<>"')]+)/g,
      (_, lead, url) =>
        `${lead}<a href="${url}" target="_blank" rel="noopener noreferrer">${url}</a>`
    );
}


function renderTable(block) {
  const lines = block.trim().split("\n").filter((line) => line.trim());
  if (lines.length < 2) return block;

  const header = splitRow(lines[0]);
  // İkinci satır hizalama tanımı: `:---`, `:--:`, `---:`
  const aligns = splitRow(lines[1]).map((spec) => {
    const left = spec.startsWith(":");
    const right = spec.endsWith(":");
    if (left && right) return "center";
    if (right) return "right";
    return "left";
  });
  const rows = lines.slice(2).map(splitRow);

  const cell = (value, index, tag) => {
    const align = aligns[index] || "left";
    const style = align === "left" ? "" : ` style="text-align:${align}"`;
    return `<${tag}${style}>${inline(value ?? "")}</${tag}>`;
  };

  const head = `<tr>${header.map((v, i) => cell(v, i, "th")).join("")}</tr>`;
  const body = rows
    .map((row) => `<tr>${header.map((_, i) => cell(row[i], i, "td")).join("")}</tr>`)
    .join("");

  // Dar panelde taşmasın diye kendi yatay kaydırma kabında.
  return `<div class="md-table-wrap"><table class="md-table"><thead>${head}</thead><tbody>${body}</tbody></table></div>`;
}

export function markdown(raw) {
  let text = escapeHtml(raw);
  const blocks = [];

  // ```kod``` bloklarını önce çıkar, içlerine biçimlendirme uygulanmasın.
  text = text.replace(/```(\w*)\n?([\s\S]*?)```/g, (_, lang, code) => {
    blocks.push(`<pre><code data-lang="${escapeHtml(lang)}">${code.replace(/\n$/, "")}</code></pre>`);
    return placeholder(blocks.length - 1);
  });

  // Tablolar kod bloklarından SONRA, satır içi biçimlendirmeden ÖNCE:
  // hücre içeriği yine biçimlendirmeden geçsin diye yer tutucuya alıyoruz.
  text = text.replace(TABLE_BLOCK, (match) => {
    blocks.push(renderTable(match));
    return placeholder(blocks.length - 1);
  });

  text = inline(text)
    .replace(/^###\s+(.+)$/gm, "<h4>$1</h4>")
    .replace(/^##\s+(.+)$/gm, "<h3>$1</h3>");

  // Listeler: ardışık madde satırlarını tek <ul>/<ol> içine topla.
  const lines = text.split("\n");
  const out = [];
  let list = null;
  for (const line of lines) {
    const bullet = line.match(/^\s*[-*•]\s+(.*)$/);
    const numbered = line.match(/^\s*\d+[.)]\s+(.*)$/);
    const kind = bullet ? "ul" : numbered ? "ol" : null;
    if (kind) {
      if (list !== kind) {
        if (list) out.push(`</${list}>`);
        out.push(`<${kind}>`);
        list = kind;
      }
      out.push(`<li>${(bullet || numbered)[1]}</li>`);
      continue;
    }
    if (list) {
      out.push(`</${list}>`);
      list = null;
    }
    out.push(line);
  }
  if (list) out.push(`</${list}>`);

  text = out
    .join("\n")
    .split(/\n{2,}/)
    .map((chunk) => {
      const trimmed = chunk.trim();
      if (!trimmed) return "";
      if (PLACEHOLDER_ONLY.test(trimmed) || /^<(h[34]|ul|ol|pre|div)/.test(trimmed)) return trimmed;
      return `<p>${trimmed.replace(/\n/g, "<br>")}</p>`;
    })
    .join("");

  return text.replace(PLACEHOLDER, (_, index) => blocks[Number(index)]);
}

/* --- tarih/saat ---------------------------------------------------------- */

const TR_MONTHS = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran",
  "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"];
const TR_DAYS = ["Paz", "Pzt", "Sal", "Çar", "Per", "Cum", "Cmt"];

export const MONTHS = TR_MONTHS;
export const DAYS_SHORT = ["Pzt", "Sal", "Çar", "Per", "Cum", "Cmt", "Paz"];

export function parseDate(value) {
  if (!value) return null;
  // SQLite'ın "2026-08-16 20:22:51" biçimi ISO değil; T ekleyip UTC sayıyoruz.
  const text = /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}/.test(value) ? value.replace(" ", "T") + "Z" : value;
  const date = new Date(text);
  return Number.isNaN(date.getTime()) ? null : date;
}

export function formatDate(value, { withTime = true } = {}) {
  const date = parseDate(value);
  if (!date) return "—";
  const day = `${date.getDate()} ${TR_MONTHS[date.getMonth()]} ${date.getFullYear()}`;
  if (!withTime) return day;
  return `${day}, ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

export function formatRelative(value) {
  const date = parseDate(value);
  if (!date) return "";
  const now = new Date();
  const diffMs = now - date;
  const sameDay = date.toDateString() === now.toDateString();
  if (sameDay) return `${pad(date.getHours())}:${pad(date.getMinutes())}`;

  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  if (date.toDateString() === yesterday.toDateString()) return "dün";

  if (diffMs < 7 * 864e5 && diffMs > 0) return TR_DAYS[date.getDay()];
  if (date.getFullYear() === now.getFullYear()) {
    return `${date.getDate()} ${TR_MONTHS[date.getMonth()].slice(0, 3)}`;
  }
  return `${pad(date.getDate())}.${pad(date.getMonth() + 1)}.${date.getFullYear()}`;
}

export function formatEventTime(startValue, endValue, allDay) {
  const start = parseDate(startValue);
  if (!start) return "—";
  if (allDay) return `${start.getDate()} ${TR_MONTHS[start.getMonth()]} · tüm gün`;
  const end = parseDate(endValue);
  const head = `${start.getDate()} ${TR_MONTHS[start.getMonth()]} ${pad(start.getHours())}:${pad(start.getMinutes())}`;
  if (!end) return head;
  const sameDay = start.toDateString() === end.toDateString();
  return sameDay ? `${head} – ${pad(end.getHours())}:${pad(end.getMinutes())}` : head;
}

export function pad(value) {
  return String(value).padStart(2, "0");
}

export function toLocalInput(date) {
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

export function dayKey(date) {
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

/* --- renk ---------------------------------------------------------------- */

const AVATAR_COLORS = ["#5b8cff", "#8b6bff", "#34c98a", "#eab24a", "#f4626f", "#4fb6e0", "#d16bff"];

export function colorFor(seed) {
  let hash = 0;
  for (let i = 0; i < String(seed).length; i += 1) {
    hash = (hash * 31 + String(seed).charCodeAt(i)) >>> 0;
  }
  return AVATAR_COLORS[hash % AVATAR_COLORS.length];
}

export function initials(name, address) {
  const source = (name || address || "?").trim();
  const parts = source.split(/[\s.@_-]+/).filter(Boolean);
  if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase();
  return source.slice(0, 2).toUpperCase();
}

/* --- toast --------------------------------------------------------------- */

export function toast(title, detail = "", kind = "") {
  const host = document.getElementById("toasts");
  if (!host) return;
  const node = el("div", `toast ${kind ? `is-${kind}` : ""}`);
  node.appendChild(el("b", "", title));
  if (detail) node.appendChild(el("span", "", detail));
  host.appendChild(node);
  setTimeout(() => {
    node.style.transition = "opacity .25s, transform .25s";
    node.style.opacity = "0";
    node.style.transform = "translateX(16px)";
    setTimeout(() => node.remove(), 260);
  }, kind === "err" ? 7000 : 3800);
}

/* --- modal --------------------------------------------------------------- */

let modalCloser = null;

export function openModal(html, { onClose } = {}) {
  const backdrop = document.getElementById("modal-backdrop");
  const modal = document.getElementById("modal");
  modal.innerHTML = html;
  backdrop.hidden = false;
  modalCloser = onClose || null;
  return modal;
}

export function closeModal() {
  const backdrop = document.getElementById("modal-backdrop");
  if (!backdrop || backdrop.hidden) return;
  backdrop.hidden = true;
  document.getElementById("modal").innerHTML = "";
  const closer = modalCloser;
  modalCloser = null;
  if (closer) closer();
}

export function isModalOpen() {
  const backdrop = document.getElementById("modal-backdrop");
  return backdrop && !backdrop.hidden;
}

export function emptyState(container, icon, title, text, actionHtml = "") {
  container.innerHTML = `
    <div class="empty">
      ${icon}
      <h3>${escapeHtml(title)}</h3>
      <p>${escapeHtml(text)}</p>
      ${actionHtml}
    </div>`;
}

/* --- dış bağlantılar ------------------------------------------------------
 * Uygulama native bir WebKit penceresi: içindeki `<a target="_blank">`
 * HİÇBİR ŞEY yapmaz (pywebview'in GTK arka ucunda yeni pencere politikası
 * yok). Google'ın uygulama parolası sayfası, OAuth onay ekranı ve
 * takvimdeki toplantı bağlantıları dışarıda açılmak zorunda, o yüzden
 * backend'in `xdg-open` çağıran ucundan geçiyoruz.
 */

export async function openExternal(url) {
  const { api } = await import("./api.js");
  try {
    await api.openExternal(url);
    return true;
  } catch (error) {
    toast("Tarayıcı açılamadı", `${error.message} — adres panoya kopyalandı.`, "err");
    try {
      await navigator.clipboard.writeText(url);
    } catch {
      /* pano da yoksa yapacak bir şey kalmıyor */
    }
    return false;
  }
}

/** Sayfadaki tüm dış bağlantıları yakala — tek tek `onclick` yazmayalım. */
export function interceptExternalLinks(root = document) {
  root.addEventListener("click", (event) => {
    const anchor = event.target.closest?.("a[href^='http']");
    if (!anchor) return;
    event.preventDefault();
    openExternal(anchor.href);
  });
}

export const ICONS = {
  mail: '<svg viewBox="0 0 24 24"><path d="M3 6h18v12H3z"/><path d="m3 7 9 6 9-6"/></svg>',
  calendar: '<svg viewBox="0 0 24 24"><rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4M16 3v4"/></svg>',
  chat: '<svg viewBox="0 0 24 24"><path d="M21 11.5a8.4 8.4 0 0 1-9 8.4 9 9 0 0 1-3.6-.7L3 21l1.9-5A8.4 8.4 0 0 1 12 3a8.4 8.4 0 0 1 9 8.5Z"/></svg>',
  search: '<svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>',
};
