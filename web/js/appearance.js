/* Görünüm ayarlarını sayfaya uygular (CSS değişkenleri + yağmur). */
import { configure as configureRain } from "./rain.js";
import { hexToRgba } from "./util.js";

export const DEFAULT_APPEARANCE = {
  rain_enabled: true,
  rain_speed: 1,
  rain_density: 1,
  rain_opacity: 1,
  rain_red_ratio: 0.06,
  rain_size: 18,
  font_size: 14,
  font_bold: false,
  font_family: "sans",
  accent: "#ff2a3d",
  panel_opacity: 0.88,
  chat_width: 860,
  spinner_words: true,
};

export function applyAppearance(a) {
  a = { ...DEFAULT_APPEARANCE, ...a };
  const r = document.documentElement.style;
  r.setProperty("--fs", `${a.font_size}px`);
  r.setProperty("--fw", a.font_bold ? "600" : "400");
  r.setProperty("--font-body", a.font_family === "mono" ? "var(--mono)" : "var(--sans)");
  r.setProperty("--red", a.accent);
  r.setProperty("--red-soft", hexToRgba(a.accent, 0.12));
  r.setProperty("--red-line", hexToRgba(a.accent, 0.45));
  r.setProperty("--panel-a", String(a.panel_opacity));
  r.setProperty("--chat-w", `${a.chat_width}px`);

  configureRain({
    enabled: a.rain_enabled,
    speed: a.rain_speed,
    density: a.rain_density,
    size: a.rain_size,
    redRatio: a.rain_red_ratio,
    opacity: a.rain_opacity,
    red: a.accent,
  });

  // Bir sonraki açılışta sayfa çizilmeden uygulanabilsin (index.html başı).
  try { localStorage.setItem("ull.appearance", JSON.stringify(a)); } catch { /* yok say */ }
}
