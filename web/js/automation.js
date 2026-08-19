/* Otomasyon görünümü: sohbet + adımlar (sol), canlı tarayıcı (sağ).
 *
 * Tek bir WebSocket (`/ws/browser`) her şeyi taşıyor: canlı ekran kareleri
 * aşağı, kullanıcının tıklaması/yazması ve çalıştırma komutları yukarı.
 * Ayrı bir kanal açmıyoruz — ekran akışıyla komutların sırası bozulmasın.
 *
 * Canlı görüntüde koordinat çevirisi ÖNEMLİ: sunucudan gelen JPEG kareler
 * tarayıcının kendi boyutunda (1280x800), `<img>` ise panele sığacak kadar
 * küçültülmüş. Kullanıcının tıkladığı yeri olduğu gibi göndermek, sayfada
 * bambaşka bir yere tıklamak demek olurdu.
 */

import { api } from "./api.js";
import { closeModal, escapeHtml, openModal, toast } from "./util.js";

// Adım türleri: kullanıcı ekleme/düzenleme sırasında seçiyor, listede
// rozet olarak görünüyor. Planlayıcı da bunları üretiyor.
const KINDS = {
  sayfa: "sayfa aç",
  oku: "oku",
  yaz: "yaz",
  tikla: "tıkla",
  bekle: "bekle",
  kontrol: "kontrol",
  islem: "işlem",
};

const STATUS_MARK = {
  bekliyor: "[ ]", calisiyor: "[~]", tamam: "[X]", hata: "[!]", atlandi: "[-]",
};

export class AutomationView {
  constructor(app) {
    this.app = app;
    this.socket = null;
    this.current = null;
    this.automations = [];
    this.steps = [];
    this.loaded = false;
    this.frameSize = { width: 1280, height: 800 };

    this.chatEl = document.getElementById("auto-chat");
    this.stepsEl = document.getElementById("auto-steps");
    this.frameEl = document.getElementById("auto-frame");
    this.emptyEl = document.getElementById("auto-empty");
    this.statusEl = document.getElementById("auto-status");
    this.selectEl = document.getElementById("auto-select");

    this.bind();
  }

  async activate() {
    if (!this.loaded) {
      this.loaded = true;
      await this.load();
    }
  }

  /* ------------------------------------------------------------- veri */

  async load() {
    const data = await api.automations().catch(() => ({ automations: [] }));
    this.automations = data.automations || [];
    if (!this.automations.length) {
      this.automations = [await api.automationCreate({
        name: "Otomasyon 1",
        allowlist: ["mail.google.com", "docs.google.com", "google.com"],
      })];
    }
    this.selectEl.innerHTML = this.automations
      .map((item) => `<option value="${item.id}">${escapeHtml(item.name)}</option>`)
      .join("");
    await this.select(this.current?.id || this.automations[0].id);
  }

  async select(id) {
    const detail = await api.automation(id);
    this.current = detail;
    this.steps = detail.steps || [];
    this.selectEl.value = String(id);
    document.getElementById("auto-url").value = detail.start_url || "";
    this.renderSteps();
    this.renderChat();
  }

  /* ------------------------------------------------------------ olaylar */

  bind() {
    this.selectEl.addEventListener("change", () => this.select(Number(this.selectEl.value)));

    document.getElementById("auto-new").addEventListener("click", () => this.newDialog());
    document.getElementById("auto-edit").addEventListener("click", () => this.editDialog());
    document.getElementById("auto-del").addEventListener("click", () => this.deleteDialog());

    document.getElementById("auto-open").addEventListener("click", () => this.openBrowser());
    document.getElementById("auto-go").addEventListener("click", () => this.go());
    document.getElementById("auto-login").addEventListener("click", async () => {
      try {
        const result = await api.browserLogin();
        toast("Giriş penceresi açıldı", result.detail || "", "ok");
      } catch (error) {
        toast("Açılamadı", error.message, "err");
      }
    });

    document.getElementById("auto-form").addEventListener("submit", (event) => {
      event.preventDefault();
      this.plan();
    });
    document.getElementById("auto-add-step").addEventListener("click", () => this.addStepDialog());
    document.getElementById("auto-run").addEventListener("click", () => this.run());
    document.getElementById("auto-stop").addEventListener("click", () => this.send({ type: "stop" }));

    document.getElementById("auto-tabs").addEventListener("change", (event) => {
      this.send({ type: "focus_tab", target_id: event.target.value });
    });

    this.bindScreenInput();
    this.bindSplitter();
  }

  /** Canlı görüntü üzerinde kullanıcının kendi tıklaması/yazması. */
  bindScreenInput() {
    this.frameEl.addEventListener("click", (event) => {
      const rect = this.frameEl.getBoundingClientRect();
      // Ekran koordinatı → tarayıcı koordinatı (görüntü küçültülmüş).
      const scale = this.frameSize.width / rect.width;
      this.send({
        type: "input", event: "click",
        x: Math.round((event.clientX - rect.left) * scale),
        y: Math.round((event.clientY - rect.top) * scale),
      });
    });

    document.getElementById("auto-screen").addEventListener("wheel", (event) => {
      if (!this.socket) return;
      event.preventDefault();
      this.send({ type: "input", event: "scroll", dy: Math.round(event.deltaY) });
    }, { passive: false });

    // Görüntü odaktayken klavye doğrudan tarayıcıya gider.
    this.frameEl.tabIndex = 0;
    this.frameEl.addEventListener("keydown", (event) => {
      if (!this.socket) return;
      const named = ["Enter", "Tab", "Escape", "Backspace", "Delete",
                     "ArrowDown", "ArrowUp", "ArrowLeft", "ArrowRight"];
      if (named.includes(event.key)) {
        event.preventDefault();
        this.send({ type: "input", event: "key", key: event.key });
      } else if (event.key.length === 1) {
        event.preventDefault();
        this.send({ type: "input", event: "text", text: event.key });
      }
    });
  }

  /* ------------------------------------------------------------- soket */

  connect() {
    if (this.socket && this.socket.readyState <= 1) return;
    const protocol = location.protocol === "https:" ? "wss" : "ws";
    this.socket = new WebSocket(`${protocol}://${location.host}/ws/browser`);
    this.socket.onmessage = (message) => this.handle(JSON.parse(message.data));
    this.socket.onclose = () => {
      this.socket = null;
      this.setStatus("kapalı");
    };
  }

  /** Soket açılana kadar bekleyip gönder (erken giden mesaj kaybolur). */
  whenReady(action, tries = 60) {
    if (this.socket && this.socket.readyState === WebSocket.OPEN) {
      action();
      return;
    }
    if (tries <= 0) {
      toast("Bağlanamadı", "Tarayıcı kanalı açılmadı.", "err");
      return;
    }
    setTimeout(() => this.whenReady(action, tries - 1), 100);
  }

  send(payload) {
    if (this.socket && this.socket.readyState === WebSocket.OPEN) {
      this.socket.send(JSON.stringify(payload));
    }
  }

  handle(event) {
    switch (event.type) {
      case "frame":
        this.frameEl.hidden = false;
        this.emptyEl.hidden = true;
        this.frameEl.src = `data:image/jpeg;base64,${event.data}`;
        this.setStatus("canlı");
        break;
      case "steps":
        this.steps = event.steps || [];
        this.renderSteps();
        this.addChat("bot", `${this.steps.length} adımlık plan hazır. Adımları gözden geçir, sonra “Çalıştır”.`);
        break;
      case "step_update": {
        const step = this.steps.find((item) => item.id === event.id);
        if (step) {
          step.status = event.status;
          step.detail = event.detail;
          step.used_model = event.model;
        }
        this.renderSteps();
        break;
      }
      case "approval_request":
        this.askApproval(event);
        break;
      case "run_done": {
        document.getElementById("auto-stop").hidden = true;
        // Sunucudan gelen SON durumu kullan; yoksa yerel listeyi toparla.
        if (event.steps) this.steps = event.steps;
        this.steps.forEach((step) => {
          if (step.status === "calisiyor") step.status = "bekliyor";
        });
        this.renderSteps();

        const etiket = { tamam: "TAMAMLANDI", hata: "HATA", yarida: "YARIDA KESİLDİ",
                         durduruldu: "DURDURULDU" }[event.status] || event.status;
        const tamam = this.steps.filter((s) => s.status === "tamam").length;
        this.addChat("bot", `▚ ${etiket} — ${tamam}/${this.steps.length} adım tamam`);
        toast(
          `Otomasyon ${etiket.toLowerCase()}`,
          `${tamam}/${this.steps.length} adım`,
          event.status === "tamam" ? "ok" : "warn"
        );
        this.setStatus(event.status === "tamam" ? "bitti" : etiket.toLowerCase());
        break;
      }
      case "error":
        this.addChat("bot", `Hata: ${event.message}`);
        toast("Otomasyon hatası", event.message, "err");
        break;
      case "blocked":
        this.askAllow(event);
        break;
      case "allowlist":
        if (this.current) this.current.allowlist = event.allowlist;
        toast("İzin verildi", event.allowlist.join(", "), "ok");
        break;
      case "tabs":
        this.renderTabs(event.tabs || []);
        break;
      case "log":
        this.addLog(event.text, event.level);
        break;
      case "state":
        this.setStatus(event.running ? "açık" : "kapalı");
        if (!event.running) {
          // Kullanıcı gerçek pencereyi kapattı: son kareyi göstermeye
          // devam etmek "hâlâ açık" izlenimi veriyordu.
          this.frameEl.hidden = true;
          this.frameEl.removeAttribute("src");
          this.emptyEl.hidden = false;
          if (event.closed) this.addChat("bot", "Tarayıcı kapatıldı.");
        }
        break;
      default:
        break;
    }
  }

  /* ------------------------------------------------------------ eylemler */

  async openBrowser() {
    this.connect();
    // Soketin açılmasını bekle; komut erken giderse kaybolur.
    for (let i = 0; i < 50 && this.socket?.readyState !== WebSocket.OPEN; i += 1) {
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
    this.setStatus("açılıyor…");
    this.send({
      type: "start",
      automation_id: this.current.id,
      url: document.getElementById("auto-url").value.trim(),
    });
  }

  async go() {
    const url = document.getElementById("auto-url").value.trim();
    if (!url) return;
    await api.automationUpdate(this.current.id, { start_url: url });
    this.connect();
    this.send({ type: "start", automation_id: this.current.id, url });
  }

  plan() {
    const input = document.getElementById("auto-input");
    const goal = input.value.trim();
    if (!goal) return;
    // Tarayıcı kapalıysa sunucu kendisi açıyor ve başlangıç adresine
    // gidiyor (kullanıcının isteği: "sayfa açık değilse bile açsın").
    this.connect();
    this.addChat("user", goal);
    this.addChat("bot", "Sayfaya bakıp adımları çıkarıyorum…");
    input.value = "";
    // Soket yeni açıldıysa hazır olmasını bekle; erken giden mesaj kaybolur.
    this.whenReady(() => this.send({ type: "plan", automation_id: this.current.id, goal }));
  }

  run({ only = null, from = 0 } = {}) {
    if (!this.steps.length) {
      toast("Adım yok", "Önce ne yapılacağını yaz.", "warn");
      return;
    }
    this.connect();
    document.getElementById("auto-stop").hidden = false;
    this.whenReady(() => this.send({
      type: "run",
      automation_id: this.current.id,
      step_by_step: document.getElementById("auto-stepwise").checked,
      only_step: only,
      from_step: from,
    }));
    if (only) this.addChat("bot", "Tek adım deneniyor…");
    else if (from) this.addChat("bot", `${from + 1}. adımdan devam ediliyor…`);
  }

  askApproval(event) {
    const node = document.createElement("div");
    node.className = "auto-approval";
    node.innerHTML = `
      <div class="ask">
        <b>Onay:</b> ${escapeHtml(event.intent)}
        <div class="what">${escapeHtml(event.action)}</div>
      </div>
      <div class="row">
        <button class="btn btn-primary btn-sm" data-yes>Uygula</button>
        <button class="btn btn-ghost btn-sm" data-no>Atla</button>
      </div>`;
    this.chatEl.appendChild(node);
    this.chatEl.scrollTop = this.chatEl.scrollHeight;
    const answer = (approved) => {
      this.send({ type: "approval_response", id: event.id, approved });
      node.remove();
    };
    node.querySelector("[data-yes]").addEventListener("click", () => answer(true));
    node.querySelector("[data-no]").addEventListener("click", () => answer(false));
  }

  /* -------------------------------------------------------------- çizim */

  setStatus(text) {
    this.statusEl.textContent = text;
  }

  /** Canlı ilerleme satırı: hangi aşamada, ne yaptı, nerede takıldı. */
  addLog(text, level = "") {
    const node = document.createElement("div");
    node.className = `auto-log ${level === "err" ? "is-err" : ""}`;
    const time = new Date().toLocaleTimeString("tr-TR", { hour12: false });
    node.textContent = `${time}  ${text}`;
    this.chatEl.appendChild(node);
    this.chatEl.scrollTop = this.chatEl.scrollHeight;
    // Günlük şişmesin: en fazla 200 satır tutuluyor.
    const logs = this.chatEl.querySelectorAll(".auto-log");
    if (logs.length > 200) logs[0].remove();
  }

  addChat(role, text) {
    const node = document.createElement("div");
    node.className = `auto-msg role-${role}`;
    node.textContent = text;
    this.chatEl.appendChild(node);
    this.chatEl.scrollTop = this.chatEl.scrollHeight;
  }

  renderChat() {
    this.chatEl.innerHTML = "";
    if (this.current?.goal) this.addChat("user", this.current.goal);
  }

  renderSteps() {
    if (!this.steps.length) {
      this.stepsEl.innerHTML =
        '<p class="muted">Henüz adım yok. Yukarıya ne yapılacağını yaz.</p>';
      return;
    }
    this.stepsEl.innerHTML = this.steps.map((step, index) => `
      <div class="auto-step is-${step.status}" data-step="${step.id}">
        <span class="mark">${STATUS_MARK[step.status] || "[ ]"}</span>
        <span class="n">${index + 1}.</span>
        <span class="intent">
          <span class="kind kind-${escapeHtml(step.kind || "islem")}">${escapeHtml(KINDS[step.kind] || step.kind || "işlem")}</span>
          ${escapeHtml(step.intent)}
        </span>
        <span class="tools">
          <button class="icon-btn" data-up title="Yukarı taşı">▲</button>
          <button class="icon-btn" data-down title="Aşağı taşı">▼</button>
          <button class="icon-btn" data-only title="Sadece bu adımı dene">▷</button>
          <button class="icon-btn" data-from title="Buradan itibaren devam et">▶▶</button>
          <button class="icon-btn" data-edit title="Düzenle">✎</button>
          <button class="icon-btn" data-del title="Sil">&#10005;</button>
        </span>
        ${step.detail ? `<div class="detail">${escapeHtml(String(step.detail).slice(0, 300))}</div>` : ""}
        ${step.last_error ? `<div class="detail err">${escapeHtml(step.last_error)}</div>` : ""}
      </div>`).join("");

    this.stepsEl.querySelectorAll("[data-step]").forEach((node) => {
      const id = Number(node.dataset.step);
      node.querySelector("[data-del]").addEventListener("click", async () => {
        await api.automationStepDelete(id);
        this.steps = this.steps.filter((step) => step.id !== id);
        this.renderSteps();
      });
      node.querySelector("[data-edit]").addEventListener("click", () => this.editStep(id));
      // Bir adımı tek başına denemek, düzeltmek ve oradan devam etmek:
      // otomasyonu baştan çalıştırmadan hata ayıklamanın tek pratik yolu.
      node.querySelector("[data-up]").addEventListener("click", () => this.moveStep(id, -1));
      node.querySelector("[data-down]").addEventListener("click", () => this.moveStep(id, 1));
      node.querySelector("[data-only]").addEventListener("click", () => this.run({ only: id }));
      node.querySelector("[data-from]").addEventListener("click", () => {
        const step = this.steps.find((item) => item.id === id);
        this.run({ from: step ? step.position : 0 });
      });
    });
  }

  async moveStep(id, delta) {
    try {
      const result = await api.automationStepMove(id, delta);
      this.steps = result.steps || this.steps;
      this.renderSteps();
    } catch (error) {
      toast("Taşınamadı", error.message, "err");
    }
  }

  addStepDialog() {
    this.formDialog({
      title: "[ ADIM EKLE ]",
      submitLabel: "Ekle",
      fields: [
        { name: "intent", label: "Adım ne yapsın?",
          hint: "Örn. “Tabloda son dolu satıra git (ctrl+end)”" },
        { name: "kind", type: "select", label: "Tür", value: "islem",
          options: Object.entries(KINDS) },
        { name: "position", label: "Sıra (boş = sona ekle)", value: "" },
      ],
      onSubmit: async ({ intent, kind, position }) => {
        if (!intent) return;
        await api.automationStepAdd(this.current.id, {
          intent, kind,
          position: position === "" ? null : Number(position) - 1,
        });
        await this.select(this.current.id);
        this.addChat("bot", `Adım eklendi: ${intent}`);
      },
    });
  }

  /** Engellenen adres: tek tıkla izin ver.
   *
   * Güvenlik sınırı korunuyor — ekleyen İNSAN, ajan değil. Ama akış
   * kopmuyor: eskiden her engelde ayarları açıp adresi elle yazmak
   * gerekiyordu ve kullanıcı "sürekli izin dışında diyor" dedi.
   */
  askAllow(event) {
    const node = document.createElement("div");
    node.className = "auto-approval";
    node.innerHTML = `
      <div class="ask">
        <b>İzinli değil:</b> ${escapeHtml(event.host || event.url)}
        <div class="what">İzinliler: ${escapeHtml((event.allowlist || []).join(", ") || "(boş)")}</div>
      </div>
      <div class="row">
        <button class="btn btn-primary btn-sm" data-yes>${escapeHtml(event.host)} için izin ver</button>
        <button class="btn btn-ghost btn-sm" data-no>Vazgeç</button>
      </div>`;
    this.chatEl.appendChild(node);
    this.chatEl.scrollTop = this.chatEl.scrollHeight;
    node.querySelector("[data-yes]").addEventListener("click", () => {
      this.send({ type: "allow_host", automation_id: this.current.id, host: event.host });
      node.remove();
      this.addChat("bot", `${event.host} izinli sitelere eklendi. Tekrar dene.`);
    });
    node.querySelector("[data-no]").addEventListener("click", () => node.remove());
  }

  renderTabs(tabs) {
    const select = document.getElementById("auto-tabs");
    if (!select) return;
    select.hidden = tabs.length < 2;
    select.innerHTML = tabs.map((tab) => `
      <option value="${escapeHtml(tab.id)}" ${tab.active ? "selected" : ""}>
        ${escapeHtml((tab.title || tab.url || "sekme").slice(0, 40))}
      </option>`).join("");
  }

  /* ---------------------------------------------------------- diyaloglar
   *
   * `window.prompt`/`confirm` KULLANILMIYOR: WebKit onları işletim
   * sisteminin kendi penceresiyle çiziyor ve uygulamanın teması, kare
   * köşeleri, yazı tipi hiçbiri geçerli olmuyor — kullanıcı bunu ekran
   * görüntüsüyle bildirdi ("programda olan sistemle gelen bişey değil").
   */

  formDialog({ title, fields, submitLabel, onSubmit }) {
    openModal(`
      <div class="modal-head"><h3>${escapeHtml(title)}</h3></div>
      <div class="modal-body">
        ${fields.map((field) => field.type === "select" ? `
          <label class="dlg-field">
            <span>${escapeHtml(field.label)}</span>
            <select data-field="${field.name}">
              ${field.options.map(([value, label]) => `
                <option value="${escapeHtml(value)}" ${value === field.value ? "selected" : ""}>
                  ${escapeHtml(label)}</option>`).join("")}
            </select>
          </label>` : field.type === "list" ? `
          <div class="dlg-field">
            <span>${escapeHtml(field.label)}</span>
            ${field.hint ? `<em>${escapeHtml(field.hint)}</em>` : ""}
            <div class="dlg-list" data-list="${field.name}">
              ${(field.value?.length ? field.value : [""]).map((item) => `
                <div class="dlg-row">
                  <input type="text" value="${escapeHtml(item)}"
                         placeholder="mail.google.com" />
                  <button type="button" class="icon-btn" data-row-del title="Kaldır">&#10005;</button>
                </div>`).join("")}
            </div>
            <button type="button" class="btn btn-ghost btn-sm" data-row-add>+ Site ekle</button>
          </div>` : `
          <label class="dlg-field">
            <span>${escapeHtml(field.label)}</span>
            ${field.hint ? `<em>${escapeHtml(field.hint)}</em>` : ""}
            <input type="text" data-field="${field.name}"
                   value="${escapeHtml(field.value || "")}" />
          </label>`).join("")}
      </div>
      <div class="modal-foot">
        <button class="btn btn-ghost" data-cancel>Vazgeç</button>
        <button class="btn btn-primary" data-ok>${escapeHtml(submitLabel)}</button>
      </div>`);

    const values = () => {
      const data = Object.fromEntries(
        [...document.querySelectorAll("[data-field]")].map((input) => [
          input.dataset.field, input.value.trim(),
        ])
      );
      // Liste alanları: her satır bir değer. Virgülle ayırmak yerine satır
      // satır — kullanıcının isteği ("1. link, + ya bastık, 2. link").
      document.querySelectorAll("[data-list]").forEach((host) => {
        data[host.dataset.list] = [...host.querySelectorAll("input")]
          .map((input) => input.value.trim())
          .filter(Boolean);
      });
      return data;
    };

    const bindRows = () => {
      document.querySelectorAll("[data-row-del]").forEach((button) => {
        button.onclick = () => {
          const list = button.closest("[data-list]");
          if (list.querySelectorAll(".dlg-row").length > 1) button.closest(".dlg-row").remove();
          else list.querySelector("input").value = "";
        };
      });
    };
    document.querySelectorAll("[data-row-add]").forEach((button) => {
      button.onclick = () => {
        const list = button.previousElementSibling;
        const row = document.createElement("div");
        row.className = "dlg-row";
        row.innerHTML = '<input type="text" placeholder="docs.google.com">'
          + '<button type="button" class="icon-btn" data-row-del>&#10005;</button>';
        list.appendChild(row);
        row.querySelector("input").focus();
        bindRows();
      };
    });
    bindRows();
    const first = document.querySelector("[data-field]");
    if (first) { first.focus(); first.select(); }
    document.querySelector("[data-cancel]").addEventListener("click", closeModal);
    document.querySelector("[data-ok]").addEventListener("click", async () => {
      const data = values();
      closeModal();
      await onSubmit(data);
    });
  }

  async newDialog() {
    this.formDialog({
      title: "[ YENİ OTOMASYON ]",
      submitLabel: "Oluştur",
      fields: [
        { name: "name", label: "Ad", value: `Otomasyon ${this.automations.length + 1}` },
        {
          name: "sites", type: "list", label: "İzinli siteler",
          hint: "Ajan yalnızca bu sitelerde çalışabilir. Her satıra bir site.",
          value: ["mail.google.com", "docs.google.com"],
        },
      ],
      onSubmit: async ({ name, sites }) => {
        if (!name) return;
        const created = await api.automationCreate({ name, allowlist: sites });
        this.current = created;
        await this.load();
      },
    });
  }

  editDialog() {
    if (!this.current) return;
    this.formDialog({
      title: "[ OTOMASYON AYARLARI ]",
      submitLabel: "Kaydet",
      fields: [
        { name: "name", label: "Ad", value: this.current.name },
        {
          name: "sites", type: "list", label: "İzinli siteler",
          hint: "Ajan yalnızca bu sitelerde çalışabilir. Her satıra bir site.",
          value: this.current.allowlist || [],
        },
        { name: "start_url", label: "Başlangıç adresi", value: this.current.start_url || "" },
      ],
      onSubmit: async ({ name, sites, start_url }) => {
        try {
          await api.automationUpdate(this.current.id, { name, start_url, allowlist: sites });
          await this.load();
          toast("Kaydedildi", "", "ok");
        } catch (error) {
          // En sık hata: bütün satırlar boş bırakılmış. Sunucu reddediyor,
          // kullanıcı sebebini görsün ve eski liste yerinde kalsın.
          toast("Kaydedilemedi", error.message, "err");
        }
      },
    });
  }

  deleteDialog() {
    if (!this.current) return;
    const name = this.current.name;
    openModal(`
      <div class="modal-head"><h3>[ SİL ]</h3>
        <p>“${escapeHtml(name)}” ve adımları silinecek.</p></div>
      <div class="modal-foot">
        <button class="btn btn-ghost" data-cancel>Vazgeç</button>
        <button class="btn btn-danger" data-ok>Sil</button>
      </div>`);
    document.querySelector("[data-cancel]").addEventListener("click", closeModal);
    document.querySelector("[data-ok]").addEventListener("click", async () => {
      closeModal();
      await api.automationDelete(this.current.id);
      this.current = null;
      await this.load();
    });
  }

  /** "Düzenle": adımın cümlesini revize ettiriyoruz. */
  editStep(id) {
    const step = this.steps.find((item) => item.id === id);
    if (!step) return;
    this.formDialog({
      title: "[ ADIMI DÜZENLE ]",
      submitLabel: "Kaydet",
      fields: [
        {
          name: "intent", label: "Bu adım ne yapsın?",
          hint: "Cümleyi değiştirince kayıtlı somut eylem düşer, adım yeniden çözülür.",
          value: step.intent,
        },
        { name: "kind", type: "select", label: "Tür",
          value: step.kind || "islem", options: Object.entries(KINDS) },
      ],
      onSubmit: async ({ intent, kind }) => {
        if (!intent) return;
        try {
          await api.automationStepUpdate(id, { intent, kind });
          step.intent = intent;
          step.kind = kind;
          step.status = "bekliyor";
          step.action = null;
          this.renderSteps();
          this.addChat("bot", `Adım güncellendi: ${intent}`);
        } catch (error) {
          toast("Güncellenemedi", error.message, "err");
        }
      },
    });
  }

  /* ------------------------------------------------------ ayırıcı çubuk */

  bindSplitter() {
    const splitter = document.getElementById("auto-splitter");
    const layout = document.querySelector(".auto-layout");
    if (!splitter || !layout) return;

    const saved = Number(localStorage.getItem("auto-split") || 0);
    if (saved >= 260) layout.style.gridTemplateColumns = `${saved}px 6px 1fr`;

    let dragging = false;
    const move = (event) => {
      if (!dragging) return;
      const left = event.clientX - layout.getBoundingClientRect().left;
      // Sınırlar: sol panel okunamayacak kadar daralmasın, tarayıcı da
      // ekranın tamamını yutmasın.
      const width = Math.max(260, Math.min(left, layout.clientWidth - 320));
      layout.style.gridTemplateColumns = `${width}px 6px 1fr`;
      localStorage.setItem("auto-split", String(Math.round(width)));
    };
    const stop = () => {
      dragging = false;
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
    };

    splitter.addEventListener("mousedown", (event) => {
      event.preventDefault();
      dragging = true;
      // Sürüklerken imleç ve seçim kilitleniyor; yoksa fare panelin
      // dışına çıkınca metin seçmeye başlıyor ve sürükleme kopuyor.
      document.body.style.cursor = "col-resize";
      document.body.style.userSelect = "none";
    });
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", stop);
  }
}
