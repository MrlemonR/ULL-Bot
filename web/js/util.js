/* Küçük DOM ve metin yardımcıları. */

export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

export const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

export function el(tag, cls, html) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (html != null) n.innerHTML = html;
  return n;
}

export function debounce(fn, ms) {
  let t = 0;
  const d = (...args) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...args), ms);
  };
  d.flush = (...args) => { clearTimeout(t); fn(...args); };
  d.cancel = () => clearTimeout(t);
  return d;
}

export function fmtBytes(n) {
  if (!n) return "—";
  const gb = n / 1024 ** 3;
  if (gb >= 1) return `${gb.toFixed(1)} GB`;
  return n >= 1024 ** 2 ? `${Math.round(n / 1024 ** 2)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`;
}

export function hexToRgba(hex, a) {
  const m = /^#?([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(hex || "");
  if (!m) return `rgba(255, 42, 61, ${a})`;
  return `rgba(${parseInt(m[1], 16)}, ${parseInt(m[2], 16)}, ${parseInt(m[3], 16)}, ${a})`;
}

export function relTime(ts) {
  const s = Date.now() / 1000 - ts;
  if (s < 60) return "şimdi";
  if (s < 3600) return `${Math.floor(s / 60)} dk`;
  if (s < 86400) return `${Math.floor(s / 3600)} sa`;
  if (s < 86400 * 30) return `${Math.floor(s / 86400)} gün`;
  return new Date(ts * 1000).toLocaleDateString("tr-TR");
}

/* --- markdown (yalnızca kod blokları, satır içi kod, kalın, başlık) ---------- */

function inlineMd(escaped) {
  return escaped
    .replace(/`([^`\n]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>")
    .replace(/^#{1,6} (.+)$/gm, "<strong>$1</strong>");
}

/** "<think>…</think>cevap" biçimini ayırır (bazı modeller düşünceyi metne gömer). */
export function splitThink(src) {
  const m = /^\s*<think>([\s\S]*?)(<\/think>|$)/.exec(src);
  if (!m) return { thinking: "", answer: src, open: false };
  return { thinking: m[1].trim(), answer: src.slice(m[0].length), open: !m[2] };
}

export function renderMd(src) {
  const parts = src.split("```");
  let html = "";
  parts.forEach((part, i) => {
    if (i % 2 === 1) {
      // Kod bloğu (akış sırasında henüz kapanmamış olabilir)
      let lang = "";
      let code = part;
      const nl = part.indexOf("\n");
      if (nl !== -1 && /^[\w+#.-]*$/.test(part.slice(0, nl).trim())) {
        lang = part.slice(0, nl).trim();
        code = part.slice(nl + 1);
      }
      html +=
        `<pre class="code"><div class="code-lang"><span>${esc(lang || "kod")}</span>` +
        `<button type="button" data-copy-code>KOPYALA</button></div><code>${esc(code.replace(/\n$/, ""))}</code></pre>`;
    } else {
      const text = part.replace(/^\n+|\n+$/g, "");
      if (text) html += `<div class="md">${inlineMd(esc(text))}</div>`;
    }
  });
  return html;
}

export async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    // http://127.0.0.1 güvenli bağlam sayılıyor ama yine de yedek yol dursun.
    const ta = el("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand("copy");
    ta.remove();
    return ok;
  }
}
