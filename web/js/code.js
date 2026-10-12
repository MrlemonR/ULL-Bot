/* Code modu: seçilen klasörde çalışan kod ajanı (Claude Code benzeri). */
import { api, stream } from "./api.js";
import { createSpinner } from "./spinner.js";
import { S, codeSessionById, emit, upsertCodeSession } from "./store.js";
import { arrowNav, confirmDialog, openModal, promptDialog, toast } from "./ui.js";
import { $, copyText, el, esc, renderMd } from "./util.js";

const bar = $("#code-bar");
const log = $("#code-log");
const input = $("#code-input");
const sendBtn = $("#code-send");
const jumpBtn = $("#code-jump");
const permBtn = $("#perm-btn");
const permMenu = $("#perm-menu");

let sessionId = null;
let token = 0;
let live = null; // yayındaki iş: {sessionId, controller, nodes: [...], spinner}

const MODES = {
  ask: ["◆", "SOR", "Dosya düzenlemeden ve komut çalıştırmadan önce izin ister."],
  edits: ["✎", "DÜZENLEMELER SERBEST", "Dosya düzenlemeleri sorulmaz; komutlar sorulur."],
  all: ["⚡", "TAM YETKİ", "Hiçbir şey sorulmaz. Yalnızca güvendiğin klasörlerde kullan."],
};
const TOOL_LABEL = {
  list_files: "List", read_file: "Read", search: "Search",
  edit_file: "Update", write_file: "Write", run_command: "Bash",
};

const folderName = (cwd) => cwd.split(/[\\/]/).filter(Boolean).pop() || cwd;

// --- kaydırma (sohbetteki gibi: yukarı çıkınca takip bırakılır) ---------------------------

let follow = true;
let autoTop = 0;
const distance = () => log.scrollHeight - log.scrollTop - log.clientHeight;
function toBottom() {
  log.scrollTop = log.scrollHeight;
  autoTop = log.scrollTop;
  follow = true;
  jumpBtn.hidden = true;
}
log.addEventListener("wheel", (e) => { if (e.deltaY < 0) { follow = false; jumpBtn.hidden = distance() < 40; } }, { passive: true });
log.addEventListener("scroll", () => {
  if (log.scrollTop < autoTop - 2) follow = false;
  if (distance() < 24) { follow = true; autoTop = log.scrollTop; jumpBtn.hidden = true; } else if (!follow) jumpBtn.hidden = false;
}, { passive: true });
jumpBtn.addEventListener("click", toBottom);
const stick = () => { if (follow) toBottom(); };

// --- görünüm ---------------------------------------------------------------------------------

export function codeCrumb(crumb) {
  const sess = codeSessionById(sessionId);
  crumb.appendChild(el("span", "", "&lt;/&gt;"));
  if (!sess) {
    crumb.appendChild(el("span", "cur", "CODE"));
    return;
  }
  crumb.appendChild(el("span", "", esc(folderName(sess.cwd))));
  crumb.appendChild(el("span", "sep", "/"));
  const t = el("a", "cur", esc(sess.title));
  t.href = "#";
  t.title = "Yeniden adlandırmak için tıkla";
  t.addEventListener("click", (e) => { e.preventDefault(); renameSession(sess); });
  crumb.appendChild(t);
}

export function leaveCode() {
  token++;
}

export async function showCode(id) {
  const my = ++token;
  sessionId = id || null;
  log.innerHTML = "";
  bar.innerHTML = "";
  follow = true;
  jumpBtn.hidden = true;
  updateComposer();
  if (!sessionId) return renderStart();

  let data;
  try {
    data = await api(`/api/code/sessions/${sessionId}`);
  } catch (err) {
    if (my === token) log.appendChild(el("div", "code-empty", `<h3>OTURUM AÇILAMADI</h3>${esc(err.message)}`));
    return;
  }
  if (my !== token) return;
  upsertCodeSession({ id: data.id, title: data.title, cwd: data.cwd, mode: data.mode, updated_at: data.updated_at });
  renderBar(data);
  if (!data.exists) log.appendChild(el("div", "err", `✖ Klasör artık yok: ${esc(data.cwd)}`));
  if (!data.messages.length && !(live && live.sessionId === sessionId)) {
    log.appendChild(el("div", "code-empty",
      `<h3>${esc(folderName(data.cwd))}</h3><p class="muted">${esc(data.cwd)}</p>` +
      "<p>Ne yapmamı istediğini yaz. Önce dosyalara bakar, sonra değişiklik yaparım; izin moduna göre her düzenlemeden önce sorarım.</p>" +
      "<ul><li>bu proje ne yapıyor, kısaca anlat</li><li>README'ye kurulum bölümü ekle</li><li>testleri çalıştır, hata varsa düzelt</li></ul>"));
  }
  data.messages.forEach((m) => renderStored(m));
  if (live && live.sessionId === sessionId) live.nodes.forEach((n) => log.appendChild(n));
  updateComposer();
  toBottom();
  input.focus();
}

function renderStart() {
  const recent = [...new Set(S.codeSessions.map((x) => x.cwd))].slice(0, 6);
  const box = el("div", "code-empty");
  box.innerHTML =
    "<h3>CODE</h3>" +
    "<p>Bir klasör seç: ULL-Bot oradaki dosyaları okur, düzenler ve komut çalıştırır — Claude Code gibi, ama sağ üstte seçtiğin yerel modelle.</p>" +
    '<p class="muted">Model araç çağırmayı (tools) desteklemeli: qwen2.5-coder, qwen3, qwen3-coder, llama3.1, gpt-oss…</p>';
  const pick = el("button", "btn btn-solid", "KLASÖR SEÇ");
  pick.type = "button";
  pick.style.marginTop = "0.8rem";
  pick.addEventListener("click", () => newCodeSession());
  box.appendChild(pick);
  if (recent.length) {
    box.appendChild(el("p", "muted", "Son klasörler:"));
    const list = el("div", "folder-list");
    recent.forEach((cwd) => {
      const b = el("button", "", `${esc(folderName(cwd))} <span class="muted">— ${esc(cwd)}</span>`);
      b.type = "button";
      b.addEventListener("click", () => newCodeSession(cwd));
      list.appendChild(b);
    });
    box.appendChild(list);
  }
  log.appendChild(box);
}

function renderBar(sess) {
  bar.innerHTML = `<span class="path" title="${esc(sess.cwd)}">📁 <b>${esc(folderName(sess.cwd))}</b> <span class="muted">${esc(sess.cwd)}</span></span>`;
  const claude = el("button", "btn btn-ghost", "CLAUDE CODE İLE AÇ ↗");
  claude.type = "button";
  claude.title = "Gerçek Claude Code'u bu klasörde, seçili yerel modelle bir terminalde açar";
  claude.addEventListener("click", () => openClaude(sess));
  bar.appendChild(claude);
  $("#code-folder").textContent = `📁 ${folderName(sess.cwd)}`;
  paintMode(sess.mode);
}

// --- mesaj düğümleri -----------------------------------------------------------------------

function textNode(content, stopped) {
  const n = el("div", "c-text" + (stopped ? " is-stopped" : ""));
  const inner = el("div");
  inner.innerHTML = renderMd(content) + (stopped ? '<div class="muted">■ durduruldu</div>' : "");
  n.appendChild(inner);
  return n;
}

function diffHtml(diff) {
  return diff.split("\n").filter((l, i, a) => i < a.length - 1 || l).map((l) => {
    const cls = l.startsWith("+++") || l.startsWith("---") ? "meta" : l.startsWith("@@") ? "hunk" : l.startsWith("+") ? "add" : l.startsWith("-") ? "del" : "";
    return `<span class="${cls}">${esc(l) || " "}</span>`;
  }).join("");
}

function summary(ui) {
  if (ui.denied) return "reddedildi";
  if (!ui.ok) return "hata";
  if (ui.diff) {
    const lines = ui.diff.split("\n");
    const a = lines.filter((l) => l.startsWith("+") && !l.startsWith("+++")).length;
    const d = lines.filter((l) => l.startsWith("-") && !l.startsWith("---")).length;
    return `${ui.new_file ? "yeni · " : ""}+${a} −${d}`;
  }
  if (ui.name === "run_command") return `exit ${ui.exit_code ?? "?"}`;
  const n = (ui.display || "").split("\n").length;
  if (ui.name === "read_file") return `${n} satır`;
  if (ui.name === "search") return ui.display === "Eşleşme yok." ? "eşleşme yok" : `${n} eşleşme`;
  if (ui.name === "list_files") return `${n - 1} öğe`;
  return "";
}

function toolCard({ id, name, label }) {
  const card = el("div", "c-tool");
  card.dataset.id = id || "";
  card.innerHTML =
    `<button type="button" class="c-tool-head"><span class="st run">●</span><span class="tn">${esc(TOOL_LABEL[name] || name)}</span>` +
    `<span class="ta">${esc(label || "")}</span><span class="sum">…</span></button><div class="c-tool-body" hidden></div>`;
  card.querySelector(".c-tool-head").addEventListener("click", () => {
    const b = card.querySelector(".c-tool-body");
    if (b.innerHTML) b.hidden = !b.hidden;
  });
  return card;
}

function fillTool(card, ui) {
  const st = card.querySelector(".st");
  st.className = "st " + (ui.ok ? "ok" : "err");
  st.textContent = ui.ok ? "✓" : ui.denied ? "⊘" : "✖";
  card.querySelector(".sum").textContent = summary(ui);
  const body = card.querySelector(".c-tool-body");
  if (ui.diff && ui.ok) {
    body.innerHTML = `<pre class="diff">${diffHtml(ui.diff)}</pre>`;
    body.hidden = false; // düzenlemeler Claude Code'daki gibi açık gelir
  } else {
    body.innerHTML = `<pre>${esc(ui.display || "")}</pre>`;
    const lines = (ui.display || "").split("\n").length;
    body.hidden = !(ui.name === "run_command" ? lines <= 14 : !ui.ok);
  }
}

function renderStored(m) {
  if (m.role === "user") {
    const n = el("div", "c-user");
    n.textContent = m.content;
    log.appendChild(n);
  } else if (m.role === "assistant") {
    if (m.content && m.content.trim()) log.appendChild(textNode(m.content, m.stopped));
  } else if (m.role === "tool" && m.ui) {
    const card = toolCard(m.ui);
    fillTool(card, m.ui);
    log.appendChild(card);
  }
}

// --- gönderme / akış ----------------------------------------------------------------------------

function updateComposer() {
  const mine = live && live.sessionId === sessionId;
  const other = live && !mine;
  sendBtn.classList.toggle("is-stop", Boolean(mine));
  sendBtn.textContent = mine ? "■ DURDUR" : "GÖNDER ▶";
  sendBtn.type = mine ? "button" : "submit";
  sendBtn.disabled = !mine && (!sessionId || other || !input.value.trim());
  input.disabled = !sessionId;
  input.placeholder = sessionId
    ? "Ne yapayım? Örn: README'ye kurulum bölümü ekle · testleri çalıştır ve hataları düzelt"
    : "Önce bir klasör seç.";
  $("#code-hint").textContent = mine ? "ESC ▸ durdur · 1/2/3 ▸ izin" : other ? "başka bir oturum çalışıyor…" : "ENTER ▸ gönder · SHIFT+ENTER ▸ yeni satır";
  permBtn.parentElement.hidden = !sessionId;
  $("#code-folder").hidden = !sessionId;
}

function append(node) {
  if (!live) return log.appendChild(node);
  live.nodes.splice(live.nodes.length - 1, 0, node); // spinner hep sonda
  if (live.sessionId === sessionId) log.insertBefore(node, live.spinner.node);
  stick();
  return node;
}

async function send(text) {
  if (live) return toast("Önce çalışan işin bitmesini bekle ya da durdur.", true);
  const sid = sessionId;
  log.querySelector(".code-empty")?.remove();
  const user = el("div", "c-user");
  user.textContent = text;
  log.appendChild(user);
  const spinner = createSpinner({ words: S.settings.appearance?.spinner_words !== false });
  const controller = new AbortController();
  live = { sessionId: sid, controller, nodes: [spinner.node], spinner };
  log.appendChild(spinner.node);
  toBottom();
  updateComposer();

  let textEl = null;
  let content = "";
  let queued = false;
  const cards = new Map();
  const paint = () => {
    queued = false;
    if (textEl) textEl.firstChild.innerHTML = renderMd(content) + '<span class="cursor">█</span>';
    stick();
  };
  const finishText = () => {
    if (textEl) {
      if (content.trim()) textEl.firstChild.innerHTML = renderMd(content);
      else { textEl.remove(); live.nodes = live.nodes.filter((n) => n !== textEl); }
    }
    textEl = null;
    content = "";
  };

  try {
    await stream(`/api/code/sessions/${sid}/send`, { content: text }, (ev) => {
      if (ev.type === "start") {
        upsertCodeSession(ev.session);
      } else if (ev.type === "token") {
        if (!textEl) textEl = append(textNode("", false));
        content += ev.content;
        if (!queued) { queued = true; requestAnimationFrame(paint); }
      } else if (ev.type === "retract") {
        content = "";
        finishText();
      } else if (ev.type === "turn_end") {
        finishText();
      } else if (ev.type === "tool_call") {
        const card = append(toolCard(ev));
        cards.set(ev.id, card);
        spinner.setLabel(null);
      } else if (ev.type === "tool_running") {
        const card = cards.get(ev.id);
        if (card) card.querySelector(".sum").textContent = "çalışıyor…";
      } else if (ev.type === "permission") {
        append(permissionCard(ev));
      } else if (ev.type === "tool_result") {
        const card = cards.get(ev.call_id);
        if (card) fillTool(card, ev);
        log.querySelector(`.c-perm[data-call="${ev.call_id}"]`)?.remove();
        stick();
      } else if (ev.type === "error") {
        const box = el("div", "err");
        box.textContent = "✖ " + ev.message;
        append(box);
      } else if (ev.type === "done") {
        upsertCodeSession(ev.session);
      }
    }, controller.signal);
  } catch (err) {
    if (err.name !== "AbortError") {
      const box = el("div", "err");
      box.textContent = "✖ " + (err.message || err);
      append(box);
    } else {
      append(el("div", "muted", "■ durduruldu"));
    }
  }
  finishText();
  spinner.stop();
  live.nodes.forEach((n) => n.querySelector?.(".st.run") && (n.querySelector(".st.run").textContent = "■"));
  live = null;
  log.querySelectorAll(".c-perm").forEach((n) => n.remove());
  updateComposer();
  stick();
  emit("code-sessions");
}

function permissionCard(ev) {
  const card = el("div", "c-perm");
  card.dataset.call = ev.call_id;
  const what = ev.kind === "bash"
    ? `Bu komutu çalıştırayım mı? <b>${esc(ev.label)}</b>`
    : `${ev.new_file ? "Bu dosyayı oluşturayım mı?" : "Bu dosyayı düzenleyeyim mi?"} <b>${esc(ev.label)}</b>`;
  card.innerHTML = `<div class="q">${what}</div>` +
    (ev.diff ? `<div class="c-tool-body"><pre class="diff">${diffHtml(ev.diff)}</pre></div>` : "") +
    `<div class="acts"></div>`;
  const acts = card.querySelector(".acts");
  const answer = async (decision) => {
    card.querySelectorAll("button").forEach((b) => { b.disabled = true; });
    try {
      await api("/api/code/permission", { method: "POST", body: { id: ev.id, decision } });
      card.remove();
    } catch (err) {
      toast(err.message, true);
    }
  };
  const always = ev.kind === "bash" ? "EVET, KOMUTLARA HEP İZİN VER" : "EVET, DÜZENLEMELERE HEP İZİN VER";
  [["1", "EVET", "btn-solid", "allow"], ["2", always, "btn", "always"], ["3", "HAYIR", "btn-danger", "deny"]].forEach(([k, label, cls, d]) => {
    const b = el("button", `btn ${cls}`, `<span class="k">${k}</span>${label}`);
    b.type = "button";
    b.dataset.key = k;
    b.addEventListener("click", () => answer(d));
    acts.appendChild(b);
  });
  requestAnimationFrame(() => acts.firstChild?.focus());
  return card;
}

// 1/2/3 kısayolları: açık izin sorusu varken (yazı kutusu dışında)
document.addEventListener("keydown", (e) => {
  if (S.mode !== "code" || !["1", "2", "3"].includes(e.key) || e.target === input || e.ctrlKey || e.metaKey) return;
  const card = log.querySelector(".c-perm");
  if (!card) return;
  e.preventDefault();
  card.querySelector(`button[data-key="${e.key}"]`)?.click();
});

$("#code-form").addEventListener("submit", (e) => {
  e.preventDefault();
  if (live || !sessionId) return;
  const text = input.value.trim();
  if (!text) return;
  input.value = "";
  input.style.height = "auto";
  send(text);
});
sendBtn.addEventListener("click", () => { if (sendBtn.classList.contains("is-stop")) live?.controller.abort(); });
input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
    e.preventDefault();
    $("#code-form").requestSubmit();
  } else if (e.key === "Escape" && live) {
    live.controller.abort();
  }
});
input.addEventListener("input", () => {
  input.style.height = "auto";
  input.style.height = input.scrollHeight + "px";
  updateComposer();
});
log.addEventListener("click", (e) => {
  const b = e.target.closest("[data-copy-code]");
  if (b) copyText(b.closest("pre").querySelector("code").textContent).then(() => toast("Kod kopyalandı"));
});

// --- izin modu --------------------------------------------------------------------------------

function paintMode(mode) {
  const [glyph, name] = MODES[mode] || MODES.ask;
  permBtn.querySelector(".glyph").textContent = glyph;
  $("#perm-name").textContent = name;
  permBtn.title = (MODES[mode] || MODES.ask)[2];
}

function closePerm() {
  permMenu.hidden = true;
  permBtn.setAttribute("aria-expanded", "false");
}

permBtn.addEventListener("click", () => {
  if (!permMenu.hidden) return closePerm();
  const sess = codeSessionById(sessionId);
  permMenu.innerHTML = '<div class="pop-title">İZİN MODU</div>';
  Object.entries(MODES).forEach(([key, [glyph, name, desc]]) => {
    const b = el("button", "pop-item" + (sess?.mode === key ? " is-active" : ""), `<span class="n">${glyph} ${name}</span><span class="p">${esc(desc)}</span>`);
    b.type = "button";
    b.addEventListener("click", async () => {
      closePerm();
      try {
        upsertCodeSession(await api(`/api/code/sessions/${sessionId}`, { method: "PATCH", body: { mode: key } }));
        paintMode(key);
        toast(`İzin modu: ${name}`);
      } catch (err) {
        toast(err.message, true);
      }
      input.focus();
    });
    permMenu.appendChild(b);
  });
  permMenu.hidden = false;
  permBtn.setAttribute("aria-expanded", "true");
  (permMenu.querySelector(".is-active") || permMenu.querySelector(".pop-item"))?.focus();
});
arrowNav(permMenu);
document.addEventListener("mousedown", (e) => { if (!permMenu.hidden && !e.target.closest(".persona-pick")) closePerm(); });

// --- oturum işlemleri -------------------------------------------------------------------------------

export async function newCodeSession(cwd) {
  if (!cwd) cwd = await pickFolder();
  if (!cwd) return;
  try {
    const sess = await api("/api/code/sessions", { method: "POST", body: { cwd } });
    upsertCodeSession(sess);
    location.hash = `#/code/${sess.id}`;
  } catch (err) {
    toast("Oturum açılamadı: " + err.message, true);
  }
}

async function renameSession(sess) {
  const title = await promptDialog({ title: "Oturumu yeniden adlandır", value: sess.title });
  if (!title || title === sess.title) return;
  try {
    upsertCodeSession(await api(`/api/code/sessions/${sess.id}`, { method: "PATCH", body: { title } }));
  } catch (err) {
    toast(err.message, true);
  }
}

async function deleteSession(sess) {
  const ok = await confirmDialog({ title: "Oturumu sil", text: `"${sess.title}" oturumu silinsin mi? Klasördeki dosyalara dokunulmaz.` });
  if (!ok) return;
  try {
    await api(`/api/code/sessions/${sess.id}`, { method: "DELETE" });
    S.codeSessions = S.codeSessions.filter((x) => x.id !== sess.id);
    emit("code-sessions");
    if (sessionId === sess.id) location.hash = "#/code";
  } catch (err) {
    toast(err.message, true);
  }
}

export function codeMenuItems(sess) {
  return [
    { label: "Bu klasörde yeni oturum", onClick: () => newCodeSession(sess.cwd) },
    { label: "Yeniden adlandır", onClick: () => renameSession(sess) },
    { label: "Sil", danger: true, onClick: () => deleteSession(sess) },
  ];
}

async function pickFolder() {
  let state;
  try {
    const last = S.codeSessions[0]?.cwd;
    state = await api(`/api/code/browse${last ? `?path=${encodeURIComponent(last)}` : ""}`);
  } catch (err) {
    toast(err.message, true);
    return null;
  }
  const wrap = el("div", "folder-pick");
  wrap.style.display = "contents";
  const pathIn = el("input");
  pathIn.spellcheck = false;
  const marks = el("div", "marks");
  const list = el("div", "folder-list");
  wrap.append(pathIn, marks, list);

  const go = async (path) => {
    try {
      state = await api(`/api/code/browse?path=${encodeURIComponent(path)}`);
    } catch (err) {
      return toast(err.message, true);
    }
    paint();
  };
  const paint = () => {
    pathIn.value = state.path;
    marks.textContent = state.markers.length ? `bu klasörde: ${state.markers.join(" · ")}` : "";
    list.innerHTML = "";
    if (state.parent) {
      const up = el("button", "", "↑ .. (üst klasör)");
      up.type = "button";
      up.addEventListener("click", () => go(state.parent));
      list.appendChild(up);
    }
    state.dirs.forEach((d) => {
      const b = el("button", "", `📁 ${esc(d)}`);
      b.type = "button";
      b.addEventListener("click", () => go(`${state.path}/${d}`));
      list.appendChild(b);
    });
    if (!state.dirs.length) list.appendChild(el("div", "pop-empty", "Alt klasör yok."));
  };
  pathIn.addEventListener("keydown", (e) => {
    // Enter = o klasöre git (pencerenin genel "Enter = onayla" davranışını engelle).
    if (e.key === "Enter") { e.preventDefault(); e.stopImmediatePropagation(); go(pathIn.value.trim()); }
  });
  paint();
  return openModal({
    title: "Klasör seç",
    wide: true,
    body: wrap,
    buttons: [
      { label: "VAZGEÇ", cls: "btn-ghost", value: () => null },
      { label: "BU KLASÖRDE BAŞLA", cls: "btn-solid", value: () => state.path },
    ],
  });
}

// --- Claude Code ------------------------------------------------------------------------------------

async function openClaude(sess) {
  let res;
  try {
    res = await api(`/api/code/sessions/${sess.id}/claude`, { method: "POST" });
  } catch (err) {
    return toast(err.message, true);
  }
  if (res.ok) {
    toast("Claude Code terminalde açıldı");
    return;
  }
  let info = {};
  try { info = await api("/api/code/claude"); } catch { /* yok say */ }
  const box = el("div", "claude-box");
  box.style.display = "contents";
  const win = navigator.platform.toLowerCase().startsWith("win");
  box.innerHTML =
    `<div class="status is-bad">${esc(res.error || "Açılamadı")}</div>` +
    (!res.found ? `<div>1) Claude Code'u kur:<pre>${esc(win ? res.install.windows : res.install.posix)}</pre><span class="muted">ya da: ${esc(res.install.npm)}</span></div>` : "") +
    `<div>${res.found ? "" : "2) "}Bu klasörde, yerel modelle başlatmak için:<pre id="claude-cmd">${esc(win ? res.commands.powershell : res.commands.posix)}</pre></div>` +
    `<div class="muted">Ollama ${esc(info.ollama_version || "?")} — Claude Code için Ollama 0.14+ (Anthropic API desteği) ve geniş bağlam penceresi gerekir; ` +
    `Ollama ayarlarından bağlam uzunluğunu 32K–64K yap. Araç destekli güçlü bir kod modeli seç (ör. qwen3-coder, gpt-oss:20b).</div>`;
  await openModal({
    title: "Claude Code",
    wide: true,
    body: box,
    buttons: [
      { label: "KOMUTU KOPYALA", cls: "btn-ghost", left: true, action: () => copyText($("#claude-cmd").textContent).then(() => toast("Kopyalandı")) },
      { label: "TAMAM", cls: "btn-solid", value: () => true },
    ],
  });
}
