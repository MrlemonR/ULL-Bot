/* Mail paneli: filtre şeridi + liste + detay.
 *
 * Bütün okumalar yerel önbellekten (`/api/mail/messages`) — ağ beklemesi yok.
 * IMAP'e giden tek düğmeler "Senkronla", okundu/yıldız ve taşıma.
 */

import { api } from "./api.js";
import { mountMailBody } from "./mailbody.js";
import {
  ICONS, closeModal, colorFor, el, emptyState, escapeHtml, formatDate,
  formatRelative, initials, markdown, openExternal, openModal, toast,
} from "./util.js";

// Sıra kullanıcının verdiği önem sırasıyla: kod ve sipariş önde, reklam
// arkada (bkz. app/mail/classify.py CATEGORIES).
const CATEGORY_ORDER = [
  "kod", "genel", "bildirim", "reklam", "toplanti", "fatura", "is", "kisisel", "diger",
];
// Spam listede DEĞİL: "Tümü"ye karışmıyor ve şeridin sonunda, diğer
// kategorilerden ayrı duruyor (kullanıcının isteği).
const EXILED = ["spam"];
// "Öncelikli" görünümünden düşen kategoriler. Kullanıcı "Tümü direkt seçili
// gelmesin, önemli mailler önde gelsin" dedi: açılışta reklam ve kararsızlar
// listeye hiç karışmıyor, tek tıkla ulaşılabiliyorlar.
const NOISY = ["reklam", "diger"];
const CATEGORY_DOT = {
  kod: "#f4626f", genel: "#34c98a", bildirim: "#4fb6e0", reklam: "#7a879d",
  toplanti: "#8b6bff", fatura: "#eab24a", is: "#5b8cff",
  kisisel: "#34c98a", diger: "#4a5468", spam: "#f4626f",
};

export class MailView {
  constructor(app) {
    this.app = app;
    this.messages = [];
    this.counts = { total: 0, unread: 0, categories: [], folders: [] };
    this.selected = null;
    // Açılış görünümü "Tümü" değil "Öncelikli" (kullanıcı isteği).
    this.filter = { view: "priority", category: null, unread: false, flagged: false, q: "" };
    this.loaded = false;
    // Checkbox ile seçilenler. Doluyken üst bar kategoriler yerine toplu
    // işlemleri gösteriyor.
    this.checked = new Set();

    this.listEl = document.getElementById("mail-list");
    this.detailEl = document.getElementById("mail-detail");
    this.filtersEl = document.getElementById("mail-filters");
    this.bulkEl = document.getElementById("mail-bulk");
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
        exclude: this.filter.view === "priority" ? NOISY.join(",") : undefined,
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

    const item = (key, label, count, dot) => `
      <button class="tab ${this.filter.view === key ? "is-active" : ""}" data-filter="${key}">
        ${dot ? `<span class="dot" style="background:${dot}"></span>` : ""}
        <span>${escapeHtml(label)}</span>
        ${count ? `<span class="n">${count}</span>` : ""}
      </button>`;

    // Gürültülü kategoriler dışarıda kaldığı için "Öncelikli"nin sayısı
    // toplamdan farklı — kullanıcı neyi görmediğini bilsin diye sayıyoruz.
    const noisy = NOISY.reduce((sum, key) => sum + (categories.get(key)?.total || 0), 0);
    chunks.push(
      item("priority", "Öncelikli", Math.max(0, (this.counts.total || 0) - noisy), "#34c98a"),
      item("all", "Tümü", this.counts.total, "#4a5468"),
      item("unread", "Okunmamış", this.counts.unread, "#5b8cff"),
      item("flagged", "Yıldızlı", 0, "#eab24a"),
      '<span class="tab-sep"></span>'
    );

    for (const key of CATEGORY_ORDER) {
      const row = categories.get(key);
      if (!row) continue;
      chunks.push(item(`cat:${key}`, this.app.categories[key] || key, row.total, CATEGORY_DOT[key]));
    }
    for (const key of EXILED) {
      const row = categories.get(key);
      if (row) {
        chunks.push(item(`cat:${key}`, this.app.categories[key] || key, row.total, CATEGORY_DOT[key]));
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
    this.clearSelection();
    this.load();
  }

  /* --------------------------------------------------- toplu seçim */

  clearSelection() {
    this.checked.clear();
    this.renderBulk();
  }

  toggleCheck(id, on) {
    if (on) this.checked.add(id);
    else this.checked.delete(id);
    this.renderBulk();
  }

  /** Seçim varsa kategoriler gizlenir, yerine toplu işlemler gelir. */
  renderBulk() {
    const count = this.checked.size;
    this.filtersEl.hidden = count > 0;
    this.bulkEl.hidden = count === 0;
    if (!count) {
      this.bulkEl.innerHTML = "";
      return;
    }
    this.bulkEl.innerHTML = `
      <span class="bulk-count">[ ${count} seçili ]</span>
      <button class="btn btn-ghost btn-sm" data-bulk="read">Okundu yap</button>
      <button class="btn btn-ghost btn-sm" data-bulk="unread">Okunmadı yap</button>
      <button class="btn btn-ghost btn-sm" data-bulk="star">Yıldızla</button>
      <button class="btn btn-ghost btn-sm" data-bulk="category">Kategori değiştir</button>
      <button class="btn btn-danger btn-sm" data-bulk="trash">Sil</button>
      <span class="bulk-sep"></span>
      <button class="btn btn-ghost btn-sm" data-bulk="all-read">Tümünü okundu yap</button>
      <button class="btn btn-ghost btn-sm" data-bulk="none">Seçimi bırak</button>`;
    this.bulkEl.querySelectorAll("[data-bulk]").forEach((button) => {
      button.addEventListener("click", () => this.runBulk(button.dataset.bulk));
    });
  }

  async runBulk(action) {
    if (action === "none") {
      this.clearSelection();
      this.renderList();
      return;
    }
    if (action === "category") {
      this.bulkCategoryDialog();
      return;
    }

    // "Tümünü okundu yap" seçime bakmaz: listedeki okunmamışların hepsi.
    const ids = action === "all-read"
      ? this.messages.filter((message) => !message.seen).map((message) => message.id)
      : [...this.checked];
    if (!ids.length) {
      toast("Yapılacak bir şey yok", "Okunmamış mail kalmamış.", "");
      return;
    }
    if (action === "trash" && !window.confirm(`${ids.length} mail çöpe taşınacak. Emin misin?`)) {
      return;
    }

    try {
      const result = await api.mailBulk(ids, action === "all-read" ? "read" : action);
      if (result.errors?.length) {
        toast("Kısmen yapıldı", result.errors.join(" · "), "warn");
      } else {
        toast("Tamam", `${result.updated} mail güncellendi.`, "ok");
      }
      this.clearSelection();
      await this.load();
    } catch (error) {
      toast("İşlem başarısız", error.message, "err");
    }
  }

  bulkCategoryDialog() {
    const buttons = Object.keys(this.app.categories || {})
      .map((key) => `<button class="btn btn-ghost btn-sm" data-cat="${key}">
        ${escapeHtml(this.app.categories[key])}</button>`)
      .join("");
    openModal(`
      <div class="modal-head"><h3>[ KATEGORİ DEĞİŞTİR ]</h3>
        <p>${this.checked.size} mail için yeni kategori seç.</p></div>
      <div class="modal-body"><div class="chip-row">${buttons}</div></div>
      <div class="modal-foot"><button class="btn btn-ghost" data-close>Vazgeç</button></div>`);
    document.querySelector("[data-close]").addEventListener("click", closeModal);
    document.querySelectorAll("[data-cat]").forEach((button) => {
      button.addEventListener("click", async () => {
        try {
          const result = await api.mailBulk([...this.checked], "category", button.dataset.cat);
          closeModal();
          toast("Kategori güncellendi", `${result.updated} mail`, "ok");
          this.clearSelection();
          await this.load();
        } catch (error) {
          toast("Değiştirilemedi", error.message, "err");
        }
      });
    });
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
          <label class="mail-check" title="Seç">
            <input type="checkbox" ${this.checked.has(message.id) ? "checked" : ""}>
            <span class="box" aria-hidden="true"></span>
          </label>
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
      // Checkbox tıklaması maili AÇMAMALI — sadece seçer.
      const box = node.querySelector(".mail-check input");
      box.addEventListener("click", (event) => {
        event.stopPropagation();
        this.toggleCheck(message.id, box.checked);
        node.classList.toggle("is-checked", box.checked);
      });
      node.addEventListener("click", () => this.select(message.id));
      if (this.checked.has(message.id)) node.classList.add("is-checked");
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

    const index = this.messages.findIndex((row) => row.id === message.id);
    const hasPrev = index > 0;
    const hasNext = index >= 0 && index < this.messages.length - 1;

    this.detailEl.innerHTML = `
      <div class="mail-detail-head">
        <div class="mail-nav">
          <span class="cat cat-${category}">${escapeHtml(this.app.categories[category] || category)}</span>
          <span class="nav-gap"></span>
          <button class="icon-btn" data-act="prev" title="Önceki mail" ${hasPrev ? "" : "disabled"}>&larr;</button>
          <button class="icon-btn" data-act="next" title="Sonraki mail" ${hasNext ? "" : "disabled"}>&rarr;</button>
          <button class="icon-btn" data-act="close" title="Kapat">&#10005;</button>
        </div>
        <h2>${escapeHtml(message.subject || "(konusuz)")}</h2>
        <div class="mail-from">
          <div class="avatar" style="background:${color}">${escapeHtml(initials(message.from_name, message.from_addr))}</div>
          <div>
            <div class="who">${escapeHtml(message.from_name || message.from_addr)}</div>
            <div class="addr">${escapeHtml(message.from_addr)} · ${escapeHtml(formatDate(message.date_ts))}</div>
          </div>
        </div>
        ${message.code ? `
        <div class="code-card">
          <span class="code-label">[ KOD ]</span>
          <code class="code-value">${escapeHtml(message.code)}</code>
          <button class="btn btn-primary btn-sm" data-act="copycode">Kodu kopyala</button>
        </div>` : ""}
        <div class="mail-actions">
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
        ${message.category_reason ? `<p class="muted cat-reason">Neden bu kategori: ${escapeHtml(message.category_reason)}</p>` : ""}
        <button class="head-toggle" data-act="headsize" title="Başlığı küçült/büyüt">
          <span class="chev">▲</span>
        </button>
      </div>
      ${message.summary ? `
        <div class="summary-card">
          <h4>[ ÖZET ]${message.summary_model ? ` · ${escapeHtml(message.summary_model)}` : ""}</h4>
          <div class="summary-text">${markdown(message.summary)}</div>
        </div>` : ""}
      ${message.ics_payload ? '<div class="invite-card" id="invite-card"><h4>[ TAKVİM DAVETİ ]</h4><div class="invite-row">okunuyor…</div></div>' : ""}
      ${attachments.length ? `
        <div class="summary-card" style="background:var(--bg-1);border-color:var(--line)">
          <h4 style="color:var(--text-3)">[ EKLER ] ${attachments.length}</h4>
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

    this.bindHeaderCollapse();

    if (message.ics_payload) this.loadInvite(message.id);
  }

  /** Aşağı kaydırınca başlık barı küçülsün — mail gövdesine yer açılsın.
   *
   * Kullanıcı bildirdi: "mailin aşağı doğru büyük bir kısmı görünmüyor".
   * Bar `position: sticky` olduğu için ekranın üstünde duruyor ve uzun
   * araç satırıyla birlikte ~180px yer kaplıyordu. Artık 60px'den sonra
   * kendiliğinden daralıyor; kullanıcı sağ alttaki düğmeyle sabitleyebilir.
   */
  bindHeaderCollapse() {
    const head = this.detailEl.querySelector(".mail-detail-head");
    if (!head) return;
    // Elle seçim otomatik davranışı ezer; yeni mail açılınca sıfırlanır.
    this.headPinned = null;

    const apply = () => {
      const compact = this.headPinned === null ? this.detailEl.scrollTop > 60 : this.headPinned;
      head.classList.toggle("is-compact", compact);
    };
    this.detailEl.addEventListener("scroll", apply, { passive: true });
    head.querySelector("[data-act='headsize']").addEventListener("click", (event) => {
      event.stopPropagation();
      this.headPinned = !head.classList.contains("is-compact");
      apply();
    });
    apply();
  }

  async loadInvite(messageId) {
    try {
      const draft = await api.calendarDraftFromMail(messageId);
      const card = document.getElementById("invite-card");
      if (!card) return;
      card.innerHTML = `
        <h4>[ TAKVİM DAVETİ ]</h4>
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

  /** Listede bir sonraki/önceki maile geç (detay üstündeki oklar). */
  step(delta) {
    const index = this.messages.findIndex((row) => row.id === this.selected?.id);
    const target = this.messages[index + delta];
    if (target) this.select(target.id);
  }

  closeDetail() {
    this.selected = null;
    this.detailEl.innerHTML = "";
    this.layoutEl.classList.remove("show-detail");
    this.app.setDockContext("mail", null);
    this.renderList();
  }

  async action(kind, message) {
    try {
      if (kind === "prev") return this.step(-1);
      if (kind === "next") return this.step(1);
      if (kind === "close") return this.closeDetail();
      if (kind === "copycode") {
        await navigator.clipboard.writeText(message.code);
        toast("Kopyalandı", message.code, "ok");
        return;
      }
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
