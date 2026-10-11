/* Sol kenar çubuğu: sabitlenenler, projeler (klasörler), diğer sohbetler, arama. */
import { api } from "./api.js";
import { chatMenuItems, createProject, projectMenuItems } from "./actions.js";
import { S, on, projectById } from "./store.js";
import { openContextMenu } from "./ui.js";
import { $, debounce, el, esc } from "./util.js";

const list = $("#side-list");
const search = $("#side-search");
let route = { view: "chat" };
let searchResults = null; // null: arama yok

// Katlanan proje bölümleri bu tarayıcıda hatırlanıyor.
const collapsed = new Set(JSON.parse(localStorage.getItem("ull.collapsed") || "[]"));
const saveCollapsed = () => {
  try { localStorage.setItem("ull.collapsed", JSON.stringify([...collapsed])); } catch { /* yok say */ }
};

function chatItem(c, { showProject = false } = {}) {
  const active = route.view === "chat" && route.chatId === c.id;
  const item = el("div", "side-item" + (active ? " is-active" : ""));
  const p = showProject && c.project_id ? projectById(c.project_id) : null;
  item.innerHTML =
    `<span class="ic">${c.pinned ? "▪" : "○"}</span>` +
    `<a class="t" href="#/c/${c.id}" title="${esc(c.title)}">${esc(c.title)}${p ? ` <span class="muted">· ${esc(p.name)}</span>` : ""}</a>` +
    `<button class="mini-btn more" type="button" aria-label="Seçenekler" aria-expanded="false">⋯</button>`;
  item.querySelector(".more").addEventListener("click", (e) => {
    e.preventDefault();
    openContextMenu(e.currentTarget, chatMenuItems(c));
  });
  return item;
}

function projectLink(p) {
  const active = route.view === "project" && route.projectId === p.id;
  const item = el("div", "side-item" + (active ? " is-active" : ""));
  item.innerHTML =
    `<span class="ic">▣</span><a class="t" href="#/p/${p.id}" title="${esc(p.name)}">${esc(p.name)}</a>` +
    `<button class="mini-btn more" type="button" aria-label="Proje seçenekleri" aria-expanded="false">⋯</button>`;
  item.querySelector(".more").addEventListener("click", (e) => openContextMenu(e.currentTarget, projectMenuItems(p)));
  return item;
}

function section(label, { tools = [], collapseKey = null, href = null } = {}) {
  const sec = el("section", "side-sec");
  const head = el("div", "side-sec-head");
  if (collapseKey) {
    const isCol = collapsed.has(collapseKey);
    sec.classList.toggle("is-collapsed", isCol);
    const chev = el("button", "mini-btn chev", isCol ? "▸" : "▾");
    chev.type = "button";
    chev.title = "Daralt / genişlet";
    chev.addEventListener("click", () => {
      if (collapsed.has(collapseKey)) collapsed.delete(collapseKey);
      else collapsed.add(collapseKey);
      saveCollapsed();
      render();
    });
    head.appendChild(chev);
  }
  const lbl = href ? el("a", "label") : el("span", "label");
  if (href) lbl.href = href;
  lbl.textContent = label;
  lbl.title = label;
  head.appendChild(lbl);
  if (tools.length) {
    const box = el("span", "tools");
    tools.forEach(([glyph, title, fn]) => {
      const b = el("button", "mini-btn", glyph);
      b.type = "button";
      b.title = title;
      b.setAttribute("aria-label", title);
      b.setAttribute("aria-expanded", "false");
      b.addEventListener("click", (e) => fn(e.currentTarget));
      box.appendChild(b);
    });
    head.appendChild(box);
  }
  const items = el("div", "side-items");
  sec.append(head, items);
  list.appendChild(sec);
  return items;
}

function render() {
  list.innerHTML = "";

  if (searchResults) {
    const items = section(`Sonuçlar (${searchResults.length})`);
    if (!searchResults.length) items.appendChild(el("div", "side-empty", "Eşleşen sohbet yok."));
    searchResults.forEach((c) => items.appendChild(chatItem(c, { showProject: true })));
    return;
  }

  const pinnedProjects = S.projects.filter((p) => p.pinned);
  const pinnedChats = S.chats.filter((c) => c.pinned);
  if (pinnedProjects.length || pinnedChats.length) {
    const items = section("Sabitlenenler");
    pinnedProjects.forEach((p) => items.appendChild(projectLink(p)));
    pinnedChats.forEach((c) => items.appendChild(chatItem(c, { showProject: true })));
  }

  S.projects.forEach((p) => {
    const items = section(p.name, {
      collapseKey: p.id,
      href: `#/p/${p.id}`,
      tools: [
        ["+", "Bu projede yeni sohbet", () => { location.hash = `#/new/${p.id}`; }],
        ["⋯", "Proje seçenekleri", (btn) => openContextMenu(btn, projectMenuItems(p))],
      ],
    });
    const chats = S.chats.filter((c) => c.project_id === p.id && !c.pinned);
    if (!chats.length) items.appendChild(el("div", "side-empty", "Henüz sohbet yok — + ile başlat."));
    chats.forEach((c) => items.appendChild(chatItem(c)));
  });

  const others = S.chats.filter((c) => !c.project_id && !c.pinned);
  const items = section(S.projects.length ? "Diğer" : "Sohbetler", {
    tools: [["+", "Yeni sohbet", () => { location.hash = "#/"; }]],
  });
  if (!others.length) items.appendChild(el("div", "side-empty", "Henüz sohbet yok."));
  others.forEach((c) => items.appendChild(chatItem(c)));
}

const runSearch = debounce(async (q) => {
  if (!q) {
    searchResults = null;
    render();
    return;
  }
  try {
    const results = await api(`/api/chats?q=${encodeURIComponent(q)}`);
    if (search.value.trim() === q) {
      searchResults = results;
      render();
    }
  } catch { /* arama hatası sessiz geçilir */ }
}, 200);

search.addEventListener("input", () => runSearch(search.value.trim()));
search.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    search.value = "";
    runSearch.flush("");
  }
});

$("#act-new-project").addEventListener("click", () => createProject());

on("chats", () => {
  // Arama açıkken liste değiştiyse (silme, adlandırma) sonuçları tazele.
  if (searchResults) runSearch(search.value.trim());
  else render();
});
on("projects", render);
on("route", (r) => {
  route = r;
  $("#act-settings").classList.toggle("is-active", r.view === "settings");
  $("#act-train").classList.toggle("is-active", r.view === "train");
  render();
});

export function focusSearch() {
  search.focus();
  search.select();
}
