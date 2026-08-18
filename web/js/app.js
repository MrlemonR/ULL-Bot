/* Uygulama kabuğu: sol şerit, görünüm değişimi, onay diyalogları, dock'lar.
 *
 * Kullanıcının istediği düzen: Sohbet kendi tam ekran paneli; Mail ve
 * Takvim'e geçince YANDA bağlamlı bir sohbet kutusu açılıyor. Üç sohbet
 * yüzeyi de tek `ChatCore`'u paylaşıyor, yani aynı konuşma.
 */

import { api } from "./api.js";
import { ChatCore, ChatView } from "./chat.js";
import { CalendarView } from "./calendar.js";
import { MailView } from "./mail.js";
import { HistoryView, QuotaView, SettingsView } from "./panels.js";
import {
  ICONS, closeModal, escapeHtml, interceptExternalLinks, isModalOpen, openModal, toast,
} from "./util.js";

const VIEW_META = {
  chat: { title: "Sohbet", sub: "Ajanla konuş — dosyalar, komutlar, mail ve takvim." },
  mail: { title: "Mail", sub: "IMAP kutun, kategorilere ayrılmış. Yandaki kutudan sor." },
  calendar: { title: "Takvim", sub: "Uygulamanın kendi takvimi. Hatırlatmalar masaüstü bildirimi olarak gelir." },
  quota: { title: "Kota", sub: "Sağlayıcı limitleri ve son 14 günün kullanımı." },
  history: { title: "Geçmiş", sub: "Kaydedilmiş oturumlar ve tüm mesajlarda arama." },
  settings: { title: "Ayarlar", sub: "Mail hesapları, bildirimler, kalıcı hafıza." },
};

const CHAT_EMPTY = `
  <div class="empty">
    ${ICONS.chat}
    <h3>Ne yapmamı istersin?</h3>
    <p>Dosyalarına bakabilir, komut çalıştırabilir, maillerini okuyup özetleyebilir
       ve toplantıları takvime ekleyebilirim.</p>
  </div>`;

class App {
  constructor() {
    this.config = {};
    this.categories = {};
    this.view = "chat";

    this.core = new ChatCore();
    this.chat = null;
    this.docks = {};
    this.dockContext = { mail: null, calendar: null };

    this.mail = new MailView(this);
    this.calendar = new CalendarView(this);
    this.quota = new QuotaView(this);
    this.history = new HistoryView(this);
    this.settings = new SettingsView(this);
  }

  async start() {
    await this.refreshConfig();

    this.chat = new ChatView(this.core, document.getElementById("chat-log"), {
      emptyHtml: CHAT_EMPTY,
    });
    this.buildDock("mail");
    this.buildDock("calendar");

    this.core.on("state", (state) => this.onConnection(state));
    this.core.on("event", (event) => this.onEvent(event));
    this.core.connect();

    this.bindRail();
    this.bindComposer();
    this.bindGlobalKeys();
    // Native pencerede `target="_blank"` çalışmıyor; tüm dış bağlantılar
    // sistem tarayıcısına yönlendirilir (bkz. util.js).
    interceptExternalLinks();

    // Rozetleri açılışta bir kere doldur — kullanıcı mail paneline hiç
    // girmese bile okunmamış sayısını görsün.
    this.primeBadges();
  }

  async refreshConfig() {
    try {
      this.config = await api.config();
      this.categories = this.config.categories || {};
      document.getElementById("chip-profile").textContent = this.config.profile || "—";
      document.getElementById("chip-dryrun").hidden = !this.config.dry_run;
    } catch (error) {
      toast("Ayarlar okunamadı", error.message, "err");
    }
  }

  async primeBadges() {
    try {
      const [mail, calendar] = await Promise.all([
        api.mailMessages({ limit: 1 }).catch(() => null),
        api.calendarEvents().catch(() => null),
      ]);
      if (mail?.counts) this.updateMailBadge(mail.counts.unread || 0);
      if (calendar?.stats) this.updateCalendarBadge(calendar.stats.today || 0);
    } catch {
      /* rozetler kritik değil */
    }
  }

  /* ------------------------------------------------------- gezinme */

  bindRail() {
    document.querySelectorAll(".rail-btn[data-view]").forEach((button) => {
      button.addEventListener("click", () => this.show(button.dataset.view));
    });
  }

  show(name) {
    if (!VIEW_META[name]) return;
    this.view = name;

    document.querySelectorAll(".rail-btn[data-view]").forEach((button) => {
      button.classList.toggle("is-active", button.dataset.view === name);
    });
    document.querySelectorAll(".view").forEach((section) => {
      section.classList.toggle("is-active", section.dataset.view === name);
    });

    document.getElementById("view-title").textContent = VIEW_META[name].title;
    document.getElementById("view-sub").textContent = VIEW_META[name].sub;

    const target = { mail: this.mail, calendar: this.calendar, quota: this.quota,
                     history: this.history, settings: this.settings }[name];
    if (target) target.activate();

    if (name === "chat") {
      setTimeout(() => document.getElementById("chat-input").focus(), 40);
    }
  }

  /* --------------------------------------------------- sohbet dock'u */

  buildDock(name) {
    const host = document.getElementById(`dock-${name}`);
    host.innerHTML = `
      <div class="dock-head">
        <h3>Asistan</h3>
        <button class="icon-btn" data-new title="Yeni konuşma">✎</button>
        <button class="icon-btn" data-toggle title="Daralt/genişlet">⇥</button>
      </div>
      <div class="dock-context" hidden></div>
      <div class="dock-body"></div>
      <form class="dock-form">
        <div class="composer-box">
          <textarea rows="1" placeholder="Bu sayfa hakkında sor…"></textarea>
          <button type="submit" class="send-btn" title="Gönder">
            <svg viewBox="0 0 24 24"><path d="m4 12 16-8-6 8 6 8z"/></svg>
          </button>
        </div>
      </form>`;

    const view = new ChatView(this.core, host.querySelector(".dock-body"), {
      compact: true,
      emptyHtml: `<div class="empty" style="min-height:120px">${ICONS.chat}
        <p>${name === "mail"
          ? "Bir mail seç, sonra “özetle”, “takvime ekle” ya da “bu kimden?” diye sor."
          : "“Yarın 15:00’e diş randevusu ekle” gibi yazabilirsin."}</p></div>`,
    });

    const form = host.querySelector(".dock-form");
    const input = host.querySelector("textarea");
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      this.sendFrom(input, this.dockContext[name]);
    });
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        this.sendFrom(input, this.dockContext[name]);
      }
    });
    autoGrow(input);

    host.querySelector("[data-new]").addEventListener("click", () => {
      this.core.newSession();
      toast("Yeni konuşma başladı", "", "ok");
    });
    host.querySelector("[data-toggle]").addEventListener("click", () => {
      host.parentElement.classList.toggle("dock-collapsed");
    });

    this.docks[name] = { host, view, input, contextEl: host.querySelector(".dock-context") };
  }

  setDockContext(name, context) {
    this.dockContext[name] = context;
    const dock = this.docks[name];
    if (!dock) return;
    if (!context) {
      dock.contextEl.hidden = true;
      return;
    }
    dock.contextEl.hidden = false;
    dock.contextEl.innerHTML = `📎 <b>Bağlam</b> ${escapeHtml(context.label)}<span class="x" data-clear>×</span>`;
    dock.contextEl.querySelector("[data-clear]").addEventListener("click", () => {
      this.setDockContext(name, null);
    });
  }

  /* ------------------------------------------------------- besteci */

  bindComposer() {
    const form = document.getElementById("chat-form");
    const input = document.getElementById("chat-input");

    form.addEventListener("submit", (event) => {
      event.preventDefault();
      this.sendFrom(input, null);
    });
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        this.sendFrom(input, null);
      }
    });
    autoGrow(input);

    document.querySelectorAll("#chat-hints .hint").forEach((button) => {
      button.addEventListener("click", () => {
        input.value = button.dataset.prompt;
        input.focus();
        this.sendFrom(input, null);
      });
    });
  }

  sendFrom(input, context) {
    const text = input.value.trim();
    if (!text) return;
    const result = this.core.send(text, context);
    if (result === true) {
      input.value = "";
      input.style.height = "auto";
      // Bağlam tek seferlik: gönderildikten sonra düşer, kullanıcı aynı
      // maili sürekli başa eklemek zorunda kalmasın.
      const hints = document.getElementById("chat-hints");
      if (hints) hints.style.display = "none";
    } else if (result && result.error) {
      toast("Gönderilemedi", result.error, "err");
    }
  }

  /** Mail panelinden "sohbette sor" — bağlamı da taşır. */
  askInChat(contextLabel, question) {
    const input = document.getElementById("chat-input");
    input.value = question;
    input.focus();
    this.core.send(question, { label: contextLabel });
    input.value = "";
  }

  resumeSession(sessionId, messages) {
    this.core.sessionId = sessionId;
    this.core.newSession();
    this.core.sessionId = sessionId;
    const log = document.getElementById("chat-log");
    log.innerHTML = "";
    for (const message of messages) {
      if (message.role === "user") this.chat.addUser(message.content);
      else if (message.role === "assistant" && message.content) {
        this.chat.ensureStream();
        this.chat.buffer = message.content;
        this.chat.streamBody.innerHTML = escapeHtml(message.content).replace(/\n/g, "<br>");
        this.chat.finishStream();
      }
    }
    this.show("chat");
    toast("Oturuma devam ediliyor", `${messages.length} mesaj yüklendi`, "ok");
  }

  /* ------------------------------------------------- bağlantı durumu */

  onConnection({ connection, busy }) {
    const chip = document.getElementById("chip-conn");
    chip.classList.remove("is-ok", "is-busy", "is-down");
    const label = chip.querySelector("span");

    if (connection !== "open") {
      chip.classList.add("is-down");
      label.textContent = "bağlantı yok";
    } else if (busy) {
      chip.classList.add("is-busy");
      label.textContent = "çalışıyor";
    } else {
      chip.classList.add("is-ok");
      label.textContent = "hazır";
    }

    // FAZ7_TESLIM.md §4.5 — tur bitene kadar gönderim kilitli.
    const locked = busy || connection !== "open";
    document.getElementById("chat-form").classList.toggle("is-locked", locked);
    document.getElementById("chat-send").disabled = locked;
    for (const dock of Object.values(this.docks)) {
      dock.host.querySelector(".dock-form").classList.toggle("is-locked", locked);
    }
  }

  /* ----------------------------------------------- onay diyalogları */

  onEvent(event) {
    if (event.type === "approval_request") this.approvalDialog(event);
    if (event.type === "continue_request") this.continueDialog(event);
    if (event.type === "approval_timeout" && isModalOpen()) closeModal();
    // Ajan takvimi/maili değiştirmiş olabilir — panelleri tazele.
    if (event.type === "done") {
      this.calendar.invalidate();
      if (this.view === "calendar") this.calendar.load();
      if (this.view === "mail") this.mail.load();
      this.primeBadges();
    }
  }

  approvalDialog(event) {
    const dryRun = Boolean(event.dry_run);
    openModal(`
      <div class="modal-head">
        <h3>Onay gerekiyor</h3>
        <p>Ajan bir işlem yapmak istiyor ve senin iznini bekliyor.</p>
      </div>
      <div class="modal-body">
        ${dryRun ? '<div class="notice notice-warn">KURU ÇALIŞMA açık — onaylasan bile işlem gerçekten çalışmayacak, sadece ne yapacağını raporlayacak.</div>' : ""}
        <dl class="kv">
          <dt>Araç</dt><dd><code>${escapeHtml(event.tool)}</code></dd>
          <dt>Özet</dt><dd>${escapeHtml(event.summary || "—")}</dd>
          <dt>Gerekçe</dt><dd>${escapeHtml(event.reason || "—")}</dd>
          ${event.paths?.length ? `<dt>Yollar</dt><dd>${event.paths.map((path) => escapeHtml(path)).join("<br>")}</dd>` : ""}
        </dl>
        ${event.detail ? `<pre>${escapeHtml(event.detail)}</pre>` : ""}
        <details style="margin-top:12px">
          <summary class="muted" style="cursor:pointer">Ham argümanlar</summary>
          <pre style="margin-top:8px">${escapeHtml(JSON.stringify(event.args ?? {}, null, 2))}</pre>
        </details>
      </div>
      <div class="modal-foot">
        <button class="btn btn-ghost" data-deny>Reddet</button>
        <button class="btn btn-primary" data-approve>${dryRun ? "Onayla (kuru)" : "Onayla ve çalıştır"}</button>
      </div>`);

    const respond = (approved) => {
      this.core.respond("approval_response", event.id, approved);
      closeModal();
    };
    document.querySelector("[data-approve]").addEventListener("click", () => respond(true));
    document.querySelector("[data-deny]").addEventListener("click", () => respond(false));
    document.querySelector("[data-approve]").focus();
  }

  continueDialog(event) {
    openModal(`
      <div class="modal-head">
        <h3>Adım limitine ulaşıldı</h3>
        <p>${escapeHtml(event.summary || `${event.steps}/${event.limit} adım tamamlandı.`)}</p>
      </div>
      <div class="modal-body">
        <p class="muted">Ajan hâlâ bitmedi. Devam etmesini istiyor musun? Her devam ${escapeHtml(String(event.limit))} adım daha ekler.</p>
      </div>
      <div class="modal-foot">
        <button class="btn btn-ghost" data-stop>Durdur</button>
        <button class="btn btn-primary" data-go>Devam et</button>
      </div>`);

    const respond = (approved) => {
      this.core.respond("continue_response", event.id, approved);
      closeModal();
    };
    document.querySelector("[data-go]").addEventListener("click", () => respond(true));
    document.querySelector("[data-stop]").addEventListener("click", () => respond(false));
  }

  /* ------------------------------------------------------- rozetler */

  updateMailBadge(count) {
    const badge = document.getElementById("rail-mail-badge");
    badge.textContent = count > 99 ? "99+" : String(count);
    badge.hidden = !count;
  }

  updateCalendarBadge(count) {
    const badge = document.getElementById("rail-cal-badge");
    badge.textContent = String(count);
    badge.hidden = !count;
  }

  /* ------------------------------------------------------ kısayollar */

  bindGlobalKeys() {
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && isModalOpen()) {
        // Onay diyaloğunu Esc ile kapatmak REDDETME anlamına gelmez —
        // ajan beklemeye devam eder. Kullanıcıyı yanıltmamak için
        // yalnızca onay içermeyen diyaloglar Esc ile kapanır.
        const modal = document.getElementById("modal");
        if (!modal.querySelector("[data-approve], [data-go]")) closeModal();
      }
      if ((event.ctrlKey || event.metaKey) && event.key >= "1" && event.key <= "6") {
        const order = ["chat", "mail", "calendar", "quota", "history", "settings"];
        event.preventDefault();
        this.show(order[Number(event.key) - 1]);
      }
    });

    document.getElementById("modal-backdrop").addEventListener("click", (event) => {
      if (event.target.id !== "modal-backdrop") return;
      const modal = document.getElementById("modal");
      if (!modal.querySelector("[data-approve], [data-go]")) closeModal();
    });
  }
}

function autoGrow(textarea) {
  const resize = () => {
    textarea.style.height = "auto";
    textarea.style.height = `${Math.min(textarea.scrollHeight, 180)}px`;
  };
  textarea.addEventListener("input", resize);
}

const app = new App();
window.ullbot = app; // konsoldan hata ayıklamak için
app.start();
