/* Mail paneli: filtre şeridi + liste + detay.
 *
 * Bütün okumalar yerel önbellekten (`/api/mail/messages`) — ağ beklemesi yok.
 * IMAP'e giden tek düğmeler "Senkronla", okundu/yıldız ve taşıma.
 */

import { api } from "./api.js";
import { mountMailBody } from "./mailbody.js";
import {
  ICONS, closeModal, colorFor, el, emptyState, escapeHtml, formatDate,
  formatRelative, initials, openExternal, openModal, toast,
} from "./util.js";

const CATEGORY_ORDER = ["toplanti", "is", "fatura", "kisisel", "bildirim", "bulten", "diger"];
// Spam listede DEĞİL: "Tümü"ye karışmıyor ve şeridin en altında, diğer
// kategorilerden ayrı bir bölümde duruyor (kullanıcının isteği).
const EXILED = ["spam"];
const CATEGORY_DOT = {
  toplanti: "#8b6bff", is: "#5b8cff", fatura: "#eab24a", bulten: "#4fb6e0",
  bildirim: "#7a879d", kisisel: "#34c98a", diger: "#4a5468", spam: "#f4626f",
};

export class MailView {
  constructor(app) {
    this.app = app;
    this.messages = [];
    this.counts = { total: 0, unread: 0, categories: [], folders: [] };
    this.selected = null;
    this.filter = { view: "all", category: null, unread: false, flagged: false, q: "" };
    this.loaded = false;

    this.listEl = document.getElementById("mail-list");
    this.detailEl = document.getElementById("mail-detail");
    this.filtersEl = document.getElementById("mail-filters");
    this.layoutEl = document.querySelector(".mail-layout");

    document.getElementById("mail-sync").addEventListener("click", () => this.sync());
    document.getElementById("mail-ai-sort").addEventListener("click", () => this.aiSort());

    const search = document.getElementById("mail-search");
    let timer = null;
    search.addEventListener("input", () => {
      clearTimeout(timer);
      timer = setTimeout(() => {
        this.filter.q = search.value.trim();
        this.load();
      }, 260);
    });
  }

  async activate() {
    if (!this.loaded) {
      this.loaded = true;
      await this.load();
    }
  }

  /* ------------------------------------------------------------- veri */

  async load() {
    try {
      const data = await api.mailMessages({
        category: this.filter.category || undefined,
        unread: this.filter.unread,
        flagged: this.filter.flagged,
        q: this.filter.q,
        limit: 200,
      });
      this.messages = data.messages || [];
      this.counts = data.counts || this.counts;
      this.renderFilters();
      this.renderList();
      this.app.updateMailBadge(this.counts.unread || 0);
    } catch (error) {
      this.listEl.innerHTML = "";
      emptyState(this.listEl, ICONS.mail, "Mail okunamadı", error.message);
    }
  }

  async sync() {
    const button = document.getElementById("mail-sync");
    const original = button.innerHTML;
    button.disabled = true;
    button.innerHTML = '<span class="spin"></span> Senkronlanıyor…';
    try {
      const result = await api.mailSync();
      const failures = (result.reports || []).filter((report) => report.error);
      if (failures.length) {
        toast("Senkron hatası", failures.map((item) => item.error).join(" · "), "err");
      } else {
        toast(
          result.new ? `${result.new} yeni mail` : "Yeni mail yok",
          `Okunmamış: ${result.counts?.unread ?? 0}`,
          "ok"
        );
      }
      await this.load();
      this.app.calendar.invalidate();
    } catch (error) {
      toast("Senkron başarısız", error.message, "err");
    } finally {
      button.disabled = false;
      button.innerHTML = original;
    }
  }

  async aiSort() {
    const button = document.getElementById("mail-ai-sort");
    button.disabled = true;
    const original = button.textContent;
    button.innerHTML = '<span class="spin"></span> Modele soruluyor…';
    try {
      const result = await api.mailCategorizeBatch(15);
      if (result.detail) {
        toast("Sınıflandırma", result.detail, result.updated ? "ok" : "");
      } else {
        toast(
          `${result.updated}/${result.checked} mail yeniden sınıflandı`,
          result.model ? `Model: ${result.model}` : "",
          "ok"
        );
      }
      await this.load();
    } catch (error) {
      toast("Sınıflandırma başarısız", error.message, "err");
    } finally {
      button.disabled = false;
      button.textContent = original;
    }
  }

  /* ------------------------------------------------------ filtre şeridi */

  renderFilters() {
    const categories = new Map((this.counts.categories || []).map((row) => [row.category, row]));
    const chunks = [];

    const item = (key, label, active, count, dot) => `
      <button class="filter ${active ? "is-active" : ""}" data-filter="${key}">
        ${dot ? `<span class="dot" style="background:${dot}"></span>` : ""}
        <span>${escapeHtml(label)}</span>
        ${count ? `<span class="n">${count}</span>` : ""}
      </button>`;

    chunks.push(
      item("all", "Tümü", this.filter.view === "all", this.counts.total, "#4a5468"),
      item("unread", "Okunmamış", this.filter.view === "unread", this.counts.unread, "#5b8cff"),
      item("flagged", "Yıldızlı", this.filter.view === "flagged", 0, "#eab24a")
    );

    chunks.push('<div class="fgroup">Kategoriler</div>');
    for (const key of CATEGORY_ORDER) {
      const row = categories.get(key);
      if (!row && key !== "toplanti") continue;
      const label = this.app.categories[key] || key;
      chunks.push(
        item(`cat:${key}`, label, this.filter.view === `cat:${key}`, row?.total || 0, CATEGORY_DOT[key])
      );
    }

    // Sürgün kategoriler en altta, ayrı bir bölümde. "Tümü"ye hiç
    // karışmadıkları için burada olmaları tek görünme yolları.
    const exiled = EXILED.filter((key) => categories.get(key));
    if (exiled.length) {
      chunks.push('<div class="fgroup fgroup-exile">Ayrılanlar</div>');
      for (const key of exiled) {
        const row = categories.get(key);
        chunks.push(
          item(`cat:${key}`, this.app.categories[key] || key,
               this.filter.view === `cat:${key}`, row.total, CATEGORY_DOT[key])
        );
      }
    }

    this.filtersEl.innerHTML = chunks.join("");
    this.filtersEl.querySelectorAll("[data-filter]").forEach((button) => {
      button.addEventListener("click", () => this.applyFilter(button.dataset.filter));
    });
  }

  applyFilter(key) {
    this.filter.view = key;
    this.filter.category = key.startsWith("cat:") ? key.slice(4) : null;
    this.filter.unread = key === "unread";
    this.filter.flagged = key === "flagged";
    this.load();
  }

  /* ---------------------------------------------------------- liste */

  renderList() {
    if (!this.messages.length) {
      const hasAccounts = this.app.config.mail_accounts > 0;
      emptyState(
        this.listEl,
        ICONS.mail,
        hasAccounts ? "Bu filtrede mail yok" : "Henüz bir mail hesabı yok",
        hasAccounts
          ? "Sunucudan yeni mail çekmek için Senkronla'ya bas."
          : "Ayarlar → Mail hesapları'ndan bir IMAP hesabı ekle. Gmail için uygulama parolası gerekiyor.",
        hasAccounts ? "" : '<button class="btn btn-primary btn-sm" data-goto-settings>Ayarlara git</button>'
      );
      const goto = this.listEl.querySelector("[data-goto-settings]");
      if (goto) goto.addEventListener("click", () => this.app.show("settings"));
      this.detailEl.innerHTML = "";
      return;
    }

    this.listEl.innerHTML = "";
    for (const message of this.messages) {
      const node = el(
        "div",
        `mail-item ${message.seen ? "" : "is-unread"} ${this.selected?.id === message.id ? "is-active" : ""}`
      );
      const category = message.category || "diger";
      node.innerHTML = `
        <div class="mail-row">
          ${message.seen ? "" : '<span class="unread-dot"></span>'}
          <span class="from">${escapeHtml(message.from_name || message.from_addr || "—")}</span>
          <span class="marks">
            ${message.flagged ? "★" : ""}
            ${message.has_invite ? "📅" : ""}
            ${message.attachments?.length ? "📎" : ""}
          </span>
          <span class="when">${escapeHtml(formatRelative(message.date_ts))}</span>
        </div>
        <div class="subj">${escapeHtml(message.subject || "(konusuz)")}</div>
        <div class="mail-row" style="margin-top:4px">
          <span class="cat cat-${category}">${escapeHtml(this.app.categories[category] || category)}</span>
          ${message.summary ? '<span class="cat cat-is">özetlendi</span>' : ""}
        </div>
        <div class="snip">${escapeHtml(message.snippet || "")}</div>`;
      node.addEventListener("click", () => this.select(message.id));
      this.listEl.appendChild(node);
    }
  }

  /* ---------------------------------------------------------- detay */

  async select(messageId) {
    try {
      const message = await api.mailMessage(messageId);
      this.selected = message;
      this.renderList();
      this.renderDetail(message);
      this.layoutEl.classList.add("show-detail");
      // Dock'a bağlam ver: artık "bunu özetle" demek yeterli.
      this.app.setDockContext("mail", {
        label: `mail #${message.id} — ${message.subject || "(konusuz)"}`,
        id: message.id,
      });
      if (!message.seen) {
        api.mailMark(messageId, { seen: true })
          .then(() => {
            message.seen = true;
            const row = this.messages.find((item) => item.id === messageId);
            if (row) row.seen = true;
            this.counts.unread = Math.max(0, (this.counts.unread || 0) - 1);
            this.renderList();
            this.renderFilters();
            this.app.updateMailBadge(this.counts.unread);
          })
          .catch(() => {});
      }
    } catch (error) {
      toast("Mail açılamadı", error.message, "err");
    }
  }

  renderDetail(message) {
    const category = message.category || "diger";
    const color = colorFor(message.from_addr || message.from_name || "?");
    const attachments = message.attachments || [];

    this.detailEl.innerHTML = `
      <div class="mail-detail-head">
        <h2>${escapeHtml(message.subject || "(konusuz)")}</h2>
        <div class="mail-from">
          <div class="avatar" style="background:${color}">${escapeHtml(initials(message.from_name, message.from_addr))}</div>
          <div>
            <div class="who">${escapeHtml(message.from_name || message.from_addr)}</div>
            <div class="addr">${escapeHtml(message.from_addr)} · ${escapeHtml(formatDate(message.date_ts))}</div>
          </div>
        </div>
        <div class="mail-tools">
          <span class="cat cat-${category}" style="padding:5px 10px">${escapeHtml(this.app.categories[category] || category)}</span>
          <button class="btn btn-ghost btn-sm" data-act="summarize">✨ Özetle</button>
          <button class="btn btn-ghost btn-sm" data-act="calendar">📅 Takvime ekle</button>
          <button class="btn btn-ghost btn-sm" data-act="flag">${message.flagged ? "★ Yıldızı kaldır" : "☆ Yıldızla"}</button>
          <button class="btn btn-ghost btn-sm" data-act="unread">${message.seen ? "Okunmadı yap" : "Okundu yap"}</button>
          <button class="btn btn-ghost btn-sm" data-act="recat">Kategori değiştir</button>
          <button class="btn btn-ghost btn-sm" data-act="ask">💬 Sohbette sor</button>
          ${category === "spam"
            ? '<button class="btn btn-ghost btn-sm" data-act="notspam">Spam değil</button>'
            : '<button class="btn btn-ghost btn-sm" data-act="spam">🚫 Spam</button>'}
          <button class="btn btn-danger btn-sm" data-act="trash">Çöpe taşı</button>
        </div>
        ${message.category_reason ? `<p class="muted" style="margin-top:9px">Neden bu kategori: ${escapeHtml(message.category_reason)}</p>` : ""}
      </div>
      ${message.summary ? `
        <div class="summary-card">
          <h4>ÖZET${message.summary_model ? ` · ${escapeHtml(message.summary_model)}` : ""}</h4>
          <div>${escapeHtml(message.summary)}</div>
        </div>` : ""}
      ${message.ics_payload ? '<div class="invite-card" id="invite-card"><h4>TAKVİM DAVETİ</h4><div class="invite-row">okunuyor…</div></div>' : ""}
      ${attachments.length ? `
        <div class="summary-card" style="background:var(--bg-1);border-color:var(--line)">
          <h4 style="color:var(--text-3)">EKLER (${attachments.length})</h4>
          <div>${attachments.map((item) => escapeHtml(`${item.filename} · ${item.content_type}`)).join("<br>")}</div>
        </div>` : ""}
      <div class="mail-body" id="mail-body-host"></div>`;

    // Gövde ayrı bir modülde: HTML mailler temizlenip kum havuzunda,
    // uzak resimler engelli olarak çiziliyor (bkz. mailbody.js).
    mountMailBody(document.getElementById("mail-body-host"), message, {
      onOpenLink: (url) => openExternal(url),
    });

    this.detailEl.querySelectorAll("[data-act]").forEach((button) => {
      button.addEventListener("click", () => this.action(button.dataset.act, message));
    });

    if (message.ics_payload) this.loadInvite(message.id);
  }

  async loadInvite(messageId) {
    try {
      const draft = await api.calendarDraftFromMail(messageId);
      const card = document.getElementById("invite-card");
      if (!card) return;
      card.innerHTML = `
        <h4>TAKVİM DAVETİ</h4>
        <div class="invite-row"><b>${escapeHtml(draft.title || "—")}</b></div>
        <div class="invite-row">🕐 ${escapeHtml(formatDate(draft.starts_at))}</div>
        ${draft.location ? `<div class="invite-row">📍 ${escapeHtml(draft.location)}</div>` : ""}
        ${draft.meeting_url ? `<div class="invite-row">🔗 ${escapeHtml(draft.meeting_url)}</div>` : ""}
        ${draft.attendees?.length ? `<div class="invite-row">👥 ${escapeHtml(draft.attendees.slice(0, 4).join(", "))}</div>` : ""}
        ${draft.recurring ? '<div class="invite-row" style="color:var(--warn)">⚠ Tekrarlayan seri — sadece ilk oluşum eklenir.</div>' : ""}
        <button class="btn btn-primary btn-sm" style="margin-top:9px" data-act="calendar">Takvime ekle</button>`;
      card.querySelector("[data-act]").addEventListener("click", () => this.action("calendar", { id: messageId }));
    } catch {
      /* davet okunamadıysa kart sessizce kalsın */
    }
  }

  /* -------------------------------------------------------- eylemler */

  async action(kind, message) {
    try {
      if (kind === "summarize") return await this.summarize(message.id);
      if (kind === "calendar") return await this.toCalendar(message.id);
      if (kind === "ask") {
        this.app.show("chat");
        this.app.askInChat(
          `mail #${message.id} — ${message.subject || ""}`.trim(),
          "Bu maili özetle ve benden bir aksiyon bekleniyorsa söyle."
        );
        return;
      }
      if (kind === "flag") {
        await api.mailMark(message.id, { flagged: !message.flagged });
        toast(message.flagged ? "Yıldız kaldırıldı" : "Yıldızlandı", "", "ok");
      }
      if (kind === "unread") {
        await api.mailMark(message.id, { seen: !message.seen });
      }
      if (kind === "recat") return this.categoryDialog(message);
      if (kind === "spam" || kind === "notspam") {
        // Spam'e alınan mail "Tümü"den kayboluyor; nereye gittiğini
        // söylemezsek kullanıcı silindi sanır.
        await api.mailCategory(message.id, kind === "spam" ? "spam" : "diger");
        toast(
          kind === "spam" ? "Spam'e taşındı" : "Spam'den çıkarıldı",
          kind === "spam" ? "Şeridin altındaki Spam bölümünde." : "Artık Tümü listesinde.",
          "ok"
        );
        if (kind === "spam") {
          this.selected = null;
          this.detailEl.innerHTML = "";
          this.app.setDockContext("mail", null);
          await this.load();
          return;
        }
      }
      if (kind === "trash") {
        if (!confirm("Bu mail çöp kutusuna taşınacak. Devam?")) return;
        await api.mailMove(message.id, "__trash__");
        toast("Çöpe taşındı", "", "ok");
        this.selected = null;
        this.detailEl.innerHTML = "";
        this.app.setDockContext("mail", null);
      }
      await this.load();
      if (this.selected && kind !== "trash") await this.select(this.selected.id);
    } catch (error) {
      toast("İşlem başarısız", error.message, "err");
    }
  }

  async summarize(messageId) {
    const button = this.detailEl.querySelector('[data-act="summarize"]');
    if (button) {
      button.disabled = true;
      button.innerHTML = '<span class="spin"></span> Özetleniyor…';
    }
    try {
      const result = await api.mailSummarize(messageId);
      toast(result.cached ? "Özet (önbellekten)" : "Özet hazır", result.model || "", "ok");
      await this.select(messageId);
    } catch (error) {
      toast("Özetlenemedi", error.message, "err");
      if (button) {
        button.disabled = false;
        button.innerHTML = "✨ Özetle";
      }
    }
  }

  async toCalendar(messageId) {
    let draft;
    try {
      draft = await api.calendarDraftFromMail(messageId);
    } catch (error) {
      return toast("Toplantı okunamadı", error.message, "err");
    }

    const confidence = Math.round((draft.confidence || 0) * 100);
    // Güven tam 1.0 ise tarih ICS davetinden OKUNDU; her şey daha azsa
    // metinden TAHMİN edildi. Backend metin tahminini bilerek 0.95'te
    // tutuyor, böylece bu ayrım eşik uydurmadan yapılabiliyor.
    const fromInvite = draft.confidence >= 1;
    const startValue = draft.starts_at ? draft.starts_at.slice(0, 16) : "";
    openModal(`
      <div class="modal-head">
        <h3>Takvime ekle</h3>
        <p>${fromInvite
          ? "Maildeki takvim daveti okundu — tarih tahmin edilmedi."
          : `Tarih metinden çıkarıldı · güven %${confidence}`}</p>
      </div>
      <div class="modal-body">
        ${confidence < 50 ? `<div class="notice notice-warn">${escapeHtml(draft.reason || "")}<br>Tarihi kontrol et.</div>` : ""}
        <label class="f"><span>Başlık</span><input type="text" id="ev-title" value="${escapeHtml(draft.title || "")}"></label>
        <label class="f"><span>Başlangıç</span><input type="datetime-local" id="ev-start" value="${escapeHtml(startValue)}"></label>
        <label class="f"><span>Bitiş (boşsa +1 saat)</span><input type="datetime-local" id="ev-end" value="${escapeHtml((draft.ends_at || "").slice(0, 16))}"></label>
        <label class="f"><span>Yer / bağlantı</span><input type="text" id="ev-loc" value="${escapeHtml(draft.location || draft.meeting_url || "")}"></label>
        <label class="f"><span>Hatırlatma (dakika önce)</span><input type="number" id="ev-rem" value="${this.app.config.default_reminder_minutes ?? 10}" min="-1"></label>
        ${draft.recurring ? '<div class="notice notice-warn">Bu tekrarlayan bir seri; sadece ilk oluşum eklenecek.</div>' : ""}
        <p class="muted">Gerekçe: ${escapeHtml(draft.reason || "—")}</p>
      </div>
      <div class="modal-foot">
        <button class="btn btn-ghost" data-close>Vazgeç</button>
        <button class="btn btn-primary" data-save>Takvime ekle</button>
      </div>`);

    document.querySelector("[data-close]").addEventListener("click", closeModal);
    document.querySelector("[data-save]").addEventListener("click", async () => {
      const start = document.getElementById("ev-start").value;
      if (!start) return toast("Başlangıç zamanı gerekli", "", "err");
      try {
        await api.calendarFromMail(messageId, {
          title: document.getElementById("ev-title").value,
          starts_at: start,
          ends_at: document.getElementById("ev-end").value,
          location: document.getElementById("ev-loc").value,
          reminder_minutes: Number(document.getElementById("ev-rem").value),
        });
        closeModal();
        toast("Takvime eklendi", "Hatırlatma masaüstü bildirimiyle gelecek.", "ok");
        this.app.calendar.invalidate();
      } catch (error) {
        toast("Eklenemedi", error.message, "err");
      }
    });
  }

  categoryDialog(message) {
    // Spam da seçilebilmeli: kullanıcının bir maili listeden sürgüne
    // göndermesinin yolu bu.
    const options = [...CATEGORY_ORDER, ...EXILED].map(
      (key) => `<button class="btn btn-ghost btn-sm" data-cat="${key}"
        style="border-color:${CATEGORY_DOT[key]}55">${escapeHtml(this.app.categories[key] || key)}</button>`
    ).join(" ");
    openModal(`
      <div class="modal-head"><h3>Kategori değiştir</h3><p>${escapeHtml(message.subject || "")}</p></div>
      <div class="modal-body"><div style="display:flex;gap:7px;flex-wrap:wrap">${options}</div></div>
      <div class="modal-foot"><button class="btn btn-ghost" data-close>Kapat</button></div>`);
    document.querySelector("[data-close]").addEventListener("click", closeModal);
    document.querySelectorAll("[data-cat]").forEach((button) => {
      button.addEventListener("click", async () => {
        try {
          await api.mailCategory(message.id, button.dataset.cat);
          closeModal();
          toast("Kategori güncellendi", "", "ok");
          await this.load();
          await this.select(message.id);
        } catch (error) {
          toast("Değiştirilemedi", error.message, "err");
        }
      });
    });
  }
}
