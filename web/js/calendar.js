/* Takvim paneli: ay ızgarası + seçili gün + maildeki bekleyen toplantılar.
 *
 * Bu uygulamanın kendi takvimi — Google/CalDAV yok. Dışa aktarım ICS ile.
 * Hatırlatmalar backend'in `background.reminder_loop`'undan, dunst üzerinden
 * gidiyor; burada sadece kaçıncı dakika önce olduğu ayarlanıyor.
 */

import { api } from "./api.js";
import {
  DAYS_SHORT, ICONS, MONTHS, closeModal, dayKey, el, emptyState, escapeHtml,
  formatDate, formatEventTime, openModal, pad, parseDate, toLocalInput, toast,
} from "./util.js";

export class CalendarView {
  constructor(app) {
    this.app = app;
    this.cursor = new Date();
    this.cursor.setDate(1);
    this.selectedDay = new Date();
    this.events = [];
    this.pending = [];
    this.dirty = true;

    this.gridEl = document.getElementById("cal-grid");
    this.titleEl = document.getElementById("cal-title");
    this.dayEl = document.getElementById("cal-day");
    this.pendingEl = document.getElementById("cal-pending");

    document.getElementById("cal-prev").addEventListener("click", () => this.move(-1));
    document.getElementById("cal-next").addEventListener("click", () => this.move(1));
    document.getElementById("cal-today").addEventListener("click", () => {
      this.cursor = new Date();
      this.cursor.setDate(1);
      this.selectedDay = new Date();
      this.load();
    });
    document.getElementById("cal-new").addEventListener("click", () => this.editDialog(null));
    document.getElementById("cal-export").addEventListener("click", () => this.export());
  }

  invalidate() {
    this.dirty = true;
  }

  async activate() {
    if (this.dirty) await this.load();
  }

  move(delta) {
    this.cursor.setMonth(this.cursor.getMonth() + delta);
    this.load();
  }

  /* -------------------------------------------------------------- veri */

  async load() {
    this.dirty = false;
    // Izgara komşu ayların günlerini de gösteriyor; aralığı geniş tut.
    const from = new Date(this.cursor.getFullYear(), this.cursor.getMonth(), -7);
    const to = new Date(this.cursor.getFullYear(), this.cursor.getMonth() + 1, 14);
    try {
      const [data, pending] = await Promise.all([
        api.calendarEvents(from.toISOString(), to.toISOString()),
        api.calendarPending().catch(() => ({ pending: [] })),
      ]);
      this.events = data.events || [];
      this.pending = pending.pending || [];
      this.app.updateCalendarBadge(data.stats?.today || 0);
    } catch (error) {
      toast("Takvim yüklenemedi", error.message, "err");
      this.events = [];
    }
    this.render();
  }

  eventsOn(date) {
    const key = dayKey(date);
    return this.events.filter((event) => {
      const start = parseDate(event.starts_at);
      return start && dayKey(start) === key;
    });
  }

  /* ------------------------------------------------------------ çizim */

  render() {
    this.titleEl.textContent = `${MONTHS[this.cursor.getMonth()]} ${this.cursor.getFullYear()}`;

    const cells = [DAYS_SHORT.map((name) => `<div class="cal-dow">${name}</div>`).join("")];
    const firstOfMonth = new Date(this.cursor.getFullYear(), this.cursor.getMonth(), 1);
    // Hafta Pazartesi başlıyor: JS'in 0=Pazar dizinini kaydır.
    const offset = (firstOfMonth.getDay() + 6) % 7;
    const gridStart = new Date(firstOfMonth);
    gridStart.setDate(1 - offset);

    const todayKey = dayKey(new Date());
    const selectedKey = dayKey(this.selectedDay);

    for (let index = 0; index < 42; index += 1) {
      const date = new Date(gridStart);
      date.setDate(gridStart.getDate() + index);
      const key = dayKey(date);
      const dayEvents = this.eventsOn(date);
      const classes = [
        "cal-cell",
        date.getMonth() !== this.cursor.getMonth() ? "is-out" : "",
        key === todayKey ? "is-today" : "",
        key === selectedKey ? "is-selected" : "",
      ].filter(Boolean).join(" ");

      const shown = dayEvents.slice(0, 3).map((event) => {
        const start = parseDate(event.starts_at);
        const clock = event.all_day ? "" : `${pad(start.getHours())}:${pad(start.getMinutes())} `;
        return `<div class="cal-ev ${event.source === "mail" ? "from-mail" : ""}" title="${escapeHtml(event.title)}">${escapeHtml(clock + event.title)}</div>`;
      }).join("");
      const more = dayEvents.length > 3 ? `<div class="cal-more">+${dayEvents.length - 3} daha</div>` : "";

      cells.push(
        `<div class="${classes}" data-day="${key}"><span class="n">${date.getDate()}</span>${shown}${more}</div>`
      );
    }

    this.gridEl.innerHTML = cells.join("");
    this.gridEl.querySelectorAll("[data-day]").forEach((cell) => {
      cell.addEventListener("click", () => {
        const [year, month, day] = cell.dataset.day.split("-").map(Number);
        this.selectedDay = new Date(year, month - 1, day);
        this.render();
      });
      cell.addEventListener("dblclick", () => {
        const [year, month, day] = cell.dataset.day.split("-").map(Number);
        const start = new Date(year, month - 1, day, 9, 0);
        this.editDialog(null, start);
      });
    });

    this.renderDay();
    this.renderPending();
  }

  renderDay() {
    const dayEvents = this.eventsOn(this.selectedDay)
      .sort((a, b) => String(a.starts_at).localeCompare(String(b.starts_at)));

    const heading = `${this.selectedDay.getDate()} ${MONTHS[this.selectedDay.getMonth()]} ${this.selectedDay.getFullYear()}`;
    if (!dayEvents.length) {
      this.dayEl.innerHTML = `<p class="muted">${escapeHtml(heading)}<br>Bu günde etkinlik yok.</p>
        <button class="btn btn-ghost btn-sm btn-block" style="margin-top:9px" data-add>+ Bu güne ekle</button>`;
      this.dayEl.querySelector("[data-add]").addEventListener("click", () => {
        const start = new Date(this.selectedDay);
        start.setHours(9, 0, 0, 0);
        this.editDialog(null, start);
      });
      return;
    }

    this.dayEl.innerHTML =
      `<p class="muted" style="margin-bottom:9px">${escapeHtml(heading)}</p>` +
      dayEvents.map((event) => `
        <div class="ev-item">
          <div class="t">${escapeHtml(event.title)}</div>
          <div class="w">${escapeHtml(formatEventTime(event.starts_at, event.ends_at, event.all_day))}</div>
          ${event.location ? `<div class="d">📍 ${escapeHtml(event.location)}</div>` : ""}
          ${event.meeting_url ? `<div class="d">🔗 <a href="${escapeHtml(event.meeting_url)}" target="_blank" rel="noopener">bağlantı</a></div>` : ""}
          <div class="d">${event.reminder_minutes >= 0 ? `🔔 ${event.reminder_minutes} dk önce` : "🔕 hatırlatma yok"}${event.source === "mail" ? " · mailden" : ""}</div>
          <div class="acts">
            <button class="btn btn-ghost btn-sm" data-edit="${event.id}">Düzenle</button>
            <button class="btn btn-danger btn-sm" data-del="${event.id}">Sil</button>
          </div>
        </div>`).join("");

    this.dayEl.querySelectorAll("[data-edit]").forEach((button) => {
      button.addEventListener("click", () => {
        const event = this.events.find((item) => String(item.id) === button.dataset.edit);
        this.editDialog(event);
      });
    });
    this.dayEl.querySelectorAll("[data-del]").forEach((button) => {
      button.addEventListener("click", async () => {
        if (!confirm("Bu etkinlik silinecek. Geri alınamaz. Devam?")) return;
        try {
          await api.calendarDelete(Number(button.dataset.del));
          toast("Etkinlik silindi", "", "ok");
          await this.load();
        } catch (error) {
          toast("Silinemedi", error.message, "err");
        }
      });
    });
  }

  renderPending() {
    if (!this.pending.length) {
      this.pendingEl.innerHTML = '<p class="muted">Bekleyen toplantı maili yok.</p>';
      return;
    }
    this.pendingEl.innerHTML = this.pending.map((row) => `
      <div class="pending-item">
        <div class="t">${escapeHtml(row.draft.title || row.message.subject || "")}</div>
        <div class="w">🕐 ${escapeHtml(formatDate(row.draft.starts_at))}</div>
        <div class="c">${escapeHtml(row.message.from_name || row.message.from_addr)} · güven %${Math.round((row.draft.confidence || 0) * 100)}</div>
        <div class="acts" style="display:flex;gap:5px;margin-top:8px">
          <button class="btn btn-primary btn-sm" data-add="${row.message.id}">Ekle</button>
          <button class="btn btn-ghost btn-sm" data-open="${row.message.id}">Maili aç</button>
        </div>
      </div>`).join("");

    this.pendingEl.querySelectorAll("[data-add]").forEach((button) => {
      button.addEventListener("click", () => this.app.mail.toCalendar(Number(button.dataset.add)));
    });
    this.pendingEl.querySelectorAll("[data-open]").forEach((button) => {
      button.addEventListener("click", () => {
        this.app.show("mail");
        this.app.mail.select(Number(button.dataset.open));
      });
    });
  }

  /* ---------------------------------------------------------- diyalog */

  editDialog(event, defaultStart = null) {
    const isNew = !event;
    const start = event ? parseDate(event.starts_at) : defaultStart || nextHour();
    const end = event?.ends_at ? parseDate(event.ends_at) : null;

    openModal(`
      <div class="modal-head">
        <h3>${isNew ? "Yeni etkinlik" : "Etkinliği düzenle"}</h3>
        <p>Hatırlatma masaüstü bildirimi olarak gelir.</p>
      </div>
      <div class="modal-body">
        <label class="f"><span>Başlık</span><input type="text" id="f-title" value="${escapeHtml(event?.title || "")}" placeholder="Ekip toplantısı"></label>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px">
          <label class="f"><span>Başlangıç</span><input type="datetime-local" id="f-start" value="${toLocalInput(start)}"></label>
          <label class="f"><span>Bitiş</span><input type="datetime-local" id="f-end" value="${end ? toLocalInput(end) : ""}"></label>
        </div>
        <label class="f"><span>Yer</span><input type="text" id="f-loc" value="${escapeHtml(event?.location || "")}"></label>
        <label class="f"><span>Toplantı bağlantısı</span><input type="text" id="f-url" value="${escapeHtml(event?.meeting_url || "")}" placeholder="https://meet.google.com/…"></label>
        <label class="f"><span>Not</span><textarea class="field" id="f-desc" rows="3">${escapeHtml(event?.description || "")}</textarea></label>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px">
          <label class="f"><span>Hatırlatma (dk önce, -1 = kapalı)</span>
            <input type="number" id="f-rem" value="${event?.reminder_minutes ?? this.app.config.default_reminder_minutes ?? 10}" min="-1"></label>
          <label class="f"><span>Tüm gün</span>
            <select id="f-allday">
              <option value="0" ${event?.all_day ? "" : "selected"}>Hayır</option>
              <option value="1" ${event?.all_day ? "selected" : ""}>Evet</option>
            </select></label>
        </div>
      </div>
      <div class="modal-foot">
        <button class="btn btn-ghost" data-close>Vazgeç</button>
        <button class="btn btn-primary" data-save>${isNew ? "Oluştur" : "Kaydet"}</button>
      </div>`);

    document.querySelector("[data-close]").addEventListener("click", closeModal);
    document.querySelector("[data-save]").addEventListener("click", async () => {
      const payload = {
        title: document.getElementById("f-title").value.trim(),
        starts_at: document.getElementById("f-start").value,
        ends_at: document.getElementById("f-end").value,
        location: document.getElementById("f-loc").value,
        meeting_url: document.getElementById("f-url").value,
        description: document.getElementById("f-desc").value,
        reminder_minutes: Number(document.getElementById("f-rem").value),
        all_day: document.getElementById("f-allday").value === "1",
      };
      if (!payload.title) return toast("Başlık gerekli", "", "err");
      if (!payload.starts_at) return toast("Başlangıç zamanı gerekli", "", "err");
      try {
        if (isNew) await api.calendarCreate(payload);
        else await api.calendarUpdate(event.id, payload);
        closeModal();
        toast(isNew ? "Etkinlik eklendi" : "Güncellendi", "", "ok");
        await this.load();
      } catch (error) {
        toast("Kaydedilemedi", error.message, "err");
      }
    });
  }

  async export() {
    // Uygulama penceresinde indirme yok; ICS'i modal içinde gösterip
    // kopyalanabilir yapıyoruz — dosya olarak da /api/calendar/export.ics var.
    try {
      const response = await fetch("/api/calendar/export.ics");
      const text = await response.text();
      openModal(`
        <div class="modal-head"><h3>ICS dışa aktarım</h3><p>Başka bir takvim uygulamasına almak için kopyala.</p></div>
        <div class="modal-body"><pre id="ics-out">${escapeHtml(text)}</pre></div>
        <div class="modal-foot">
          <button class="btn btn-ghost" data-close>Kapat</button>
          <button class="btn btn-primary" data-copy>Panoya kopyala</button>
        </div>`);
      document.querySelector("[data-close]").addEventListener("click", closeModal);
      document.querySelector("[data-copy]").addEventListener("click", async () => {
        try {
          await navigator.clipboard.writeText(text);
          toast("Kopyalandı", `${text.length} karakter`, "ok");
        } catch {
          toast("Kopyalanamadı", "Metni elle seçip kopyalayabilirsin.", "err");
        }
      });
    } catch (error) {
      toast("Dışa aktarılamadı", error.message, "err");
    }
  }
}

function nextHour() {
  const date = new Date();
  date.setMinutes(0, 0, 0);
  date.setHours(date.getHours() + 1);
  return date;
}
