const sessionId = crypto.randomUUID();

const form = document.getElementById("chat-form");
const input = document.getElementById("chat-input");
const log = document.getElementById("chat-log");
const badges = document.getElementById("status-badges");

let socket = null;
let assistantEl = null;
let busy = false;
const toolBlocks = new Map();

function scroll() {
  log.scrollTop = log.scrollHeight;
}

function appendMessage(role, text) {
  const el = document.createElement("div");
  el.className = `msg msg-${role}`;
  el.textContent = text;
  log.appendChild(el);
  scroll();
  return el;
}

function appendNotice(text, kind = "notice") {
  const el = document.createElement("div");
  el.className = `notice notice-${kind}`;
  el.textContent = text;
  log.appendChild(el);
  scroll();
  return el;
}

/** Araç çağrısı: katlanabilir blok (spec §7.1). */
function appendToolCall(event) {
  const details = document.createElement("details");
  details.className = `tool tool-${event.risk}`;

  const summary = document.createElement("summary");
  const args = Object.entries(event.args || {})
    .map(([key, value]) => `${key}=${JSON.stringify(value)}`)
    .join(" ");
  summary.innerHTML =
    `<span class="tool-name">${event.name}</span> ` +
    `<span class="tool-args">${escapeHtml(args)}</span> ` +
    `<span class="risk risk-${event.risk}">${event.risk}</span>`;
  details.appendChild(summary);

  const body = document.createElement("pre");
  body.className = "tool-output";
  body.textContent = "çalışıyor...";
  details.appendChild(body);

  log.appendChild(details);
  toolBlocks.set(event.id, { details, body });
  scroll();
}

function completeToolCall(event) {
  const block = toolBlocks.get(event.id);
  if (!block) return;
  block.body.textContent = event.output || "(çıktı yok)";
  const summary = block.details.querySelector("summary");
  const meta = document.createElement("span");
  meta.className = event.ok ? "tool-ok" : "tool-fail";
  meta.textContent = event.ok ? ` ✓ ${event.ms}ms` : " ✗ başarısız";
  summary.appendChild(meta);
  if (!event.ok) block.details.open = true;
  scroll();
}

/** Onay diyaloğu — modal değil, akışın içinde (spec §7.1). */
function appendApproval(event) {
  const box = document.createElement("div");
  box.className = "approval";

  const isContinue = event.type === "continue_request";
  const title = document.createElement("div");
  title.className = "approval-title";
  title.textContent = isContinue ? "Devam edeyim mi?" : `Onay gerekiyor: ${event.tool}`;
  box.appendChild(title);

  if (event.summary) {
    const cmd = document.createElement("pre");
    cmd.className = "approval-command";
    cmd.textContent = event.summary;
    box.appendChild(cmd);
  }

  if (event.reason) {
    const reason = document.createElement("div");
    reason.className = "approval-reason";
    reason.textContent = event.reason;
    box.appendChild(reason);
  }

  if (event.paths && event.paths.length) {
    const paths = document.createElement("div");
    paths.className = "approval-paths";
    paths.textContent = `Etkilenen yollar: ${event.paths.join(", ")}`;
    box.appendChild(paths);
  }

  if (event.dry_run) {
    const note = document.createElement("div");
    note.className = "approval-dry";
    note.textContent = "dry-run açık — onaylasan bile yazma işlemi yapılmaz, sadece raporlanır.";
    box.appendChild(note);
  }

  const actions = document.createElement("div");
  actions.className = "approval-actions";
  const yes = document.createElement("button");
  yes.textContent = isContinue ? "Devam et" : "Onayla";
  yes.className = "btn-approve";
  const no = document.createElement("button");
  no.textContent = isContinue ? "Dur" : "Reddet";
  no.className = "btn-deny";
  actions.append(yes, no);
  box.appendChild(actions);

  const respond = (approved) => {
    socket.send(
      JSON.stringify({
        type: isContinue ? "continue_response" : "approval_response",
        id: event.id,
        approved,
      })
    );
    actions.remove();
    const verdict = document.createElement("div");
    verdict.className = approved ? "approval-yes" : "approval-no";
    verdict.textContent = approved ? "→ onaylandı" : "→ reddedildi";
    box.appendChild(verdict);
  };

  yes.addEventListener("click", () => respond(true));
  no.addEventListener("click", () => respond(false));

  log.appendChild(box);
  scroll();
  yes.focus();
  return box;
}

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}

function setBusy(value) {
  busy = value;
  input.disabled = value;
  if (!value) input.focus();
}

function connect() {
  const protocol = location.protocol === "https:" ? "wss" : "ws";
  socket = new WebSocket(`${protocol}://${location.host}/ws/chat`);

  socket.addEventListener("message", (raw) => {
    const event = JSON.parse(raw.data);
    switch (event.type) {
      case "token":
        if (!assistantEl) assistantEl = appendMessage("assistant", "");
        assistantEl.textContent += event.content;
        scroll();
        break;
      case "tool_call":
        assistantEl = null;
        appendToolCall(event);
        break;
      case "tool_result":
        completeToolCall(event);
        break;
      case "approval_request":
      case "continue_request":
        appendApproval(event);
        break;
      case "approval_timeout":
        appendNotice(event.message, "warn");
        break;
      case "stopped":
        appendNotice(event.message, "warn");
        setBusy(false);
        break;
      case "error":
        appendNotice(event.message, "error");
        setBusy(false);
        break;
      case "done":
        assistantEl = null;
        setBusy(false);
        break;
      default:
        break;
    }
  });

  socket.addEventListener("close", () => {
    appendNotice("Bağlantı kapandı, yeniden bağlanılıyor...", "warn");
    setBusy(false);
    setTimeout(connect, 1500);
  });
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  const message = input.value.trim();
  if (!message || busy) return;
  if (!socket || socket.readyState !== WebSocket.OPEN) {
    appendNotice("Bağlantı yok.", "error");
    return;
  }

  input.value = "";
  setBusy(true);
  appendMessage("user", message);
  assistantEl = null;
  socket.send(JSON.stringify({ type: "user_message", content: message, session_id: sessionId }));
});

async function loadConfig() {
  try {
    const res = await fetch("/api/config");
    const config = await res.json();
    badges.innerHTML = "";
    const items = [
      config.model,
      config.dry_run ? "dry-run açık" : "dry-run KAPALI",
      config.workspace_root,
    ];
    items.forEach((text, index) => {
      const badge = document.createElement("span");
      badge.className = index === 1 && !config.dry_run ? "badge badge-warn" : "badge";
      badge.textContent = text;
      badges.appendChild(badge);
    });
  } catch (err) {
    /* başlık rozetleri kritik değil */
  }
}

loadConfig();
connect();
