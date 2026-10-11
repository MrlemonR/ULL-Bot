/* Proje sayfası: ad, bağlam (her mesajda modele verilir) ve projedeki sohbetler. */
import { api } from "./api.js";
import { chatMenuItems, deleteProject, togglePinProject } from "./actions.js";
import { S, on, projectById, upsertProject } from "./store.js";
import { openContextMenu, toast } from "./ui.js";
import { $, debounce, el, esc, relTime } from "./util.js";

const page = $("#project-page");
let currentId = null;
let pendingSave = null;

function setStatus(node, text, kind) {
  node.textContent = text;
  node.classList.toggle("is-ok", kind === "ok");
  node.classList.toggle("is-bad", kind === "bad");
}

export function showProject(projectId) {
  pendingSave?.flush();
  currentId = projectId;
  const p = projectById(projectId);
  if (!p) {
    page.innerHTML = '<section class="panel"><p class="muted">Proje bulunamadı. Silinmiş olabilir.</p><a class="btn" href="#/">← SOHBETE DÖN</a></section>';
    return;
  }
  page.innerHTML = `
    <div class="project-title">
      <span class="ic">▣</span>
      <input id="pj-name" maxlength="80" spellcheck="false" aria-label="Proje adı" />
      <button class="btn btn-ghost" id="pj-pin" type="button"></button>
      <button class="btn btn-danger" id="pj-del" type="button">SİL</button>
    </div>

    <section class="panel project-ctx">
      <header class="panel-head"><h2>BAĞLAM</h2><span class="right"><span class="status" id="pj-status"></span></span></header>
      <p class="panel-desc">
        Bu projedeki <b>her sohbette, her mesajda</b> modele verilir; model cevap vermeden önce buna bakar.
        Projenin amacı, kurallar, karakterler, teknik detaylar, notlar… ne gerekiyorsa yaz. Otomatik kaydedilir.
      </p>
      <textarea class="field" id="pj-ctx" maxlength="100000"
        placeholder="Örn: Unity ile bir FPS korku oyunu yapıyorum. Ana karakter cerrah; silah olarak neşter kullanıyor. C# kodu yazarken Unity 6 API'sini kullan…"></textarea>
      <p class="status" id="pj-count"></p>
    </section>

    <section class="panel">
      <header class="panel-head">
        <h2>SOHBETLER</h2>
        <span class="right"><a class="btn btn-solid" href="#/new/${p.id}">+ YENİ SOHBET</a></span>
      </header>
      <div id="pj-chats"></div>
    </section>`;

  const nameIn = $("#pj-name");
  const ctxIn = $("#pj-ctx");
  const status = $("#pj-status");
  nameIn.value = p.name;
  ctxIn.value = p.context;

  const count = () => { $("#pj-count").textContent = `${ctxIn.value.length.toLocaleString("tr-TR")} karakter`; };
  count();

  let dirty = false;
  const save = debounce(async () => {
    if (!dirty) return;
    dirty = false;
    const name = nameIn.value.trim();
    if (!name) return setStatus(status, "ad boş olamaz", "bad");
    try {
      upsertProject(await api(`/api/projects/${p.id}`, { method: "PATCH", body: { name, context: ctxIn.value } }));
      setStatus(status, "✓ kaydedildi", "ok");
    } catch (err) {
      setStatus(status, "kaydedilemedi: " + err.message, "bad");
    }
  }, 600);
  pendingSave = save;
  const onEdit = () => { dirty = true; setStatus(status, "… kaydediliyor"); count(); save(); };
  nameIn.addEventListener("input", onEdit);
  ctxIn.addEventListener("input", onEdit);

  const pin = $("#pj-pin");
  pin.textContent = p.pinned ? "▪ SABİTLENDİ" : "SABİTLE";
  pin.addEventListener("click", () => togglePinProject(projectById(p.id)));
  $("#pj-del").addEventListener("click", () => deleteProject(projectById(p.id)));

  renderChats();
}

function renderChats() {
  const box = $("#pj-chats");
  if (!box || !currentId) return;
  const chats = S.chats.filter((c) => c.project_id === currentId);
  box.innerHTML = "";
  if (!chats.length) {
    box.appendChild(el("p", "muted", "Bu projede henüz sohbet yok. Yeni sohbet başlatınca bağlam otomatik kullanılır."));
    return;
  }
  const rows = el("div", "chat-rows");
  chats.forEach((c) => {
    const row = el("div", "chat-row side-item");
    row.innerHTML =
      `<span class="ic">${c.pinned ? "▪" : "○"}</span><a class="t" href="#/c/${c.id}">${esc(c.title)}</a>` +
      `<span class="d">${relTime(c.updated_at)}</span>` +
      `<button class="mini-btn more" type="button" aria-label="Seçenekler" aria-expanded="false">⋯</button>`;
    row.querySelector(".more").addEventListener("click", (e) => openContextMenu(e.currentTarget, chatMenuItems(c)));
    rows.appendChild(row);
  });
  box.appendChild(rows);
}

export function leaveProject() {
  pendingSave?.flush();
  pendingSave = null;
  currentId = null;
}

on("chats", renderChats);
on("projects", () => {
  if (!currentId) return;
  const p = projectById(currentId);
  const pin = $("#pj-pin");
  if (p && pin) pin.textContent = p.pinned ? "▪ SABİTLENDİ" : "SABİTLE";
});
on("project-deleted", (p) => {
  if (p.id === currentId) {
    pendingSave?.cancel();
    pendingSave = null;
    currentId = null;
    toast(`"${p.name}" silindi`);
    location.hash = "#/";
  }
});
