/* Matrix yağmuru — arka planda düşen Japonca karakterler (siyah-beyaz, az kırmızı).
 *
 * Her sütunun kendi hızı var; başı parlak, arkası kanvasa her karede çizilen
 * yarı saydam siyahla sönerek iz bırakıyor. Filmdeki gibi yarım genişlikli
 * katakana ağırlıklı, araya biraz kanji ve rakam karışıyor.
 *
 * Ayarlar → Görünüm'den configure() ile değiştiriliyor.
 */

const KATAKANA = "ｦｱｲｳｴｵｶｷｸｹｺｻｼｽｾｿﾀﾁﾂﾃﾄﾅﾆﾇﾈﾉﾊﾋﾌﾍﾎﾏﾐﾑﾒﾓﾔﾕﾖﾗﾘﾙﾚﾛﾜﾝ";
const KANJI = "日月火水木金土雨夢光闇電脳空心";
const DIGITS = "0123456789";
const GLYPHS = KATAKANA + KATAKANA + KANJI + DIGITS;

const BASE_FPS = 24;
const BG_SOLID = "#050505";
const BG_FADE = "rgba(5, 5, 5, 0.085)"; // iz uzunluğunu bu alfa belirliyor
const BODY = "#6e6e6e";
const HEAD = "#ffffff";

const cfg = {
  enabled: true,
  speed: 1,       // çarpan
  density: 1,     // sütunların ne kadarı aktif (0-1)
  size: 18,       // hücre boyu (px)
  redRatio: 0.06, // kırmızı sütun oranı
  opacity: 1,
  red: "#ff2a3d",
};

const canvas = document.getElementById("rain");
const ctx = canvas ? canvas.getContext("2d") : null;
const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

let w = 0, h = 0, rows = 0, drops = [];
let redBody = "#c41e2e", redHead = "#ff8a94";
let running = false, last = 0;

const pick = () => GLYPHS[(Math.random() * GLYPHS.length) | 0];

function mix(hex, toward, t) {
  const n = parseInt(hex.slice(1), 16);
  const m = parseInt(toward.slice(1), 16);
  const c = (shift) => Math.round(((n >> shift) & 255) * (1 - t) + ((m >> shift) & 255) * t);
  return `rgb(${c(16)}, ${c(8)}, ${c(0)})`;
}

function newDrop(initial) {
  return {
    // İlk açılışta ekran boş görünmesin diye bazıları ortadan başlıyor.
    y: initial ? Math.random() * rows * 1.2 - rows * 0.6 : -Math.random() * rows * 0.5,
    speed: 0.25 + Math.random() * 0.55, // satır / kare
    row: -1,
    dim: Math.random() < 0.35,          // bazı sütunlar daha sönük → derinlik
    red: Math.random() < cfg.redRatio,
    on: Math.random() < cfg.density,
  };
}

function resize() {
  if (!ctx) return;
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  w = window.innerWidth;
  h = window.innerHeight;
  canvas.width = Math.floor(w * dpr);
  canvas.height = Math.floor(h * dpr);
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.fillStyle = BG_SOLID;
  ctx.fillRect(0, 0, w, h);
  ctx.font = `${cfg.size - 2}px "MS Gothic", "Noto Sans JP", "Hiragino Sans", monospace`;
  ctx.textBaseline = "top";
  ctx.textAlign = "center";
  rows = Math.ceil(h / cfg.size);
  drops = Array.from({ length: Math.ceil(w / cfg.size) }, () => newDrop(true));
}

function cell(i, r, color, alpha) {
  const S = cfg.size;
  ctx.fillStyle = BG_SOLID;
  ctx.fillRect(i * S, r * S, S, S);
  ctx.fillStyle = color;
  ctx.globalAlpha = alpha;
  ctx.fillText(pick(), i * S + S / 2, r * S);
  ctx.globalAlpha = 1;
}

function step() {
  ctx.fillStyle = BG_FADE;
  ctx.fillRect(0, 0, w, h);

  for (let i = 0; i < drops.length; i++) {
    const d = drops[i];
    if (!d.on) {
      // Kapalı sütun ara sıra yeniden doğabilir (yoğunluk değişince boşluklar dolsun).
      if (Math.random() < 0.002) drops[i] = newDrop(false);
      continue;
    }
    d.y += d.speed * cfg.speed;
    const row = Math.floor(d.y);
    if (row === d.row) continue;

    // Bir önceki baş artık gövdenin parçası: parlak harfi sil, gövde rengiyle yaz.
    if (d.row >= 0) cell(i, d.row, d.red ? redBody : BODY, d.dim ? 0.45 : 0.9);
    d.row = row;
    if (row >= 0) {
      ctx.fillStyle = d.red ? (d.dim ? redBody : redHead) : (d.dim ? BODY : HEAD);
      ctx.globalAlpha = d.dim ? 0.6 : 1;
      ctx.fillText(pick(), i * cfg.size + cfg.size / 2, row * cfg.size);
      ctx.globalAlpha = 1;
    }
    if (row * cfg.size > h && Math.random() > 0.96) drops[i] = newDrop(false);
  }

  // Arada bir gövdedeki rastgele bir harf değişiyor (filmdeki titreşim).
  for (let k = 0; k < 3; k++) {
    const i = (Math.random() * drops.length) | 0;
    const d = drops[i];
    if (!d || !d.on || d.row < 2) continue;
    const r = d.row - 1 - ((Math.random() * Math.min(d.row, 14)) | 0);
    if (r >= 0) cell(i, r, d.red ? redBody : BODY, 0.5);
  }
}

function loop(t) {
  if (!running) return;
  // Hareket azaltma tercihi açıksa yağmur çok yavaş akıyor.
  const fps = reduceMotion.matches ? 4 : BASE_FPS;
  if (t - last >= 1000 / fps) {
    last = t;
    step();
  }
  requestAnimationFrame(loop);
}

function start() {
  if (running || !ctx) return;
  running = true;
  requestAnimationFrame(loop);
}

export function configure(next) {
  const prevSize = cfg.size;
  const prevDensity = cfg.density;
  const prevRed = cfg.redRatio;
  Object.assign(cfg, next);
  if (!ctx) return;

  redBody = mix(cfg.red, "#000000", 0.25);
  redHead = mix(cfg.red, "#ffffff", 0.5);
  canvas.style.opacity = String(cfg.opacity);
  document.documentElement.classList.toggle("no-rain", !cfg.enabled);

  if (!cfg.enabled) {
    running = false;
    return;
  }
  if (cfg.size !== prevSize || !drops.length) resize();
  else if (cfg.density !== prevDensity || cfg.redRatio !== prevRed) {
    drops.forEach((d) => {
      d.on = Math.random() < cfg.density;
      d.red = Math.random() < cfg.redRatio;
    });
  }
  start();
}

let resizeTimer = 0;
window.addEventListener("resize", () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => cfg.enabled && resize(), 120);
});
