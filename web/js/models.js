/* Sağ üstteki model seçici ve Ollama bağlantı göstergesi. */
import { api } from "./api.js";
import { S, emit, on } from "./store.js";
import { arrowNav, toast } from "./ui.js";
import { $, el, esc, fmtBytes } from "./util.js";

const btn = $("#model-btn");
const menu = $("#model-menu");

const CAT_SHORT = { genel: "genel", turkce: "türkçe", kod: "kod", mantik: "mantık", hafif: "hafif", gorsel: "görsel" };

export async function refreshModels() {
  let res;
  try {
    res = await api("/api/models");
  } catch {
    res = { ok: false, models: [], error: "Sunucuya ulaşılamadı" };
  }
  S.online = res.ok;
  S.installed = res.models || [];
  S.ollamaError = res.error || "";
  if (res.url) S.ollamaUrl = res.url;

  const conn = $("#conn");
  conn.classList.toggle("is-ok", res.ok);
  conn.classList.toggle("is-bad", !res.ok);
  $("span", conn).textContent = res.ok ? "ollama" : "ollama yok";
  conn.title = res.ok
    ? `Ollama bağlı (${S.ollamaUrl}) · ${S.installed.length} model`
    : `${res.error || "Ollama'ya ulaşılamadı"}\nAyarlamak için tıkla.`;
  renderButton();
  emit("models");
  return res;
}

export const isInstalled = (name) =>
  S.installed.some((m) => m.name === name || m.name === `${name}:latest`);

/** Ekrandaki moda göre geçerli model: Code modunda kod modeli (yoksa sohbet modeli). */
export const currentModel = () =>
  S.mode === "code" ? (S.settings.code_model || S.settings.model) : S.settings.model;

function renderButton() {
  $("#model-name").textContent = currentModel() || "—";
  const missing = S.online && !isInstalled(currentModel());
  btn.classList.toggle("is-warn", Boolean(missing));
  btn.title = missing ? `Model kurulu değil — Ayarlar → Modeller'den indir` : "Model değiştir";
}

export async function setModel(name) {
  try {
    const body = S.mode === "code" ? { code_model: name } : { model: name };
    S.settings = await api("/api/settings", { method: "PUT", body });
    renderButton();
    emit("settings");
    toast(`Model: ${name}`);
  } catch (err) {
    toast("Model değiştirilemedi: " + err.message, true);
  }
}

function closeMenu() {
  menu.hidden = true;
  btn.setAttribute("aria-expanded", "false");
}

function openMenu() {
  const code = S.mode === "code";
  menu.innerHTML = code
    ? '<div class="pop-title">KOD MODELİ SEÇ · araç (tools) desteği gerekir</div>'
    : '<div class="pop-title">MODEL SEÇ</div>';
  if (!S.online) {
    menu.appendChild(el("div", "pop-empty", esc(S.ollamaError || "Ollama'ya ulaşılamadı.")));
  } else if (!S.installed.length) {
    menu.appendChild(el("div", "pop-empty", "Kurulu model yok. Aşağıdan öneri al ve indir."));
  }
  // Code modunda kod modelleri üstte.
  const list = code ? [...S.installed].sort((a, b) => b.tags.includes("kod") - a.tags.includes("kod")) : S.installed;
  list.forEach((m) => {
    const active = m.name === currentModel();
    const item = el("button", "pop-item" + (active ? " is-active" : ""));
    item.type = "button";
    const tags = m.tags.map((t) => CAT_SHORT[t] || t).join(" · ");
    item.innerHTML =
      `<span class="n">${esc(m.name)}<span class="meta">${esc(m.parameter_size)} · ${fmtBytes(m.size)}</span></span>` +
      `<span class="p">${esc(m.desc || tags || m.family || "")}</span>`;
    item.addEventListener("click", () => {
      closeMenu();
      if (!active) setModel(m.name);
    });
    menu.appendChild(item);
  });
  const manage = el("a", "pop-item is-link", '<span class="n">Modelleri yönet · öneri al</span>');
  manage.href = "#/settings/models";
  manage.addEventListener("click", closeMenu);
  menu.appendChild(manage);

  menu.hidden = false;
  btn.setAttribute("aria-expanded", "true");
  (menu.querySelector(".pop-item.is-active") || menu.querySelector(".pop-item"))?.focus();
  refreshModels(); // arka planda tazele; bir sonraki açılışta güncel olsun
}

btn.addEventListener("click", () => (menu.hidden ? openMenu() : closeMenu()));
arrowNav(menu);
document.addEventListener("mousedown", (e) => {
  if (!menu.hidden && !e.target.closest(".model-pick")) closeMenu();
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !menu.hidden) { closeMenu(); btn.focus(); }
});

on("settings", renderButton);
on("mode", renderButton);
