/* Açılış ve yönlendirme.
 *
 * Adresler:
 *   #/                 yeni sohbet
 *   #/new/<proje>      projede yeni sohbet
 *   #/c/<sohbet>       sohbet
 *   #/p/<proje>        proje sayfası (bağlam)
 *   #/settings/<sayfa> ayarlar (appearance | models | personas)
 */
import { api } from "./api.js";
import { renameChat } from "./actions.js";
import { applyAppearance } from "./appearance.js";
import { current, focusInput, showChat, showNewChat } from "./chat.js";
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
  showView(route.view);

  if (route.view === "chat") {
    if (route.chatId) showChat(route.chatId);
    else showNewChat(route.projectId || null);
  } else if (route.view === "project") {
    showProject(route.projectId);
  } else if (route.view === "settings") {
    showSettings(route.page);
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
    $("#empty-title").textContent = "Sunucuya ulaşılamadı: " + err.message;
    return;
  }
  S.settings = data.settings;
  S.personas = data.personas;
  S.projects = data.projects;
  S.chats = data.chats;
  S.ollamaUrl = data.ollama_url;
  applyAppearance(S.settings.appearance);

  window.addEventListener("hashchange", navigate);
  navigate();
  emit("settings");
  emit("personas");
  emit("chats");

  await refreshModels();
  setInterval(refreshModels, 20000);
}

boot();
