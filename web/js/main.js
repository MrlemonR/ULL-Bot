/* Açılış ve yönlendirme.
 *
 * Adresler:
 *   #/                 yeni sohbet
 *   #/new/<proje>      projede yeni sohbet
 *   #/c/<sohbet>       sohbet
 *   #/p/<proje>        proje sayfası (bağlam)
 *   #/settings/<sayfa> ayarlar (appearance | models | personas | train)
 *   #/code             code modu: oturum seç / yeni oturum
 *   #/code/<oturum>    code oturumu
 */
import { api } from "./api.js";
import { renameChat } from "./actions.js";
import { applyAppearance } from "./appearance.js";
import { current, focusInput, showChat, showNewChat } from "./chat.js";
import { codeCrumb, leaveCode, showCode } from "./code.js";
import { refreshModels } from "./models.js";
import { leaveProject, showProject } from "./project.js";
import { leaveSettings, showSettings } from "./settings.js";
import { focusSearch } from "./sidebar.js";
import { S, chatById, emit, on, projectById } from "./store.js";
import { $, $$, el, esc } from "./util.js";

const app = $("#app");
let route = { view: "chat", chatId: null, projectId: null };

function parse(hash) {
  const parts = hash.replace(/^#\/?/, "").split("/").filter(Boolean).map(decodeURIComponent);
  if (parts[0] === "c" && parts[1]) return { view: "chat", chatId: parts[1] };
  if (parts[0] === "new" && parts[1]) return { view: "chat", chatId: null, projectId: parts[1] };
  if (parts[0] === "p" && parts[1]) return { view: "project", projectId: parts[1] };
  if (parts[0] === "settings") return { view: "settings", page: parts[1] || "appearance" };
  if (parts[0] === "train") return { view: "settings", page: "train" };
  if (parts[0] === "code") return { view: "code", sessionId: parts[1] || null };
  return { view: "chat", chatId: null, projectId: null };
}

function showView(name) {
  $$(".view").forEach((v) => v.classList.toggle("is-active", v.dataset.view === name));
}

function navigate() {
  const prev = route;
  route = parse(location.hash);
  if (prev.view === "settings" && route.view !== "settings") leaveSettings();
  if (prev.view === "project" && route.view !== "project") leaveProject();
  if (prev.view === "code" && route.view !== "code") leaveCode();
  showView(route.view);

  // Hangi moddayız? Ayarlar ikisine de ait: son modda kalır.
  const mode = route.view === "code" ? "code" : route.view === "settings" ? S.mode : "chat";
  if (route.view !== "settings") {
    try { localStorage.setItem(mode === "code" ? "ull.lastCode" : "ull.lastChat", location.hash || "#/"); } catch { /* yok say */ }
  }
  if (mode !== S.mode) {
    S.mode = mode;
    emit("mode", mode);
  }
  $$(".mode-switch button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.mode === S.mode)));

  if (route.view === "chat") {
    if (route.chatId) showChat(route.chatId);
    else showNewChat(route.projectId || null);
  } else if (route.view === "project") {
    showProject(route.projectId);
  } else if (route.view === "settings") {
    showSettings(route.page);
  } else if (route.view === "code") {
    showCode(route.sessionId);
  }
  app.classList.remove("side-open");
  renderCrumb();
  emit("route", route);
}

// --- üst çubuktaki yol ---------------------------------------------------------------

function renderCrumb() {
  const crumb = $("#crumb");
  crumb.innerHTML = "";
  const add = (node) => crumb.appendChild(node);
  const sep = () => add(el("span", "sep", "/"));

  if (route.view === "settings") {
    add(el("span", "cur", "AYARLAR"));
    return;
  }
  if (route.view === "code") {
    codeCrumb(crumb);
    return;
  }
  if (route.view === "project") {
    const p = projectById(route.projectId);
    add(el("span", "cur", p ? `▣ ${esc(p.name)}` : "proje"));
    return;
  }
  const { chatId, projectId } = current();
  const chat = chatId ? chatById(chatId) : null;
  const p = projectById(chat ? chat.project_id : projectId);
  if (p) {
    const a = el("a", "", `▣ ${esc(p.name)}`);
    a.href = `#/p/${p.id}`;
    add(a);
    sep();
  }
  if (chat) {
    const t = el("a", "cur", esc(chat.title));
    t.href = "#";
    t.title = "Yeniden adlandırmak için tıkla";
    t.addEventListener("click", (e) => {
      e.preventDefault();
      renameChat(chatById(chat.id));
    });
    add(t);
  } else {
    add(el("span", "cur", "Yeni sohbet"));
  }
}

// Akış yeni sohbete kimlik verince adres değişiyor ama sayfa yeniden açılmıyor.
on("route-replaced", (r) => {
  route = { ...route, ...r };
  renderCrumb();
  emit("route", route);
});
on("chats", renderCrumb);
on("projects", renderCrumb);
on("code-sessions", () => { if (route.view === "code") renderCrumb(); });

// --- Sohbet / Code geçişi --------------------------------------------------------------

$$(".mode-switch button").forEach((b) =>
  b.addEventListener("click", () => {
    let target = b.dataset.mode === "code" ? "#/code" : "#/";
    try { target = localStorage.getItem(b.dataset.mode === "code" ? "ull.lastCode" : "ull.lastChat") || target; } catch { /* yok say */ }
    if (location.hash === target) navigate();
    else location.hash = target;
  }),
);

// --- kenar çubuğu ----------------------------------------------------------------------

const isNarrow = () => window.matchMedia("(max-width: 860px)").matches;

$("#side-toggle").addEventListener("click", () => {
  if (isNarrow()) {
    app.classList.toggle("side-open");
  } else {
    app.classList.toggle("side-hidden");
    try { localStorage.setItem("ull.sideHidden", app.classList.contains("side-hidden") ? "1" : ""); } catch { /* yok say */ }
  }
});
$("#side-close").addEventListener("click", () => app.classList.remove("side-open"));
$("#side-scrim").addEventListener("click", () => app.classList.remove("side-open"));
try {
  if (localStorage.getItem("ull.sideHidden")) app.classList.add("side-hidden");
} catch { /* yok say */ }

// --- kısayollar ------------------------------------------------------------------------

document.addEventListener("keydown", (e) => {
  const mod = e.ctrlKey || e.metaKey;
  if (mod && e.key.toLowerCase() === "k") {
    e.preventDefault();
    if (isNarrow()) app.classList.add("side-open");
    focusSearch();
  } else if (mod && e.shiftKey && e.key.toLowerCase() === "o") {
    e.preventDefault();
    location.hash = "#/";
    focusInput();
  }
});

// --- açılış ------------------------------------------------------------------------------

async function boot() {
  let data;
  try {
    data = await api("/api/bootstrap");
  } catch (err) {
    showView("chat");
    // Yeni arayüz + eski sunucu: güncellemeden sonra eski pencere açık kalmış.
    const stale = /not found|404/i.test(err.message);
    $("#empty-title").textContent = stale
      ? "Arayüz güncel ama arkada ESKİ bir ULL-Bot sunucusu çalışıyor."
      : "ULL-Bot sunucusuna ulaşılamadı: " + err.message;
    $("#empty-meta").textContent =
      "Açık kalan ULL-Bot terminal penceresini kapat (Ctrl+C), sonra yeniden başlat: uv run ull-bot";
    return;
  }
  S.settings = data.settings;
  S.personas = data.personas;
  S.projects = data.projects;
  S.chats = data.chats;
  S.codeSessions = data.code_sessions || [];
  S.ollamaUrl = data.ollama_url;
  S.ollamaUrlDefault = data.ollama_url_default;
  S.version = data.version;
  $("#side-foot").textContent = `ULL-Bot v${data.version}`;
  applyAppearance(S.settings.appearance);

  window.addEventListener("hashchange", navigate);
  navigate();
  emit("settings");
  emit("personas");
  emit("chats");
  emit("code-sessions");

  await refreshModels();
  setInterval(refreshModels, 20000);
}

boot();
