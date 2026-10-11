/* "Düşünüyor" göstergesi.
 *
 * Üç nokta yerine blok karakterlerle akan bir dalga ve Claude'daki gibi
 * değişen fiiller. Her saniye geçen süre yazılıyor.
 */
import { el } from "./util.js";

const VERBS = [
  "Cooking", "Tuning", "Brewing", "Pondering", "Simmering", "Forging",
  "Conjuring", "Percolating", "Marinating", "Noodling", "Crunching",
  "Wrangling", "Calibrating", "Distilling", "Decoding", "Synthesizing",
  "Transmuting", "Untangling", "Mulling", "Whirring", "Scheming",
  "Cogitating", "Spelunking", "Jacking in", "Bending the spoon",
  "Following the white rabbit", "Compiling thoughts", "Reticulating",
];
const BOUNCE = ["▄", "■", "▀", "■"];
const PULSE = "░▒▓█▓▒░░░░"; // sağa doğru kayan bir parıltı
const BAR_LEN = 8;

export function createSpinner({ words = true, label = null } = {}) {
  const node = el("div", "spinner");
  node.innerHTML = '<span class="glyph"></span><span class="verb"></span><span class="bar"></span><span class="secs"></span>';
  const [glyph, verb, bar, secs] = node.children;
  const started = performance.now();
  let frame = 0;
  let lastVerb = "";

  const nextVerb = () => {
    if (label) { verb.textContent = label; return; }
    if (!words) { verb.textContent = "Thinking…"; return; }
    let v;
    do v = VERBS[(Math.random() * VERBS.length) | 0]; while (v === lastVerb);
    lastVerb = v;
    verb.textContent = v + "…";
  };

  const tick = () => {
    glyph.textContent = BOUNCE[frame % BOUNCE.length];
    let s = "";
    for (let j = 0; j < BAR_LEN; j++) s += PULSE[(((j - frame) % PULSE.length) + PULSE.length) % PULSE.length];
    bar.textContent = s;
    secs.textContent = `${Math.floor((performance.now() - started) / 1000)}s`;
    frame++;
  };

  nextVerb();
  tick();
  const t1 = setInterval(tick, 110);
  const t2 = setInterval(nextVerb, 3200);
  return {
    node,
    setLabel(text) { label = text; nextVerb(); },
    stop() {
      clearInterval(t1);
      clearInterval(t2);
      node.remove();
    },
  };
}
