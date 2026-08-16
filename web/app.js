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

/** Sağlayıcı devri rozeti: "groq → openrouter, sebep: ..." (spec §6.1). */
function appendModelSwitch(event) {
  if (!event.previous) return; // ilk seçim rozet gerektirmiyor
  const el = document.createElement("div");
  el.className = event.forced ? "switch switch-forced" : "switch";
  const reasons = (event.rejected || [])
    .filter((item) => item.provider === event.previous)
    .map((item) => item.reason);
  const why = reasons.length ? reasons[0] : event.reason;
  el.textContent = `${event.previous} → ${event.provider} · sebep: ${why}`;
  log.appendChild(el);
  scroll();
}

/** Asistan mesajının altındaki meta satırı: model · sağlayıcı · süre · token. */
function appendMeta(event) {
  if (!assistantEl) return;
  const parts = [event.model, event.provider].filter(Boolean);
  if (event.ms) parts.push(`${event.ms}ms`);
  if (event.tokens) parts.push(`${event.tokens} token`);
  if (!parts.length) return;
  const el = document.createElement("div");
  el.className = "msg-meta";
  el.textContent = parts.join(" · ");
  log.appendChild(el);
  scroll();
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
      case "model_switch":
        appendModelSwitch(event);
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
        appendMeta(event);
        assistantEl = null;
        setBusy(false);
        loadQuota();
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

// --- kota paneli (spec §7.2) ---

const quotaPanel = document.getElementById("quota-panel");
const quotaCards = document.getElementById("quota-cards");

function formatCountdown(seconds) {
  if (seconds <= 0) return "";
  if (seconds < 60) return `${seconds} sn`;
  if (seconds < 3600) return `${Math.round(seconds / 60)} dk`;
  return `${Math.round(seconds / 3600)} sa`;
}

function resetCountdown(isoString) {
  if (!isoString) return "";
  const seconds = Math.round((new Date(isoString) - new Date()) / 1000);
  return seconds > 0 ? `sıfırlanma: ${formatCountdown(seconds)}` : "";
}

function renderWindow(win) {
  const row = document.createElement("div");
  row.className = "quota-window";

  const label = document.createElement("div");
  label.className = "quota-window-label";
  const scope = win.window === "day" ? "günlük" : win.window === "minute" ? "dakikalık" : win.window;
  if (!win.known) {
    // Limit bilinmiyorsa yüzde göstermek yanıltıcı olur — sadece kullanım.
    label.textContent = `${scope}: ${win.requests} istek (limit bilinmiyor)`;
    row.appendChild(label);
    return row;
  }

  const used = win.max_requests
    ? `${win.requests}/${win.max_requests} istek`
    : `${win.tokens}/${win.max_tokens} token`;
  label.innerHTML =
    `<span>${scope}: ${used}</span>` +
    `<span class="quota-source quota-source-${win.source}">` +
    `${win.source === "live" ? "canlı" : "tahmini"}</span>`;
  row.appendChild(label);

  const bar = document.createElement("div");
  bar.className = "quota-bar";
  const fill = document.createElement("div");
  const usedRatio = 1 - win.free_ratio;
  fill.className = usedRatio > 0.9 ? "quota-fill quota-fill-danger" : usedRatio > 0.7 ? "quota-fill quota-fill-warn" : "quota-fill";
  fill.style.width = `${Math.min(100, Math.round(usedRatio * 100))}%`;
  bar.appendChild(fill);
  row.appendChild(bar);

  const reset = resetCountdown(win.resets_at);
  if (reset) {
    const note = document.createElement("div");
    note.className = "quota-reset";
    note.textContent = reset;
    row.appendChild(note);
  }
  return row;
}

function renderProvider(provider) {
  const card = document.createElement("div");
  card.className = `quota-card quota-card-${provider.available ? "ok" : "off"}`;

  const head = document.createElement("div");
  head.className = "quota-card-head";
  const name = document.createElement("span");
  name.className = "quota-name";
  name.textContent = provider.provider;
  head.appendChild(name);

  const status = document.createElement("span");
  status.className = `quota-status quota-status-${provider.available ? "ok" : "off"}`;
  if (!provider.configured) status.textContent = "anahtar yok";
  else if (provider.cooldown_seconds > 0) status.textContent = `cooldown ${formatCountdown(provider.cooldown_seconds)}`;
  else if (provider.health === "down") status.textContent = "kapalı";
  else status.textContent = provider.available ? "hazır" : "elendi";
  head.appendChild(status);
  card.appendChild(head);

  const model = document.createElement("div");
  model.className = "quota-model";
  model.textContent = provider.model;
  card.appendChild(model);

  provider.windows.forEach((win) => card.appendChild(renderWindow(win)));

  if (!provider.available && provider.reason) {
    const reason = document.createElement("div");
    reason.className = "quota-reason";
    reason.textContent = provider.reason;
    card.appendChild(reason);
  }

  if (provider.configured) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "quota-action";
    const turningOff = provider.health !== "down";
    button.textContent = turningOff ? "devre dışı bırak" : "geri aç";
    button.addEventListener("click", async () => {
      button.disabled = true;
      await fetch(`/api/quota/${provider.provider}/${turningOff ? "disable" : "enable"}`, {
        method: "POST",
      });
      loadQuota();
    });
    card.appendChild(button);
  }

  return card;
}

async function loadQuota({ probe = false } = {}) {
  if (quotaPanel.hidden && !probe) return; // panel kapalıyken boşuna sorgulama
  try {
    const res = await fetch(`/api/quota${probe ? "?probe=true" : ""}`);
    const data = await res.json();
    quotaCards.innerHTML = "";
    data.providers.forEach((provider) => quotaCards.appendChild(renderProvider(provider)));
  } catch (err) {
    quotaCards.textContent = "Kota bilgisi alınamadı.";
  }
}

document.getElementById("quota-toggle").addEventListener("click", () => {
  quotaPanel.hidden = !quotaPanel.hidden;
  if (!quotaPanel.hidden) loadQuota();
});

document.getElementById("quota-probe").addEventListener("click", () => loadQuota({ probe: true }));

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
