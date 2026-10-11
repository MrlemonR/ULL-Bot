/* Matrix yağmuru — arka planda düşen Japonca karakterler (siyah-beyaz, az kırmızı).
 *
 * Her sütunun kendi hızı var; başı parlak, arkası kanvasa her karede çizilen
 * yarı saydam siyahla sönerek iz bırakıyor. Filmdeki gibi yarım genişlikli
 * katakana ağırlıklı, araya biraz kanji ve rakam karışıyor.
 */
(() => {
  const canvas = document.getElementById("rain");
  if (!canvas) return;
  const ctx = canvas.getContext("2d");

  const KATAKANA = "ｦｱｲｳｴｵｶｷｸｹｺｻｼｽｾｿﾀﾁﾂﾃﾄﾅﾆﾇﾈﾉﾊﾋﾌﾍﾎﾏﾐﾑﾒﾓﾔﾕﾖﾗﾘﾙﾚﾛﾜﾝ";
  const KANJI = "日月火水木金土雨夢光闇電脳空心";
  const DIGITS = "0123456789";
  const GLYPHS = KATAKANA + KATAKANA + KANJI + DIGITS;

  const SIZE = 18;          // hücre boyu (px)
  const FPS = 24;
  const BG_SOLID = "#050505";
  const BG = "rgba(5, 5, 5, 0.085)";   // iz uzunluğunu bu alfa belirliyor
  // Siyah-beyaz: gri gövde, beyaz baş. Sütunların küçük bir kısmı kırmızı.
  const BODY = "#6e6e6e";
  const HEAD = "#ffffff";
  const RED_BODY = "#c41e2e";
  const RED_HEAD = "#ff8a94";
  const RED_RATIO = 0.06;

  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

  let w = 0, h = 0, rows = 0, drops = [];

  const pick = () => GLYPHS[(Math.random() * GLYPHS.length) | 0];

  function newDrop(initial) {
    return {
      // İlk açılışta ekran boş görünmesin diye bazıları ortadan başlıyor.
      y: initial ? Math.random() * rows * 1.2 - rows * 0.6 : -Math.random() * rows * 0.5,
      speed: 0.25 + Math.random() * 0.55,   // satır / kare
      row: -1,
      dim: Math.random() < 0.35,           // bazı sütunlar daha sönük → derinlik
      red: Math.random() < RED_RATIO,
    };
  }

  function resize() {
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    w = window.innerWidth;
    h = window.innerHeight;
    canvas.width = Math.floor(w * dpr);
    canvas.height = Math.floor(h * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.fillStyle = BG_SOLID;
    ctx.fillRect(0, 0, w, h);
    ctx.font = `${SIZE - 2}px "MS Gothic", "Noto Sans JP", "Hiragino Sans", monospace`;
    ctx.textBaseline = "top";
    ctx.textAlign = "center";
    rows = Math.ceil(h / SIZE);
    const cols = Math.ceil(w / SIZE);
    drops = Array.from({ length: cols }, () => newDrop(true));
  }

  function step() {
    ctx.fillStyle = BG;
    ctx.fillRect(0, 0, w, h);

    for (let i = 0; i < drops.length; i++) {
      const d = drops[i];
      d.y += d.speed;
      const row = Math.floor(d.y);
      if (row === d.row) continue;
      const x = i * SIZE + SIZE / 2;

      // Bir önceki baş artık gövdenin parçası: parlak harfi sil, gövde rengiyle yaz.
      if (d.row >= 0) {
        ctx.fillStyle = BG_SOLID;
        ctx.fillRect(i * SIZE, d.row * SIZE, SIZE, SIZE);
        ctx.fillStyle = d.red ? RED_BODY : BODY;
        ctx.globalAlpha = d.dim ? 0.45 : 0.9;
        ctx.fillText(pick(), x, d.row * SIZE);
        ctx.globalAlpha = 1;
      }

      d.row = row;
      if (row >= 0) {
        ctx.fillStyle = d.red ? (d.dim ? RED_BODY : RED_HEAD) : (d.dim ? BODY : HEAD);
        ctx.globalAlpha = d.dim ? 0.6 : 1;
        ctx.fillText(pick(), x, row * SIZE);
        ctx.globalAlpha = 1;
      }

      if (row * SIZE > h && Math.random() > 0.96) drops[i] = newDrop(false);
    }

    // Arada bir gövdedeki rastgele bir harf değişiyor (filmdeki titreşim).
    for (let k = 0; k < 3; k++) {
      const i = (Math.random() * drops.length) | 0;
      const d = drops[i];
      if (d.row < 2) continue;
      const r = d.row - 1 - ((Math.random() * Math.min(d.row, 14)) | 0);
      if (r < 0) continue;
      ctx.fillStyle = BG_SOLID;
      ctx.fillRect(i * SIZE, r * SIZE, SIZE, SIZE);
      ctx.fillStyle = d.red ? RED_BODY : BODY;
      ctx.globalAlpha = 0.5;
      ctx.fillText(pick(), i * SIZE + SIZE / 2, r * SIZE);
      ctx.globalAlpha = 1;
    }
  }

  let last = 0;
  function loop(t) {
    // Hareket azaltma tercihi açıksa yağmur çok yavaş akıyor.
    const fps = reduceMotion.matches ? 4 : FPS;
    if (t - last >= 1000 / fps) {
      last = t;
      step();
    }
    requestAnimationFrame(loop);
  }

  let resizeTimer = 0;
  window.addEventListener("resize", () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(resize, 120);
  });

  resize();
  requestAnimationFrame(loop);
})();
