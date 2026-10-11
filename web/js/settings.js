/* Ayarlar: Görünüm, Modeller, Kişilikler. */
import { api, stream } from "./api.js";
import { DEFAULT_APPEARANCE, applyAppearance } from "./appearance.js";
import { isInstalled, refreshModels, setModel } from "./models.js";
import { createSpinner } from "./spinner.js";
import { S, emit, on } from "./store.js";
import { toast } from "./ui.js";
import { $, $$, debounce, el, esc, fmtBytes } from "./util.js";

const body = $("#settings-body");
let page = null;

export function showSettings(name) {
  page = ["appearance", "models", "personas"].includes(name) ? name : "appearance";
  $$(".settings-nav a").forEach((a) => a.classList.toggle("is-active", a.dataset.page === page));
  body.innerHTML = "";
  body.scrollTop = 0;
  ({ appearance: renderAppearance, models: renderModels, personas: renderPersonas })[page]();
}

export function leaveSettings() {
  page = null;
}

// --- küçük kontroller ------------------------------------------------------------

function panel(title, desc, right) {
  const p = el("section", "panel");
  p.innerHTML = `<header class="panel-head"><h2>${esc(title)}</h2><span class="right"></span></header>${desc ? `<p class="panel-desc">${desc}</p>` : ""}`;
  if (right) $(".right", p).append(...[].concat(right));
  body.appendChild(p);
  return p;
}

function row(label, desc, controls) {
  const r = el("div", "set-row");
  r.innerHTML = `<div><div class="lbl">${esc(label)}</div>${desc ? `<div class="desc">${desc}</div>` : ""}</div><div class="ctl"></div>`;
  $(".ctl", r).append(...[].concat(controls));
  return r;
}

function switchCtl(value, onChange) {
  const b = el("button", "switch");
  b.type = "button";
  b.setAttribute("role", "switch");
  b.setAttribute("aria-checked", String(Boolean(value)));
  b.addEventListener("click", () => {
    const v = b.getAttribute("aria-checked") !== "true";
    b.setAttribute("aria-checked", String(v));
    onChange(v);
  });
  return b;
}

function rangeCtl(value, { min, max, step, fmt }, onChange) {
  const input = el("input");
  Object.assign(input, { type: "range", min, max, step, value });
  const val = el("span", "val", esc(fmt(value)));
  input.addEventListener("input", () => {
    const v = parseFloat(input.value);
    val.textContent = fmt(v);
    onChange(v);
  });
  return [input, val];
}

function segCtl(value, options, onChange) {
  const seg = el("div", "seg");
  options.forEach(([v, label]) => {
    const b = el("button", "", esc(label));
    b.type = "button";
    b.setAttribute("aria-pressed", String(v === value));
    b.addEventListener("click", () => {
      $$("button", seg).forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
      onChange(v);
    });
    seg.appendChild(b);
  });
  return seg;
}

function button(label, cls, onClick) {
  const b = el("button", `btn ${cls || ""}`, esc(label));
  b.type = "button";
  b.addEventListener("click", onClick);
  return b;
}

const pct = (v) => `${Math.round(v * 100)}%`;

async function saveSettings(patch) {
  try {
    S.settings = await api("/api/settings", { method: "PUT", body: patch });
    emit("settings");
  } catch (err) {
    toast("Kaydedilemedi: " + err.message, true);
  }
}

// --- GÖRÜNÜM ---------------------------------------------------------------------------

function renderAppearance() {
  const A = { ...DEFAULT_APPEARANCE, ...S.settings.appearance };
  const save = debounce(() => saveSettings({ appearance: A }), 400);
  const set = (key) => (v) => {
    A[key] = v;
    applyAppearance(A);
    save();
    if (key === "rain_enabled") $$(".dep-rain", body).forEach((r) => r.classList.toggle("is-off", !v));
  };
  const dep = (r) => {
    r.classList.add("dep-rain");
    r.classList.toggle("is-off", !A.rain_enabled);
    return r;
  };

  const rain = panel("ARKA PLAN — MATRIX YAĞMURU", "Değişiklikler anında uygulanır ve kaydedilir.");
  rain.append(
    row("Matrix yağmuru", "Arkada düşen Japonca karakterler. Kapatınca düz siyah zemin kalır.", switchCtl(A.rain_enabled, set("rain_enabled"))),
    dep(row("Hız", "Karakterlerin düşme hızı.", rangeCtl(A.rain_speed, { min: 0.1, max: 4, step: 0.1, fmt: (v) => `${v.toFixed(1)}×` }, set("rain_speed")))),
    dep(row("Yoğunluk", "Ekrandaki sütunların ne kadarında yağmur var.", rangeCtl(A.rain_density, { min: 0.1, max: 1, step: 0.05, fmt: pct }, set("rain_density")))),
    dep(row("Karakter boyu", "Küçük değer daha sık ve ince yağmur.", rangeCtl(A.rain_size, { min: 10, max: 36, step: 1, fmt: (v) => `${v}px` }, set("rain_size")))),
    dep(row("Parlaklık", "Yağmurun genel görünürlüğü.", rangeCtl(A.rain_opacity, { min: 0.05, max: 1, step: 0.05, fmt: pct }, set("rain_opacity")))),
    dep(row("Kırmızı sütunlar", "Renkli düşen sütunların oranı. 0 = tamamen siyah-beyaz.", rangeCtl(A.rain_red_ratio, { min: 0, max: 1, step: 0.01, fmt: pct }, set("rain_red_ratio")))),
  );

  const text = panel("YAZI", null);
  text.append(
    row("Yazı boyutu", "Tüm arayüz bununla birlikte ölçeklenir.", rangeCtl(A.font_size, { min: 11, max: 22, step: 1, fmt: (v) => `${v}px` }, set("font_size"))),
    row("Kalın yazı", "Mesajlar ve arayüz metni kalın.", switchCtl(A.font_bold, set("font_bold"))),
    row("Yazı tipi", "Terminal: her yer eş aralıklı (monospace).", segCtl(A.font_family, [["sans", "NORMAL"], ["mono", "TERMİNAL"]], set("font_family"))),
  );

  const color = el("input");
  color.type = "color";
  color.value = A.accent;
  color.addEventListener("input", () => set("accent")(color.value));
  const ui = panel("ARAYÜZ", null);
  ui.append(
    row("Vurgu rengi", "Kırmızı dokunuşların rengi: marka, bot çizgisi, gösterge, yağmurdaki renkli sütunlar.", color),
    row("Panel opaklığı", "Düşükken yağmur panellerin arkasından daha çok görünür.", rangeCtl(A.panel_opacity, { min: 0.3, max: 1, step: 0.02, fmt: pct }, set("panel_opacity"))),
    row("Sohbet genişliği", "Mesajların ve yazma kutusunun en fazla genişliği.", rangeCtl(A.chat_width, { min: 600, max: 1600, step: 20, fmt: (v) => `${v}px` }, set("chat_width"))),
    row("Düşünürken kelimeler", "Cooking…, Tuning…, Brewing… gibi. Kapalıysa yalnızca “Thinking…”.", switchCtl(A.spinner_words, set("spinner_words"))),
  );

  const reset = panel("SIFIRLA", "Tüm görünüm ayarlarını varsayılana döndürür.", button("VARSAYILANA DÖN", "btn-ghost", () => {
    Object.assign(A, DEFAULT_APPEARANCE);
    applyAppearance(A);
    save.flush();
    renderAppearanceAgain();
  }));
  reset.querySelector(".panel-desc").style.margin = "0";
}

function renderAppearanceAgain() {
  body.innerHTML = "";
  renderAppearance();
}

// --- MODELLER ---------------------------------------------------------------------------

const CAT_NAMES = {
  genel: "Genel sohbet", turkce: "Türkçe", kod: "Kod", mantik: "Akıl yürütme", hafif: "Hafif & hızlı", gorsel: "Resim anlama",
};
let scanData = null;
let scanFilter = "best";
const pulls = new Map(); // indirilen modeller: ad → {controller, status, completed, total}

function renderModels() {
  const installedPanel = panel("KURULU MODELLER", "Sohbette kullanılacak modeli seç. Sağ üstteki model adından da değiştirebilirsin.",
    button("↻ YENİLE", "btn-ghost", async () => { await refreshModels(); }));
  installedPanel.appendChild(el("div", "", "")).id = "installed-list";
  renderInstalled();

  const params = panel("MODEL AYARLARI", null);
  params.append(
    row("Sıcaklık (temperature)", "Düşük: tutarlı ve net. Yüksek: yaratıcı ama dağınık. Kişilik için 0.6–0.9 iyi.",
      rangeCtl(S.settings.temperature, { min: 0, max: 2, step: 0.05, fmt: (v) => v.toFixed(2) }, debounce((v) => saveSettings({ temperature: v }), 400))),
    row("Bağlam uzunluğu (hafıza)",
      "Modelin aynı anda görebildiği konuşma miktarı. Kısa olursa uzun sohbette baştakileri unutur. Büyük değer daha çok (V)RAM ister; değiştirince model yeniden yüklenir.",
      segCtl(S.settings.num_ctx, [[2048, "2K"], [4096, "4K"], [8192, "8K"], [16384, "16K"], [32768, "32K"]], (v) => saveSettings({ num_ctx: v }))),
    row("Otomatik başlık", "İlk cevaptan sonra sohbete modelin yazdığı kısa bir başlık verilir.",
      switchCtl(S.settings.auto_title, (v) => saveSettings({ auto_title: v }))),
  );

  const scanBtn = button("⌕ BİLGİSAYARI TARA", "btn-solid", () => runScan(scanBtn));
  const scan = panel("ÖNERİLER", "Bilgisayarındaki RAM'e, ekran kartına ve VRAM'e bakar; hangi modelin rahat çalışacağını ve hangi iş için iyi olduğunu söyler.", scanBtn);
  scan.appendChild(el("div")).id = "scan-out";
  if (scanData) renderScan();

  const nameIn = el("input");
  nameIn.placeholder = "model adı, örn: qwen2.5:14b";
  nameIn.spellcheck = false;
  const manualBtn = button("İNDİR", "btn", () => {
    const n = nameIn.value.trim();
    if (n) pull(n);
  });
  nameIn.addEventListener("keydown", (e) => { if (e.key === "Enter") manualBtn.click(); });
  const manual = panel("ELLE İNDİR", 'Listede olmayan bir model mi? Adını <a href="https://ollama.com/library" target="_blank" rel="noopener"><u>ollama.com/library</u></a>\'den bul ve yaz.');
  const r = el("div", "row");
  r.append(nameIn, manualBtn);
  manual.appendChild(r);
  const prog = el("div", "progress");
  prog.hidden = true;
  manual.appendChild(prog);
  nameIn.addEventListener("input", () => { prog.dataset.pull = nameIn.value.trim(); });
}

function renderInstalled() {
  const box = $("#installed-list");
  if (!box) return;
  box.innerHTML = "";
  if (!S.online) {
    box.appendChild(el("p", "status is-bad", esc(S.ollamaError || "Ollama'ya ulaşılamadı.") + " Ollama'yı başlat: <code>ollama serve</code>"));
    return;
  }
  if (!S.installed.length) {
    box.appendChild(el("p", "muted", "Kurulu model yok. Aşağıdan bilgisayarını tarayıp önerilen bir modeli indir."));
    return;
  }
  const list = el("div", "model-list");
  S.installed.forEach((m) => {
    const current = m.name === S.settings.model;
    const r = el("div", "model-row" + (current ? " is-current" : ""));
    const tags = m.tags.map((t) => CAT_NAMES[t]).filter(Boolean).join(" · ");
    r.innerHTML =
      `<div><div class="name">${esc(m.name)}${current ? ' <span class="badge red">AKTİF</span>' : ""}</div>` +
      (m.desc ? `<div class="info">${esc(m.desc)}</div>` : "") +
      `<div class="specs"><span>${fmtBytes(m.size)}</span>${m.parameter_size ? `<span>${esc(m.parameter_size)}</span>` : ""}` +
      `${m.quantization ? `<span>${esc(m.quantization)}</span>` : ""}${tags ? `<span>${esc(tags)}</span>` : ""}</div></div>` +
      `<div class="acts"></div>`;
    const acts = $(".acts", r);
    if (!current) acts.appendChild(button("KULLAN", "btn", () => setModel(m.name)));
    const del = button("SİL", "btn-danger", async () => {
      if (!del.classList.contains("is-armed")) {
        del.classList.add("is-armed");
        del.textContent = "EMİN MİSİN?";
        setTimeout(() => { del.classList.remove("is-armed"); del.textContent = "SİL"; }, 3000);
        return;
      }
      try {
        await api(`/api/models/${encodeURIComponent(m.name)}`, { method: "DELETE" });
        toast(`${m.name} silindi`);
        await refreshModels();
      } catch (err) {
        toast("Silinemedi: " + err.message, true);
      }
    });
    acts.appendChild(del);
    list.appendChild(r);
  });
  box.appendChild(list);
}

async function runScan(btn) {
  const out = $("#scan-out");
  btn.disabled = true;
  const sp = createSpinner({ label: "Scanning hardware…" });
  out.innerHTML = "";
  out.appendChild(sp.node);
  try {
    scanData = await api("/api/models/recommend");
    sp.stop();
    renderScan();
  } catch (err) {
    sp.stop();
    out.innerHTML = `<p class="status is-bad">Taranamadı: ${esc(err.message)}</p>`;
  }
  btn.disabled = false;
}

function hwCells(hw) {
  const gpuText = hw.gpus.length
    ? hw.gpus.map((g) => `${esc(g.name)}${g.vram_gb ? ` · ${g.vram_gb} GB` : ""}`).join("<br>")
    : "bulunamadı";
  const vramNote = hw.unified
    ? "birleşik bellek (Apple)"
    : hw.vram_gb ? "Ollama'nın kullanabileceği" : "modeller CPU'da çalışır — yavaş";
  return [
    ["İşletim sistemi", esc(hw.os), ""],
    ["İşlemci", esc(hw.cpu), `${hw.cores} çekirdek · ${hw.threads} iş parçacığı`],
    ["RAM", `${hw.ram_gb} GB`, `${hw.ram_free_gb} GB boş`],
    ["Ekran kartı", gpuText, ""],
    ["Kullanılabilir VRAM", `${hw.vram_gb} GB`, vramNote],
    ["Boş disk", hw.disk_free_gb != null ? `${hw.disk_free_gb} GB` : "—", "modeller buraya iner"],
  ]
    .map(([k, v, s]) => `<div class="hw-cell"><div class="k">${k}</div><div class="v">${v}</div>${s ? `<div class="s">${s}</div>` : ""}</div>`)
    .join("");
}

function renderScan() {
  const out = $("#scan-out");
  if (!out || !scanData) return;
  const { hardware: hw, models, ollama_ok: ok } = scanData;
  out.innerHTML = `<div class="hw-grid">${hwCells(hw)}</div>`;
  if (!ok) out.appendChild(el("p", "status is-bad", "Ollama'ya ulaşılamadı — kurulu modeller işaretlenemedi, indirme çalışmaz."));

  const tabs = el("div", "cat-tabs");
  tabs.style.marginTop = "1rem";
  [["best", "★ ÖNERİLENLER"], ["all", "TÜMÜ"], ...Object.entries(CAT_NAMES).map(([k, v]) => [k, v.toUpperCase()])].forEach(([k, label]) => {
    const b = el("button", "", esc(label));
    b.type = "button";
    b.setAttribute("aria-pressed", String(scanFilter === k));
    b.addEventListener("click", () => { scanFilter = k; renderScan(); });
    tabs.appendChild(b);
  });
  out.appendChild(tabs);

  const shown = models
    .filter((m) => scanFilter === "all" || (scanFilter === "best" ? m.best_for.length : m.tags.includes(scanFilter)))
    .sort((a, b) => (scanFilter === "best" ? 0 : ["gpu", "partial", "cpu", "no"].indexOf(a.fit) - ["gpu", "partial", "cpu", "no"].indexOf(b.fit)) || a.size_gb - b.size_gb);

  if (scanFilter === "best") {
    out.appendChild(el("p", "panel-desc",
      hw.vram_gb
        ? `Her iş için bu bilgisayarda rahat çalışabilecek en güçlü model seçildi (${hw.vram_gb} GB VRAM, ${hw.ram_gb} GB RAM).`
        : `Uygun ekran kartı bulunamadı; öneriler ${hw.ram_gb} GB RAM ile CPU'da çalışacak modeller. Küçük modeller daha akıcı olur.`));
  }

  const list = el("div", "model-list");
  shown.forEach((m) => {
    const installed = isInstalled(m.name);
    const current = installed && (S.settings.model === m.name || S.settings.model === `${m.name}:latest`);
    const r = el("div", `model-row fit-${m.fit}`);
    const best = m.best_for.map((c) => `<span class="badge red">★ ${esc(CAT_NAMES[c])}</span>`).join(" ");
    r.innerHTML =
      `<div><div class="name">${esc(m.name)} <span class="fit">${esc(m.fit_label)}</span> ${best}` +
      `${installed ? ' <span class="badge">KURULU</span>' : ""}</div>` +
      `<div class="info">${esc(m.desc)}</div>` +
      `<div class="specs"><span>${m.size_gb} GB indirme</span><span>${esc(m.params)}</span>` +
      `<span>${esc(m.tags.map((t) => CAT_NAMES[t]).join(" · "))}</span></div></div>` +
      `<div class="acts"></div><div class="progress" data-pull="${esc(m.name)}" hidden></div>`;
    const acts = $(".acts", r);
    if (current) acts.appendChild(el("span", "badge red", "AKTİF"));
    else if (installed) acts.appendChild(button("KULLAN", "btn", () => setModel(m.name)));
    else {
      const b = button("İNDİR", m.fit === "no" ? "btn-ghost" : "btn", () => pull(m.name));
      b.dataset.pullBtn = m.name;
      if (m.fit === "no") b.title = "Bu bilgisayarda bellek yetmeyebilir";
      acts.appendChild(b);
    }
    list.appendChild(r);
  });
  if (!shown.length) list.appendChild(el("p", "pop-empty", "Bu kategoride model yok."));
  out.appendChild(list);
  pulls.forEach((_, name) => paintPull(name));
}

function paintPull(name, final) {
  const st = pulls.get(name) || final;
  $$(".progress", body).filter((n) => n.dataset.pull === name).forEach((n) => {
    if (!st) { n.hidden = true; return; }
    n.hidden = false;
    if (st.error) { n.innerHTML = `<span class="bar">✖</span> ${esc(st.error)}`; return; }
    const ratio = st.total ? Math.min(1, st.completed / st.total) : 0;
    const cells = 24;
    const fill = Math.round(ratio * cells);
    const bar = "█".repeat(fill) + "░".repeat(cells - fill);
    const size = st.total ? ` ${fmtBytes(st.completed)} / ${fmtBytes(st.total)}` : "";
    n.innerHTML = `<span class="bar">${bar}</span> ${String(Math.round(ratio * 100)).padStart(3)}%${size} · ${esc(st.status || "")}`;
  });
  $$("[data-pull-btn]", body).filter((b) => b.dataset.pullBtn === name).forEach((b) => {
    b.textContent = pulls.has(name) ? "İPTAL" : "İNDİR";
  });
}

async function pull(name) {
  if (pulls.has(name)) {
    pulls.get(name).controller.abort();
    return;
  }
  const st = { controller: new AbortController(), status: "başlıyor", completed: 0, total: 0, error: "", done: false };
  pulls.set(name, st);
  paintPull(name);
  try {
    await stream("/api/models/pull", { name }, (ev) => {
      if (ev.type === "progress") {
        st.status = ev.status;
        if (ev.total) { st.total = ev.total; st.completed = ev.completed || 0; }
      } else if (ev.type === "error") {
        st.error = ev.message;
      } else if (ev.type === "done") {
        st.done = true;
      }
      paintPull(name);
    }, st.controller.signal);
  } catch (err) {
    st.error = err.name === "AbortError" ? "iptal edildi" : err.message;
  }
  pulls.delete(name);
  if (st.done && !st.error) {
    toast(`${name} indirildi ✓`);
    await refreshModels();
    paintPull(name);
  } else {
    if (st.error !== "iptal edildi") toast(`${name}: ${st.error || "indirilemedi"}`, true);
    paintPull(name, st);
  }
}

// --- KİŞİLİKLER -------------------------------------------------------------------------

const saveTimers = new Map();

function renderPersonas() {
  const behaviour = panel("KİŞİLİK DAVRANIŞI", null);
  behaviour.append(
    row("Her mesajda hatırlat",
      "Küçük modeller birkaç mesaj sonra kişiliği unutabiliyor. Açıkken her mesajın önüne modele kişiliğini ve önceki konuşmayı hatırlatan gizli bir not eklenir.",
      switchCtl(S.settings.persona_reminder, (v) => saveSettings({ persona_reminder: v }))),
  );

  const add = button("+", "btn-plus", addPersona);
  add.title = "Yeni kişilik ekle";
  const p = panel("MASTER PROMPT — KİŞİLİKLER",
    "Seçili kişiliğin promptu her konuşmanın başında modele sistem mesajı olarak verilir. <b>+</b> ile istediğin kadar kişilik ekle; sohbette kutunun sol üstündeki düğmeden aralarında geçiş yap. Değişiklikler otomatik kaydedilir.",
    add);
  p.appendChild(el("div", "persona-list")).id = "persona-list";
  renderPersonaCards();
}

function renderPersonaCards() {
  const listNode = $("#persona-list");
  if (!listNode) return;
  listNode.innerHTML = "";
  if (!S.personas.length) {
    listNode.appendChild(el("p", "muted", "Hiç kişilik yok. <b>+</b> ile ekle — kişiliksiz sohbette sistem promptu gönderilmez."));
  }
  S.personas.forEach((p, idx) => listNode.appendChild(personaCard(p, idx)));
  markActiveCards();
}

function markActiveCards() {
  $$(".persona-card", body).forEach((card) => {
    const on_ = card.dataset.id === S.settings.active_persona_id;
    card.classList.toggle("is-active", on_);
    $(".badge", card).hidden = !on_;
    $(".use-btn", card).hidden = on_;
  });
}

function setStatus(node, text, kind) {
  node.textContent = text;
  node.classList.toggle("is-ok", kind === "ok");
  node.classList.toggle("is-bad", kind === "bad");
}

function personaCard(p, idx) {
  const card = el("div", "persona-card");
  card.dataset.id = p.id;
  card.innerHTML = `
    <div class="persona-card-head">
      <span class="idx">#${String(idx + 1).padStart(2, "0")}</span>
      <input class="name" maxlength="60" spellcheck="false" aria-label="Kişilik adı" />
      <span class="badge red">AKTİF</span>
    </div>
    <textarea class="field prompt" maxlength="20000" aria-label="Master prompt"
      placeholder="Örn: Sen alaycı ama yardımsever bir hackersın. Kısa cevaplar ver, Matrix göndermeleri yap…"></textarea>
    <div class="persona-card-foot">
      <span class="status"></span>
      <button class="btn btn-ghost use-btn" type="button">BUNU KULLAN</button>
      <button class="btn btn-danger del-btn" type="button">SİL</button>
    </div>`;
  const nameIn = $(".name", card);
  const promptIn = $(".prompt", card);
  const status = $(".status", card);
  nameIn.value = p.name;
  promptIn.value = p.prompt;

  const save = async () => {
    saveTimers.delete(p.id);
    const name = nameIn.value.trim();
    if (!name) return setStatus(status, "ad boş olamaz", "bad");
    try {
      await api(`/api/personas/${p.id}`, { method: "PUT", body: { name, prompt: promptIn.value } });
      setStatus(status, "✓ kaydedildi", "ok");
    } catch (err) {
      setStatus(status, "kaydedilemedi: " + err.message, "bad");
    }
  };
  const onEdit = () => {
    // Yerel durumu hemen güncelle: seçici ve yeniden çizim en son metni görsün.
    p.name = nameIn.value.trim() || p.name;
    p.prompt = promptIn.value;
    emit("personas");
    setStatus(status, "… kaydediliyor", null);
    clearTimeout(saveTimers.get(p.id));
    saveTimers.set(p.id, setTimeout(save, 600));
  };
  nameIn.addEventListener("input", onEdit);
  promptIn.addEventListener("input", onEdit);

  $(".use-btn", card).addEventListener("click", async () => {
    try {
      S.settings = await api("/api/active", { method: "PUT", body: { persona_id: p.id } });
      emit("settings");
    } catch (err) {
      toast("Seçilemedi: " + err.message, true);
    }
  });

  const del = $(".del-btn", card);
  let armTimer = 0;
  del.addEventListener("click", async () => {
    if (!del.classList.contains("is-armed")) {
      del.classList.add("is-armed");
      del.textContent = "EMİN MİSİN?";
      armTimer = setTimeout(() => { del.classList.remove("is-armed"); del.textContent = "SİL"; }, 3000);
      return;
    }
    clearTimeout(armTimer);
    clearTimeout(saveTimers.get(p.id));
    saveTimers.delete(p.id);
    try {
      S.settings = await api(`/api/personas/${p.id}`, { method: "DELETE" });
      S.personas = S.personas.filter((x) => x.id !== p.id);
      emit("personas");
      emit("settings");
      renderPersonaCards();
    } catch (err) {
      setStatus(status, "silinemedi: " + err.message, "bad");
    }
  });
  return card;
}

async function addPersona() {
  try {
    const p = await api("/api/personas", { method: "POST", body: { name: `Kişilik ${S.personas.length + 1}`, prompt: "" } });
    S.personas.push(p);
    if (!S.settings.active_persona_id) S.settings = await api("/api/active", { method: "PUT", body: { persona_id: p.id } });
    emit("personas");
    emit("settings");
    renderPersonaCards();
    const card = body.querySelector(`.persona-card[data-id="${p.id}"]`);
    card.scrollIntoView({ behavior: "smooth", block: "center" });
    $(".name", card).select();
  } catch (err) {
    toast("Kişilik eklenemedi: " + err.message, true);
  }
}

// --- durum değişiklikleri ----------------------------------------------------------------

on("models", () => {
  if (page !== "models") return;
  renderInstalled();
  renderScan();
});
on("settings", () => {
  if (page === "models") { renderInstalled(); renderScan(); }
  if (page === "personas") markActiveCards();
});
