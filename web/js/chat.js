/* Sohbet: tek WebSocket, birden fazla görünüm.
 *
 * `ChatCore` sokete sahip olan tek nesne. Sohbet paneli, mail dock'u ve
 * takvim dock'u aynı çekirdeği paylaşıyor — yani üçü de AYNI oturumu
 * gösteriyor. Mail panelinde "bunu özetle" deyip Sohbet'e geçtiğinde
 * konuşmanın devamını orada bulman bunun sonucu.
 *
 * Protokol referansı: FAZ7_TESLIM.md §2. Oradaki davranış kurallarından
 * ikisi burada özellikle önemli ve kolay kaçırılır:
 *
 *  - §4.5 "Aynı anda tek tur": soket meşgulken ikinci `user_message` `error`
 *    döner. Bu yüzden `busy` bayrağı var ve `done`/`stopped`/`error`
 *    gelmeden gönderim açılmıyor.
 *  - §4.7 "Aynı adımda `model_switch` gelirse token arabelleğini sıfırla":
 *    bir sağlayıcı yayına başlayıp yarıda düşebiliyor. Sunucu yarım metni
 *    zaten atıyor; UI da atmazsa kullanıcı yarım cümleyle yeni cevabı
 *    birleşik okur. `resetStream()` tam olarak bunun için.
 */

import { el, escapeHtml, markdown } from "./util.js";

const RISK_LABEL = { safe: "güvenli", confirm: "onay ister", blocked: "engellendi" };
const RISK_CLASS = { safe: "tag-ok", confirm: "tag-warn", blocked: "tag-danger" };

export class ChatCore {
  constructor() {
    this.socket = null;
    this.sessionId = null;
    this.busy = false;
    this.handlers = { event: [], state: [], sent: [] };
    this.reconnectDelay = 700;
    this.shouldReconnect = true;
  }

  on(kind, handler) {
    this.handlers[kind].push(handler);
    return this;
  }

  _emit(kind, payload) {
    for (const handler of this.handlers[kind]) {
      try {
        handler(payload);
      } catch (error) {
        console.error("chat handler hatası", error);
      }
    }
  }

  connect() {
    const protocol = location.protocol === "https:" ? "wss" : "ws";
    this.socket = new WebSocket(`${protocol}://${location.host}/ws/chat`);

    this.socket.onopen = () => {
      this.reconnectDelay = 700;
      this._emit("state", { connection: "open", busy: this.busy });
    };

    this.socket.onmessage = (message) => {
      let event;
      try {
        event = JSON.parse(message.data);
      } catch {
        return;
      }
      if (event.type === "session") this.sessionId = event.session_id;
      if (["done", "stopped", "error"].includes(event.type)) this._setBusy(false);
      this._emit("event", event);
    };

    this.socket.onclose = () => {
      this._setBusy(false);
      this._emit("state", { connection: "closed", busy: false });
      if (!this.shouldReconnect) return;
      // Uygulama kapanırken de tetikleniyor; zararsız, pencere zaten gidiyor.
      setTimeout(() => this.connect(), this.reconnectDelay);
      this.reconnectDelay = Math.min(this.reconnectDelay * 1.7, 8000);
    };

    this.socket.onerror = () => this._emit("state", { connection: "error", busy: this.busy });
  }

  _setBusy(value) {
    if (this.busy === value) return;
    this.busy = value;
    this._emit("state", { connection: this.isOpen ? "open" : "closed", busy: value });
  }

  get isOpen() {
    return this.socket && this.socket.readyState === WebSocket.OPEN;
  }

  /** Yeni bir konuşma başlat (mevcut soketi koruyarak). */
  newSession() {
    this.sessionId = null;
    this._emit("event", { type: "__reset__" });
  }

  /**
   * Mesaj gönder. `context` verilirse mesajın başına GÖRÜNÜR bir bağlam
   * satırı eklenir — kullanıcı ne gönderildiğini birebir görsün diye
   * (gizli prompt enjeksiyonu yapmıyoruz).
   */
  send(text, context = null) {
    const trimmed = String(text || "").trim();
    if (!trimmed) return false;
    if (!this.isOpen) return { error: "Bağlantı yok — yeniden bağlanılıyor." };
    if (this.busy) return { error: "Önceki istek hâlâ çalışıyor." };

    const content = context ? `[Bağlam: ${context.label}]\n${trimmed}` : trimmed;
    this._setBusy(true);
    this._emit("sent", { content, context });
    this.socket.send(
      JSON.stringify({
        type: "user_message",
        content,
        ...(this.sessionId ? { session_id: this.sessionId } : {}),
      })
    );
    return true;
  }

  respond(kind, id, approved) {
    if (!this.isOpen) return;
    this.socket.send(JSON.stringify({ type: kind, id, approved }));
  }
}

/* -------------------------------------------------------------------------- */

export class ChatView {
  /**
   * @param {ChatCore} core
   * @param {HTMLElement} logEl  mesajların basılacağı kap
   * @param {{compact?: boolean, emptyHtml?: string}} options
   */
  constructor(core, logEl, options = {}) {
    this.core = core;
    this.log = logEl;
    this.compact = Boolean(options.compact);
    this.emptyHtml = options.emptyHtml || "";
    this.stream = null;      // o anki asistan cevabının DOM düğümü
    this.streamBody = null;
    this.buffer = "";
    this.currentStep = 0;
    this.toolNodes = new Map();

    core.on("event", (event) => this.handle(event));
    core.on("sent", ({ content }) => this.addUser(content));
    this.renderEmpty();
  }

  renderEmpty() {
    if (this.log.children.length === 0 && this.emptyHtml) {
      this.log.innerHTML = this.emptyHtml;
    }
  }

  _clearEmpty() {
    const empty = this.log.querySelector(".empty");
    if (empty) empty.remove();
  }

  scroll() {
    // `requestAnimationFrame`: düğüm eklendikten sonra ölçülsün.
    requestAnimationFrame(() => {
      this.log.scrollTop = this.log.scrollHeight;
    });
  }

  addUser(text) {
    this._clearEmpty();
    const node = el("div", "msg msg-user");
    node.textContent = text;
    this.log.appendChild(node);
    this.scroll();
  }

  /** Yeni bir asistan cevabı kabı aç (yoksa). */
  ensureStream() {
    if (this.stream) return;
    this._clearEmpty();
    this.stream = el("div", "msg msg-bot");
    this.streamBody = el("div", "body cursor");
    this.stream.appendChild(this.streamBody);
    this.log.appendChild(this.stream);
    this.buffer = "";
  }

  /** FAZ7_TESLIM.md §4.7 — sağlayıcı değişti, yarım metni at. */
  resetStream() {
    if (!this.stream || !this.buffer) return;
    this.buffer = "";
    this.streamBody.innerHTML = "";
  }

  finishStream() {
    if (!this.stream) return;
    this.streamBody.classList.remove("cursor");
    if (!this.buffer.trim() && !this.stream.querySelector(".tool, .notice")) {
      this.stream.remove();
    }
    this.stream = null;
    this.streamBody = null;
    this.buffer = "";
  }

  meta(node, tags) {
    let bar = node.querySelector(".msg-meta");
    if (!bar) {
      bar = el("div", "msg-meta");
      node.appendChild(bar);
    }
    for (const tag of tags) {
      const chip = el("span", `tag ${tag.cls || ""}`, tag.text);
      if (tag.title) chip.title = tag.title;
      bar.appendChild(chip);
    }
    return bar;
  }

  notice(text, kind = "info") {
    this.ensureStream();
    const node = el("div", `notice notice-${kind}`);
    node.textContent = text;
    // Araç kartlarıyla aynı akışta: olaylar sırayla, cevap en altta.
    this.stream.insertBefore(node, this.streamBody);
    this.scroll();
  }

  handle(event) {
    switch (event.type) {
      case "__reset__":
        this.log.innerHTML = "";
        this.stream = null;
        this.buffer = "";
        this.toolNodes.clear();
        this.renderEmpty();
        break;

      case "session":
        this.currentStep = 0;
        break;

      case "classification":
        if (this.compact) break;
        this.ensureStream();
        this.meta(this.stream, [
          {
            text: `görev: ${event.task_type}`,
            cls: "tag-accent",
            title: `Güven: %${Math.round((event.confidence || 0) * 100)} — ${event.reason || ""}`,
          },
        ]);
        break;

      case "step":
        this.currentStep = event.step;
        if (event.step > 1 && !this.compact) {
          this.ensureStream();
          this.meta(this.stream, [{ text: `adım ${event.step}` }]);
        }
        break;

      case "model_switch":
        // Bilgi, hata değil (FAZ7_TESLIM.md §4.4).
        this.ensureStream();
        this.resetStream();
        this.meta(this.stream, [
          {
            text: event.previous ? `${event.previous} → ${event.provider}` : event.provider,
            cls: "tag-accent",
            title: event.explanation || event.reason || "",
          },
          ...(event.forced ? [{ text: "son çare", cls: "tag-warn", title: event.reason }] : []),
        ]);
        break;

      case "token":
        this.ensureStream();
        this.buffer += event.content || "";
        this.streamBody.innerHTML = markdown(this.buffer);
        this.streamBody.classList.add("cursor");
        this.scroll();
        break;

      case "tool_call":
        this.addToolCall(event);
        break;

      case "approval_request":
      case "continue_request":
        // Diyalog global; burada sadece izini bırakıyoruz.
        this.notice(
          event.type === "approval_request"
            ? `Onay bekleniyor: ${event.summary || event.tool}`
            : `Adım limiti (${event.limit}) doldu — devam onayı bekleniyor.`,
          "warn"
        );
        break;

      case "approval_timeout":
        this.notice(event.message || "Onay süresi doldu, istek reddedildi.", "warn");
        break;

      case "tool_result":
        this.addToolResult(event);
        break;

      case "stopped":
        this.notice(event.message || "İşlem durduruldu.", "warn");
        this.finishStream();
        break;

      case "done":
        this.ensureStream();
        this.meta(this.stream, [
          { text: event.provider || "?", cls: "tag-accent" },
          { text: `${event.steps} adım` },
          { text: `${event.tokens || 0} token` },
          { text: `${((event.ms || 0) / 1000).toFixed(1)} sn` },
        ]);
        this.finishStream();
        break;

      case "error":
        this.ensureStream();
        this.notice(event.message || "Bilinmeyen hata.", "danger");
        this.finishStream();
        break;

      default:
        break;
    }
  }

  addToolCall(event) {
    this.ensureStream();
    const risk = event.risk || "safe";
    const node = el("div", `tool is-${risk}`);
    const head = el("div", "tool-head");
    head.innerHTML = `
      <span class="caret">›</span>
      <span class="name">${escapeHtml(event.name)}</span>
      <span class="args">${escapeHtml(compactArgs(event.args))}</span>
      <span class="tag ${RISK_CLASS[risk] || ""}">${RISK_LABEL[risk] || risk}</span>
      <span class="spin" data-spin></span>`;
    head.addEventListener("click", () => node.classList.toggle("is-open"));

    const body = el("div", "tool-body");
    if (event.reason) {
      body.appendChild(el("p", "tool-note", event.reason));
    }
    const args = el("pre", "", JSON.stringify(event.args ?? {}, null, 2));
    args.dataset.role = "args";
    body.appendChild(args);

    node.append(head, body);
    // Araç kartları cevabın ÜSTÜNE, kendi aralarında kronolojik sırayla.
    //
    // Eskiden `streamBody.nextSibling`den önce ekleniyordu: her yeni kart
    // gövdenin hemen arkasına giriyor, öncekileri aşağı itiyordu. Sonuç
    // kullanıcı için tersti — cevap en üstte, yapılan işler altında ve
    // TERS sırada. Doğal okuma sırası: önce ne yaptığı, sonra sonuç.
    this.stream.insertBefore(node, this.streamBody);
    this.toolNodes.set(event.id, node);
    this.scroll();
  }

  addToolResult(event) {
    const node = this.toolNodes.get(event.id);
    if (!node) return;
    const spinner = node.querySelector("[data-spin]");
    if (spinner) spinner.remove();

    const head = node.querySelector(".tool-head");
    const status = el("span", `tag ${event.ok ? "tag-ok" : "tag-danger"}`, event.ok ? "tamam" : "hata");
    head.appendChild(status);
    if (event.ms) head.appendChild(el("span", "tag", `${event.ms} ms`));

    const output = String(event.output || "");
    const isDryRun = output.includes("[dry-run]") || output.includes("[kuru çalışma]");
    if (isDryRun) {
      head.appendChild(el("span", "tag tag-warn", "kuru çalışma"));
    }
    // Politika engeli bir "hata" değil, bir karar — ayrı göster.
    if (output.startsWith("REDDEDİLDİ")) {
      head.appendChild(el("span", "tag tag-danger", "politika"));
    }

    const body = node.querySelector(".tool-body");
    const pre = el("pre", "", output.slice(0, 12000) || "(çıktı yok)");
    body.appendChild(pre);
    if (!event.ok || isDryRun) node.classList.add("is-open");
    this.scroll();
  }
}

function compactArgs(args) {
  if (!args || typeof args !== "object") return "";
  const parts = Object.entries(args).map(([key, value]) => {
    let text = typeof value === "string" ? value : JSON.stringify(value);
    if (text && text.length > 46) text = `${text.slice(0, 45)}…`;
    return `${key}=${text}`;
  });
  return parts.join(" ");
}
