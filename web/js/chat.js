/* Sohbet görünümü: mesajlar, akış, kişilik seçici. */
import { api, stream } from "./api.js";
import { S, activePersona, chatById, emit, on, projectById, upsertChat } from "./store.js";
import { createSpinner } from "./spinner.js";
import { arrowNav, toast } from "./ui.js";
import { $, copyText, el, esc, renderMd, splitThink } from "./util.js";

const log = $("#chat-log");
const input = $("#chat-input");
const sendBtn = $("#send-btn");
const emptyBox = $("#chat-empty");

// Ekrandaki sohbet. `token` her görünüm değişiminde artar; geç gelen
// cevapların yanlış ekrana yazılmasını engeller.
const cur = { chatId: null, projectId: null, token: 0 };

// Yayındaki cevap (aynı anda tek). Kullanıcı başka sohbete geçse de akış
// sürer; geri dönünce aynı düğüm tekrar eklenir.
let live = null;

export const current = () => ({ chatId: cur.chatId, projectId: cur.projectId });

// --- açılış ekranı --------------------------------------------------------------

(function drawBanner() {
  const F = {
    U: ["█  █", "█  █", "▀▀▀▀"],
    L: ["█   ", "█   ", "▀▀▀▀"],
    "-": ["    ", "▀▀▀ ", "    "],
    B: ["█▀▀▄", "█▀▀▄", "▀▀▀ "],
    O: ["█▀▀█", "█  █", "▀▀▀▀"],
    T: ["▀█▀", " █ ", " ▀ "],
  };
  const word = "ULL-BOT".split("");
  $("#banner").textContent = [0, 1, 2].map((r) => word.map((c) => F[c][r]).join(" ")).join("\n");
})();

function renderEmpty(error) {
  const p = projectById(cur.projectId);
  $("#empty-title").textContent = error
    ? error
    : p
      ? `▣ ${p.name} — bu projede yeni sohbet.`
      : "Lokal model ile konuş.";
  const persona = activePersona();
  const bits = [`model: ${S.settings.model || "—"}`, `kişilik: ${persona ? persona.name : "yok"}`];
  if (p) bits.push(p.context.trim() ? "proje bağlamı her mesajda modele verilir" : "projenin bağlamı boş");
  if (S.online === false) bits.push("⚠ Ollama'ya bağlanılamadı — sağ üstteki göstergeye tıkla");
  $("#empty-meta").textContent = bits.join(" · ");
}

function clearLog() {
  log.querySelectorAll(".msg").forEach((m) => m.remove());
  emptyBox.hidden = true;
}

function showEmpty(error) {
  renderEmpty(error);
  emptyBox.hidden = false;
}

// --- mesaj düğümleri ----------------------------------------------------------------

const nearBottom = () => log.scrollHeight - log.scrollTop - log.clientHeight < 90;
const toBottom = () => { log.scrollTop = log.scrollHeight; };

function actButton(label, fn, attr) {
  const b = el("button", "", label);
  b.type = "button";
  if (attr) b.setAttribute(attr, "");
  b.addEventListener("click", fn);
  return b;
}

function userNode(text) {
  const m = el("article", "msg msg-user");
  m.innerHTML = `<header class="msg-head"><span class="who">&gt; SEN</span><span class="acts"></span></header><div class="msg-body"><div class="md"></div></div>`;
  $(".md", m).textContent = text;
  $(".acts", m).appendChild(actButton("KOPYALA", () => copyText(text).then(() => toast("Kopyalandı"))));
  return m;
}

function botView(personaName, model) {
  const root = el("article", "msg msg-bot");
  root.innerHTML =
    `<header class="msg-head"><span class="who">◆ ${esc(personaName || "ULL-BOT")}</span>` +
    `<span class="model">${esc(model || "")}</span><span class="acts"></span></header>` +
    `<div class="msg-body" hidden></div><footer class="msg-foot"></footer>`;
  return { root, body: $(".msg-body", root), foot: $(".msg-foot", root), acts: $(".acts", root) };
}

function botHtml(content, thinking, streaming) {
  const sp = splitThink(content);
  const thought = [thinking, sp.thinking].filter((t) => t && t.trim()).join("\n");
  const answering = Boolean(sp.answer.trim());
  let html = "";
  if (thought) {
    const open = streaming && !answering;
    html += `<details class="think"${open ? " open" : ""}><summary>DÜŞÜNCE${open ? "…" : ""}</summary><div class="think-body">${esc(thought)}</div></details>`;
  }
  html += renderMd(sp.answer);
  if (streaming) html += '<span class="cursor">█</span>';
  return html;
}

function formatStats(model, meta = {}) {
  if (meta.stopped) return "■ durduruldu";
  if (meta.error) return "✖ hata ile kesildi";
  const parts = ["✓ " + (model || S.settings.model)];
  if (meta.eval_count) parts.push(`${meta.eval_count} token`);
  if (meta.eval_count && meta.eval_duration) parts.push(`${(meta.eval_count / (meta.eval_duration / 1e9)).toFixed(1)} token/sn`);
  return parts.join(" · ");
}

function addBotActions(view, content) {
  view.acts.innerHTML = "";
  view.acts.appendChild(actButton("KOPYALA", () => copyText(splitThink(content).answer.trim()).then(() => toast("Kopyalandı"))));
  view.acts.appendChild(actButton("↻ YENİDEN ÜRET", () => regenerate(view.root), "data-regen"));
}

function storedBot(msg) {
  const v = botView(msg.meta?.persona_name, msg.model);
  v.body.hidden = false;
  v.body.innerHTML = botHtml(msg.content, msg.thinking, false);
  v.foot.textContent = formatStats(msg.model, msg.meta);
  addBotActions(v, msg.content);
  return v.root;
}

function unansweredNode() {
  const v = botView(activePersona()?.name, S.settings.model);
  v.body.hidden = false;
  v.body.innerHTML = '<div class="muted">Bu mesaja henüz cevap verilmedi.</div>';
  const row = el("div", "retry-row");
  row.appendChild(actButton("↻ CEVAPLA", () => regenerate(v.root)));
  row.firstChild.className = "btn btn-ghost";
  v.body.appendChild(row);
  v.foot.remove();
  return v.root;
}

// --- görünüm geçişleri ------------------------------------------------------------------

export function showNewChat(projectId = null) {
  cur.token++;
  cur.chatId = null;
  cur.projectId = projectId && projectById(projectId) ? projectId : null;
  clearLog();
  // Yeni sohbet ekranından başlatılmış ve hâlâ kimliksiz bir akış varsa geri koy.
  if (live && live.chatId === null && live.projectId === cur.projectId) log.appendChild(live.view.root);
  else showEmpty();
  updateComposer();
  input.focus();
}

export async function showChat(chatId) {
  const token = ++cur.token;
  cur.chatId = chatId;
  cur.projectId = chatById(chatId)?.project_id ?? null;
  clearLog();
  updateComposer();
  let data;
  try {
    data = await api(`/api/chats/${chatId}`);
  } catch (err) {
    if (token === cur.token) showEmpty(`Sohbet açılamadı: ${err.message}`);
    return;
  }
  if (token !== cur.token) return;
  cur.projectId = data.project_id;

  const frag = document.createDocumentFragment();
  data.messages.forEach((m) => frag.appendChild(m.role === "user" ? userNode(m.content) : storedBot(m)));
  const isLive = live && live.chatId === chatId;
  if (isLive) frag.appendChild(live.view.root);
  else if (data.messages.length && data.messages.at(-1).role === "user") frag.appendChild(unansweredNode());
  log.appendChild(frag);
  if (!data.messages.length && !isLive) showEmpty();
  updateComposer();
  toBottom();
  input.focus();
}

// --- gönderme / akış ----------------------------------------------------------------------

function updateComposer() {
  const mine = live && live.chatId === cur.chatId && (cur.chatId !== null || live.projectId === cur.projectId);
  const elsewhere = live && !mine;
  sendBtn.classList.toggle("is-stop", Boolean(mine));
  sendBtn.textContent = mine ? "■ DURDUR" : "GÖNDER ▶";
  sendBtn.type = mine ? "button" : "submit";
  sendBtn.disabled = !mine && (elsewhere || !input.value.trim());
  $("#composer-hint").textContent = mine
    ? "ESC ▸ durdur"
    : elsewhere
      ? "başka bir sohbette cevap yazılıyor…"
      : "ENTER ▸ gönder · SHIFT+ENTER ▸ yeni satır";

  const p = projectById(cur.projectId);
  const tag = $("#composer-project");
  tag.hidden = !p;
  if (p) tag.textContent = `▣ ${p.name}`;
}

function autosize() {
  input.style.height = "auto";
  input.style.height = input.scrollHeight + "px"; // üst sınır CSS'te (max-height)
}

async function runStream(body, view, retryText) {
  const spinner = createSpinner({ words: S.settings.appearance?.spinner_words !== false });
  view.foot.innerHTML = "";
  view.foot.appendChild(spinner.node);
  view.acts.innerHTML = "";

  const controller = new AbortController();
  const L = { chatId: body.chat_id || null, projectId: cur.projectId, controller, view, content: "", thinking: "" };
  live = L;
  const startedOn = cur.token;
  updateComposer();

  let outcome = "error";
  let errorText = "";
  let doneEv = null;
  let queued = false;

  const paint = (streaming) => {
    const stick = view.root.isConnected && nearBottom();
    view.body.hidden = !(L.content || L.thinking);
    view.body.innerHTML = botHtml(L.content, L.thinking, streaming);
    if (stick) toBottom();
  };
  const queuePaint = () => {
    if (!queued) {
      queued = true;
      requestAnimationFrame(() => { queued = false; if (live === L) paint(true); });
    }
  };

  try {
    await stream("/api/send", body, (ev) => {
      if (ev.type === "start") {
        L.chatId = ev.chat.id;
        // Yeni sohbet ekranındaysak adresi sohbetin kendisine çevir (yeniden yüklemeden).
        // cur.chatId, upsertChat'ten ÖNCE atanmalı: liste güncellemesi yazma kutusunu
        // yeniden çiziyor ve akışın "bu sohbete ait" olduğunu buradan anlıyor.
        const adopt = !body.chat_id && cur.token === startedOn && cur.chatId === null;
        if (adopt) {
          cur.chatId = ev.chat.id;
          history.replaceState(null, "", `#/c/${ev.chat.id}`);
        }
        upsertChat(ev.chat);
        if (adopt) emit("route-replaced", { view: "chat", chatId: ev.chat.id });
        updateComposer();
      } else if (ev.type === "thinking") {
        L.thinking += ev.content;
        queuePaint();
      } else if (ev.type === "token") {
        L.content += ev.content;
        queuePaint();
      } else if (ev.type === "done") {
        outcome = "done";
        doneEv = ev;
      } else if (ev.type === "error") {
        outcome = "error";
        errorText = ev.message;
      }
    }, controller.signal);
    if (outcome !== "done" && !errorText) errorText = "Bağlantı beklenmedik şekilde kapandı.";
  } catch (err) {
    if (err.name === "AbortError") outcome = "stopped";
    else { outcome = "error"; errorText = err.message || String(err); }
  }

  spinner.stop();
  live = null;
  paint(false);

  if (outcome === "error") {
    view.root.classList.add("is-error");
    view.body.hidden = false;
    const box = el("div", "err");
    box.textContent = "✖ " + errorText;
    view.body.appendChild(box);
    const row = el("div", "retry-row");
    const retry = actButton("↻ TEKRAR DENE", () => {
      if (live) return toast("Önce yazılan cevabın bitmesini bekle.", true);
      if (L.chatId) {
        regenerate(view.root);
      } else {
        // Sunucu sohbeti hiç oluşturmadı: mesajı yeniden gönder.
        view.root.previousElementSibling?.remove();
        view.root.remove();
        send(retryText);
      }
    });
    retry.className = "btn btn-ghost";
    row.appendChild(retry);
    view.body.appendChild(row);
    view.foot.remove();
  } else {
    view.foot.textContent = outcome === "stopped" ? "■ durduruldu" : formatStats(doneEv?.model, doneEv || {});
    if (L.content || L.thinking) addBotActions(view, L.content);
    else view.body.hidden = true;
  }

  if (doneEv?.chat) upsertChat(doneEv.chat);
  else if (L.chatId) api(`/api/chats/${L.chatId}`).then(({ messages, ...c }) => upsertChat(c)).catch(() => {});
  updateComposer();
  if (view.root.isConnected && nearBottom()) toBottom();

  if (outcome === "done" && doneEv.first_answer && S.settings.auto_title && L.chatId) {
    api(`/api/chats/${L.chatId}/title`, { method: "POST" })
      .then((c) => { upsertChat(c); emit("chat-title", c); })
      .catch(() => {});
  }
}

function send(text) {
  if (live) return toast("Önce yazılan cevabın bitmesini bekle ya da durdur.", true);
  const persona = activePersona();
  emptyBox.hidden = true;
  log.appendChild(userNode(text));
  const view = botView(persona?.name, S.settings.model);
  log.appendChild(view.root);
  toBottom();
  runStream(
    {
      chat_id: cur.chatId,
      project_id: cur.chatId ? null : cur.projectId,
      content: text,
      persona_id: persona ? persona.id : null,
    },
    view,
    text,
  );
}

function regenerate(oldNode) {
  if (live) return toast("Önce yazılan cevabın bitmesini bekle ya da durdur.", true);
  if (!cur.chatId) return;
  const persona = activePersona();
  const view = botView(persona?.name, S.settings.model);
  oldNode.replaceWith(view.root);
  toBottom();
  runStream({ chat_id: cur.chatId, regenerate: true, persona_id: persona ? persona.id : null }, view);
}

function stop() {
  if (live) live.controller.abort();
}

$("#chat-form").addEventListener("submit", (e) => {
  e.preventDefault();
  if (live) return;
  const text = input.value.trim();
  if (!text) return;
  input.value = "";
  autosize();
  send(text);
});
sendBtn.addEventListener("click", () => {
  if (sendBtn.classList.contains("is-stop")) stop();
});
input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
    e.preventDefault();
    $("#chat-form").requestSubmit();
  } else if (e.key === "Escape" && live) {
    stop();
  }
});
input.addEventListener("input", () => { autosize(); updateComposer(); });

// Kod bloklarındaki KOPYALA düğmeleri
log.addEventListener("click", (e) => {
  const b = e.target.closest("[data-copy-code]");
  if (!b) return;
  copyText(b.closest("pre").querySelector("code").textContent).then(() => toast("Kod kopyalandı"));
});

// --- kişilik seçici (sohbet kutusunun sol üstü) ------------------------------------------------

const pBtn = $("#persona-btn");
const pMenu = $("#persona-menu");

function renderPersonaButton() {
  const p = activePersona();
  $("#persona-name").textContent = p ? p.name : "kişiliksiz";
  pBtn.title = p ? `Kişilik: ${p.name}` : "Kişilik seçilmedi — sistem promptu gönderilmez";
  if (!emptyBox.hidden) renderEmpty();
}

function closePersonaMenu() {
  pMenu.hidden = true;
  pBtn.setAttribute("aria-expanded", "false");
}

function openPersonaMenu() {
  pMenu.innerHTML = '<div class="pop-title">KİŞİLİK SEÇ</div>';
  S.personas.forEach((p) => {
    const active = p.id === S.settings.active_persona_id;
    const opt = el("button", "pop-item" + (active ? " is-active" : ""));
    opt.type = "button";
    opt.setAttribute("role", "option");
    opt.setAttribute("aria-selected", String(active));
    opt.innerHTML = `<span class="n"></span><span class="p"></span>`;
    $(".n", opt).textContent = p.name;
    $(".p", opt).textContent = p.prompt.trim() || "(boş prompt)";
    opt.addEventListener("click", () => {
      closePersonaMenu();
      selectPersona(p.id);
      input.focus();
    });
    pMenu.appendChild(opt);
  });
  const manage = el("a", "pop-item is-link", '<span class="n">Kişilikleri düzenle</span>');
  manage.href = "#/settings/personas";
  manage.addEventListener("click", closePersonaMenu);
  pMenu.appendChild(manage);
  pMenu.hidden = false;
  pBtn.setAttribute("aria-expanded", "true");
  (pMenu.querySelector(".pop-item.is-active") || pMenu.querySelector(".pop-item"))?.focus();
}

export async function selectPersona(id) {
  try {
    S.settings = await api("/api/active", { method: "PUT", body: { persona_id: id } });
    emit("settings");
  } catch (err) {
    toast("Kişilik seçilemedi: " + err.message, true);
  }
}

pBtn.addEventListener("click", () => (pMenu.hidden ? openPersonaMenu() : closePersonaMenu()));
arrowNav(pMenu);
document.addEventListener("mousedown", (e) => {
  if (!pMenu.hidden && !e.target.closest(".persona-pick")) closePersonaMenu();
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !pMenu.hidden) { closePersonaMenu(); pBtn.focus(); }
});

// --- durum değişiklikleri ---------------------------------------------------------------------

on("settings", renderPersonaButton);
on("personas", renderPersonaButton);
on("models", () => { if (!emptyBox.hidden) renderEmpty(); });
on("chats", () => {
  // Sohbet başka projeye taşındıysa kutudaki proje etiketi de değişsin.
  if (cur.chatId) cur.projectId = chatById(cur.chatId)?.project_id ?? null;
  updateComposer();
});
on("projects", () => { updateComposer(); if (!emptyBox.hidden) renderEmpty(); });
on("chat-deleted", (chat) => {
  if (cur.chatId === chat.id) location.hash = chat.project_id ? `#/new/${chat.project_id}` : "#/";
});

export function focusInput() {
  input.focus();
}
