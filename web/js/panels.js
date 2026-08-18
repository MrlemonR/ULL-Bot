/* Kota, geçmiş ve ayarlar panelleri.
 *
 * Üçü de tek dosyada: hiçbiri kendi başına büyük değil ve üçü de aynı
 * "REST'ten oku, listele, bir düğmeyle değiştir" kalıbında.
 */

import { AccountDialog } from "./accounts.js";
import { api } from "./api.js";
import {
  ICONS, closeModal, el, emptyState, escapeHtml, formatDate, formatRelative,
  openModal, toast,
} from "./util.js";

/* ============================================================== KOTA ==== */

const PROVIDER_COLOR = {
  groq: "#f4626f", openrouter: "#5b8cff", gemini: "#34c98a",
  gemini_lite: "#8b6bff", ollama: "#eab24a",
};

export class QuotaView {
  constructor(app) {
    this.app = app;
    this.cardsEl = document.getElementById("quota-cards");
    this.metaEl = document.getElementById("quota-meta");
    this.graphEl = document.getElementById("usage-graph");
    this.loaded = false;

    document.getElementById("quota-refresh").addEventListener("click", () => this.load(false));
    document.getElementById("quota-probe").addEventListener("click", () => this.load(true));
  }

  async activate() {
    if (!this.loaded) {
      this.loaded = true;
      await this.load(false);
    }
  }

  async load(probe) {
    this.metaEl.innerHTML = '<span class="spin"></span> yükleniyor…';
    try {
      const [quota, usage] = await Promise.all([
        api.quota(probe),
        api.usageGraph(14).catch(() => ({ points: [] })),
      ]);
      this.metaEl.textContent =
        `Profil: ${quota.profile} · rezerv %${Math.round((quota.reserve_ratio || 0) * 100)} · ` +
        `tükenince: ${quota.fallback_behaviour}`;
      this.renderCards(quota.providers || []);
      this.renderGraph(usage.points || []);
    } catch (error) {
      this.metaEl.textContent = "";
      emptyState(this.cardsEl, ICONS.chat, "Kota okunamadı", error.message);
    }
  }

  renderCards(providers) {
    if (!providers.length) {
      return emptyState(this.cardsEl, ICONS.chat, "Sağlayıcı yok", "routing.yaml'da tanımlı sağlayıcı bulunamadı.");
    }
    this.cardsEl.innerHTML = providers.map((provider) => {
      const color = PROVIDER_COLOR[provider.provider] || "#5b8cff";
      const windows = (provider.windows || []).map((win) => {
        const ratio = Math.max(0, Math.min(1, win.free_ratio ?? 0));
        const barColor = ratio > 0.5 ? "#34c98a" : ratio > 0.2 ? "#eab24a" : "#f4626f";
        const known = win.known
          ? `${win.remaining_requests ?? "?"} / ${win.max_requests ?? "?"} istek`
          : "limit bilinmiyor";
        return `
          <div class="qwin">
            <div class="qwin-top">
              <span>${escapeHtml(win.window)} · ${escapeHtml(win.source || "")}</span>
              <span>${escapeHtml(known)}</span>
            </div>
            <div class="qbar"><i style="width:${(ratio * 100).toFixed(0)}%;background:${barColor}"></i></div>
          </div>`;
      }).join("");

      const state = provider.health === "down" ? "is-down" : "";
      const statusTag = provider.available
        ? '<span class="tag tag-ok">uygun</span>'
        : `<span class="tag tag-danger" title="${escapeHtml(provider.reason || "")}">elendi</span>`;

      return `
        <div class="qcard ${state}">
          <div class="qcard-head">
            <span class="dot" style="width:9px;height:9px;border-radius:50%;background:${color}"></span>
            <span class="name">${escapeHtml(provider.provider)}</span>
            ${statusTag}
          </div>
          <div class="model">${escapeHtml(provider.model)}${provider.configured ? "" : " · anahtar yok"}</div>
          ${windows || '<p class="muted">Kota penceresi tanımlı değil.</p>'}
          ${provider.cooldown_seconds ? `<p class="muted" style="margin-top:8px">Cooldown: ${provider.cooldown_seconds} sn — ${escapeHtml(provider.note || "")}</p>` : ""}
          ${provider.reason && !provider.available ? `<p class="muted" style="margin-top:8px">${escapeHtml(provider.reason)}</p>` : ""}
          <div class="qcard-foot">
            <button class="btn btn-ghost btn-sm" data-provider="${escapeHtml(provider.provider)}"
              data-action="${provider.health === "down" ? "enable" : "disable"}">
              ${provider.health === "down" ? "Geri aç" : "Devre dışı bırak"}
            </button>
          </div>
        </div>`;
    }).join("");

    this.cardsEl.querySelectorAll("[data-provider]").forEach((button) => {
      button.addEventListener("click", async () => {
        try {
          await api.quotaControl(button.dataset.provider, button.dataset.action);
          toast(`${button.dataset.provider} ${button.dataset.action === "disable" ? "kapatıldı" : "açıldı"}`, "", "ok");
          await this.load(false);
        } catch (error) {
          toast("Değiştirilemedi", error.message, "err");
        }
      });
    });
  }

  renderGraph(points) {
    if (!points.length) {
      this.graphEl.innerHTML = '<p class="muted">Henüz kullanım verisi yok.</p>';
      return;
    }
    const days = [...new Set(points.map((point) => point.day))].sort();
    const providers = [...new Set(points.map((point) => point.provider))].sort();
    const byDay = new Map(days.map((day) => [day, new Map()]));
    let max = 1;
    for (const point of points) {
      byDay.get(point.day)?.set(point.provider, point.requests || 0);
    }
    for (const day of days) {
      const total = [...byDay.get(day).values()].reduce((sum, value) => sum + value, 0);
      max = Math.max(max, total);
    }

    const columns = days.map((day) => {
      const segments = providers.map((provider) => {
        const value = byDay.get(day).get(provider) || 0;
        if (!value) return "";
        const height = (value / max) * 100;
        return `<div class="bar-seg" style="height:${height}%;background:${PROVIDER_COLOR[provider] || "#5b8cff"}"
          title="${escapeHtml(provider)} · ${value} istek · ${day}"></div>`;
      }).join("");
      return `<div class="bar-col">${segments}</div>`;
    }).join("");

    const labels = days.map((day) => `<div class="bar-lbl">${day.slice(5).replace("-", ".")}</div>`).join("");
    const legend = providers.map((provider) =>
      `<span><i style="background:${PROVIDER_COLOR[provider] || "#5b8cff"}"></i>${escapeHtml(provider)}</span>`
    ).join("");

    this.graphEl.innerHTML = `
      <div class="bars">${columns}</div>
      <div style="display:flex;gap:3px">${days.map(() => '<div style="flex:1"></div>').join("")}</div>
      <div style="display:flex;gap:3px">${labels.replace(/<div class="bar-lbl">/g, '<div class="bar-lbl" style="flex:1">')}</div>
      <div class="legend">${legend}</div>`;
  }
}

/* ============================================================ GEÇMİŞ ==== */

export class HistoryView {
  constructor(app) {
    this.app = app;
    this.listEl = document.getElementById("history-list");
    this.detailEl = document.getElementById("history-detail");
    this.loaded = false;

    document.getElementById("history-refresh").addEventListener("click", () => this.load());
    const search = document.getElementById("history-search");
    let timer = null;
    search.addEventListener("input", () => {
      clearTimeout(timer);
      timer = setTimeout(() => this.search(search.value.trim()), 280);
    });
  }

  async activate() {
    if (!this.loaded) {
      this.loaded = true;
      await this.load();
    }
  }

  async load() {
    try {
      const data = await api.sessions(60);
      const sessions = data.sessions || [];
      if (!sessions.length) {
        return emptyState(this.listEl, ICONS.chat, "Geçmiş boş", "Henüz kaydedilmiş bir oturum yok.");
      }
      this.listEl.innerHTML = sessions.map((session) => `
        <button class="hist-item" data-session="${escapeHtml(session.id)}">
          <div class="t">${escapeHtml(session.title || "(başlıksız)")}</div>
          <div class="m">${session.message_count} mesaj · ${escapeHtml(formatRelative(session.last_message_at || session.created_at))}</div>
        </button>`).join("");
      this.listEl.querySelectorAll("[data-session]").forEach((button) => {
        button.addEventListener("click", () => {
          this.listEl.querySelectorAll(".hist-item").forEach((item) => item.classList.remove("is-active"));
          button.classList.add("is-active");
          this.open(button.dataset.session);
        });
      });
    } catch (error) {
      emptyState(this.listEl, ICONS.chat, "Geçmiş okunamadı", error.message);
    }
  }

  async search(query) {
    if (!query) return this.load();
    try {
      const data = await api.search(query);
      const results = data.results || [];
      if (!results.length) {
        return emptyState(this.listEl, ICONS.search, "Sonuç yok", `"${query}" hiçbir mesajda geçmiyor.`);
      }
      this.listEl.innerHTML = results.map((row) => `
        <button class="hist-item" data-session="${escapeHtml(row.session_id)}">
          <div class="m">${escapeHtml(row.role)} · ${escapeHtml(formatRelative(row.ts))}</div>
          <div class="t">${escapeHtml(String(row.content || "").slice(0, 150))}</div>
        </button>`).join("");
      this.listEl.querySelectorAll("[data-session]").forEach((button) => {
        button.addEventListener("click", () => this.open(button.dataset.session));
      });
    } catch (error) {
      toast("Arama başarısız", error.message, "err");
    }
  }

  async open(sessionId) {
    this.detailEl.innerHTML = '<p class="muted"><span class="spin"></span> yükleniyor…</p>';
    try {
      const data = await api.sessionMessages(sessionId);
      const messages = data.messages || [];
      this.detailEl.innerHTML =
        `<div class="row-between"><p class="muted">${messages.length} mesaj · ${escapeHtml(sessionId.slice(0, 8))}</p>
         <button class="btn btn-ghost btn-sm" data-continue>Bu oturuma devam et</button></div>` +
        messages.map((message) => `
          <div class="hist-msg role-${escapeHtml(message.role)}">
            <div class="r">${escapeHtml(message.role)}${message.tool_name ? ` · ${escapeHtml(message.tool_name)}` : ""}${message.model ? ` · ${escapeHtml(message.model)}` : ""}</div>
            <div class="c">${escapeHtml(String(message.content || "").slice(0, 4000))}</div>
          </div>`).join("");

      this.detailEl.querySelector("[data-continue]").addEventListener("click", () => {
        this.app.resumeSession(sessionId, messages);
      });
    } catch (error) {
      this.detailEl.innerHTML = `<p class="muted">Açılamadı: ${escapeHtml(error.message)}</p>`;
    }
  }
}

/* =========================================================== AYARLAR ==== */

export class SettingsView {
  constructor(app) {
    this.app = app;
    this.bodyEl = document.getElementById("settings-body");
    this.loaded = false;
  }

  async activate() {
    await this.load();
    this.loaded = true;
  }

  async load() {
    this.bodyEl.innerHTML = '<p class="muted"><span class="spin"></span> yükleniyor…</p>';
    const [accounts, notifications, memory] = await Promise.all([
      api.mailAccounts().catch((error) => ({ accounts: [], error: error.message })),
      api.notifications().catch(() => null),
      api.memory().catch(() => ({ notes: [] })),
    ]);
    const config = this.app.config;

    this.bodyEl.innerHTML = `
      <div class="panel">
        <h3>Mail hesapları</h3>
        <div id="acct-list"></div>
        <button class="btn btn-primary btn-sm" id="acct-add" style="margin-top:10px">+ Hesap ekle</button>
        <p class="muted" style="margin-top:10px">
          Parola/jeton veritabanına yazılmaz — ${escapeHtml(accounts.secret_backend === "libsecret"
            ? "sistem anahtarlığında (libsecret) saklanır."
            : "anahtarlık bulunamadı, veri dizininde 0600 bir dosyada saklanır.")}
          Google hesapları (Gmail ve Workspace) normal parolayı kabul etmez;
          16 haneli bir <b>uygulama parolası</b> gerekiyor — hesap ekleme
          penceresi seni doğrudan o sayfaya götürüyor.
        </p>
      </div>

      <div class="panel">
        <h3>Bildirimler</h3>
        <p class="muted">
          Arka uç: <b>${escapeHtml(notifications?.backend || "yok")}</b> ·
          ${notifications?.available ? "kullanılabilir" : "bulunamadı"} ·
          kontrol aralığı ${notifications?.poll_seconds ?? "?"} sn
        </p>
        <p class="muted" style="margin-top:6px">
          Hatırlatmalar işletim sisteminin kendi bildirim sistemine gider (bu makinede dunst).
          ${notifications?.pending?.length ? `Sırada ${notifications.pending.length} hatırlatma var.` : "Sırada hatırlatma yok."}
        </p>
        <button class="btn btn-ghost btn-sm" id="notify-test" style="margin-top:10px">Test bildirimi gönder</button>
      </div>

      <div class="panel">
        <h3>Kalıcı hafıza (<code>remember</code> notları)</h3>
        <div id="memory-list"></div>
      </div>

      <div class="panel">
        <h3>Çalışma ayarları</h3>
        <dl class="kv">
          <dt>Profil</dt><dd>${escapeHtml(config.profile || "—")}</dd>
          <dt>Kuru çalışma</dt><dd>${config.dry_run ? "AÇIK — değiştiren komutlar çalıştırılmaz" : "kapalı — onaylanan komutlar gerçekten çalışır"}</dd>
          <dt>Çalışma alanı</dt><dd>${escapeHtml(config.workspace_root || "—")}</dd>
          <dt>Adım limiti</dt><dd>${config.max_agent_steps ?? "—"}</dd>
          <dt>Mail senkronu</dt><dd>${config.mail_sync_interval ? `her ${config.mail_sync_interval} sn` : "kapalı"}</dd>
          <dt>Hatırlatma</dt><dd>varsayılan ${config.default_reminder_minutes ?? 10} dk önce</dd>
        </dl>
        <p class="muted">Bu değerler <code>.env</code>'den okunuyor; değiştirmek için dosyayı düzenleyip uygulamayı yeniden başlat.</p>
      </div>`;

    this.renderAccounts(accounts.accounts || [], accounts.error);
    this.renderMemory(memory.notes || []);

    document.getElementById("acct-add").addEventListener("click", () => {
      new AccountDialog(async () => {
        await this.app.refreshConfig();
        await this.load();
        this.app.mail.loaded = false;
        this.app.mail.activate();
      }).open();
    });
    document.getElementById("notify-test").addEventListener("click", async () => {
      try {
        const result = await api.notificationTest();
        toast(result.ok ? "Bildirim gönderildi" : "Gönderilemedi", result.detail || result.backend, result.ok ? "ok" : "err");
      } catch (error) {
        toast("Gönderilemedi", error.message, "err");
      }
    });
  }

  renderAccounts(accounts, error) {
    const host = document.getElementById("acct-list");
    if (error) {
      host.innerHTML = `<p class="muted">Okunamadı: ${escapeHtml(error)}</p>`;
      return;
    }
    if (!accounts.length) {
      host.innerHTML = '<p class="muted">Henüz hesap yok. Mail panelini kullanmak için bir IMAP hesabı ekle.</p>';
      return;
    }
    host.innerHTML = accounts.map((account) => {
      return `
      <div class="ev-item">
        <div class="t">
          ${escapeHtml(account.name || account.email)}
          ${account.enabled ? "" : '<span class="cat cat-diger" style="margin-left:4px">devre dışı</span>'}
        </div>
        <div class="d">${escapeHtml(account.email)} · ${escapeHtml(account.host)}:${account.port} · ${account.use_ssl ? "SSL" : "düz"}</div>
        <div class="d">${account.last_sync_at ? `son senkron: ${escapeHtml(formatDate(account.last_sync_at))}` : "hiç senkronlanmadı"}</div>
        ${account.last_error ? `<div class="d" style="color:var(--danger)">${escapeHtml(account.last_error)}</div>` : ""}
        <div class="acts">
          <button class="btn btn-danger btn-sm" data-del-acct="${account.id}">Kaldır</button>
        </div>
      </div>`;
    }).join("");

    host.querySelectorAll("[data-del-acct]").forEach((button) => {
      button.addEventListener("click", async () => {
        if (!confirm("Hesap ve önbelleğe alınmış mailleri silinecek. Devam?")) return;
        try {
          await api.mailAccountDelete(Number(button.dataset.delAcct));
          toast("Hesap kaldırıldı", "", "ok");
          await this.app.refreshConfig();
          await this.load();
        } catch (error) {
          toast("Kaldırılamadı", error.message, "err");
        }
      });
    });
  }

  renderMemory(notes) {
    const host = document.getElementById("memory-list");
    if (!notes.length) {
      host.innerHTML = '<p class="muted">Model henüz kalıcı bir not almadı.</p>';
      return;
    }
    host.innerHTML = notes.map((note) => `
      <div class="ev-item">
        <div class="t"><code>${escapeHtml(note.key)}</code></div>
        <div class="d">${escapeHtml(note.value)}</div>
        <div class="acts"><button class="btn btn-danger btn-sm" data-del-note="${escapeHtml(note.key)}">Sil</button></div>
      </div>`).join("");

    host.querySelectorAll("[data-del-note]").forEach((button) => {
      button.addEventListener("click", async () => {
        try {
          await api.memoryDelete(button.dataset.delNote);
          toast("Not silindi", "", "ok");
          await this.load();
        } catch (error) {
          toast("Silinemedi", error.message, "err");
        }
      });
    });
  }

}
