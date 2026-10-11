/* Ayarlar: Görünüm, Modeller, Kişilikler. */
import { api, stream } from "./api.js";
import { DEFAULT_APPEARANCE, applyAppearance } from "./appearance.js";
import { isInstalled, refreshModels, setModel } from "./models.js";
import { createSpinner } from "./spinner.js";
import { S, emit, on } from "./store.js";
import { confirmDialog, openModal, promptDialog, toast } from "./ui.js";
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

  const reset = button("VARSAYILANA DÖN", "btn-ghost", () => {
    Object.assign(A, DEFAULT_APPEARANCE);
    applyAppearance(A);
    save.flush();
    renderAppearanceAgain();
  });
  const saveFile = button("▣ KAYDET", "btn", async () => {
    save.cancel();
    await saveSettings({ appearance: A }); // bekleyen değişiklik dosyaya da girsin
    savePrefab();
  });
  const loadFile = button("▤ YÜKLE", "btn-solid", loadPrefab);
  const files = panel("SIFIRLA · KAYDET · YÜKLE",
    "<b>Kaydet</b>: görünüm, model ayarları ve tüm kişilikler uygulamanın klasöründeki <code>prefab</code> klasörüne " +
    "bir dosya olarak yazılır. <b>Yükle</b>: o klasördeki dosyalar listelenir, seçtiğinin ayarları uygulanır. " +
    "Başka bir yerden aldığın ayar dosyalarını da bu klasöre atabilirsin.");
  const r = el("div", "row");
  r.style.flexWrap = "wrap";
  r.append(reset, saveFile, loadFile);
  files.appendChild(r);
}

// --- ayar dosyaları (prefab) ------------------------------------------------------------

async function savePrefab() {
  const name = await promptDialog({
    title: "Ayarları dosyaya kaydet",
    label: "Dosya adı — prefab klasörüne .json olarak kaydedilir.",
    value: "ayarlarım",
    ok: "KAYDET",
  });
  if (!name) return;
  const post = (overwrite) => api("/api/prefabs", { method: "POST", body: { name, overwrite } });
  try {
    let res;
    try {
      res = await post(false);
    } catch (err) {
      if (!/zaten var/.test(err.message)) throw err;
      const ok = await confirmDialog({
        title: "Üzerine yazılsın mı?", text: `${err.message}. Üzerine yazılsın mı?`, ok: "ÜZERİNE YAZ", danger: false,
      });
      if (!ok) return;
      res = await post(true);
    }
    toast(`Kaydedildi → prefab/${res.file}`);
  } catch (err) {
    toast("Kaydedilemedi: " + err.message, true);
  }
}

async function loadPrefab() {
  let data;
  try {
    data = await api("/api/prefabs");
  } catch (err) {
    return toast("Liste alınamadı: " + err.message, true);
  }
  let selected = null;
  const wrap = el("div");
  wrap.style.display = "contents";
  wrap.appendChild(el("div", "prefab-dir", `Klasör: ${esc(data.dir)}`));
  const list = el("div", "prefab-list");
  wrap.appendChild(list);

  const render = () => {
    list.innerHTML = "";
    if (!data.items.length) {
      list.appendChild(el("p", "pop-empty", "Bu klasörde ayar dosyası yok. Önce KAYDET ile bir tane oluştur ya da .json dosyalarını bu klasöre at."));
      return;
    }
    data.items.forEach((it) => {
      const item = el("div", "prefab-item" + (it.valid ? "" : " is-bad") + (selected === it.file ? " is-sel" : ""));
      item.tabIndex = it.valid ? 0 : -1;
      const meta = it.valid
        ? [it.saved_at, it.model && `model: ${it.model}`, it.personas.length ? `kişilikler: ${it.personas.join(", ")}` : "kişilik yok",
           it.active_persona && `aktif: ${it.active_persona}`].filter(Boolean).join(" · ")
        : `${it.saved_at} · ${it.error}`;
      item.innerHTML = `<span class="n">${esc(it.name)}</span><span class="m">${esc(it.file)} — ${esc(meta)}</span>`;
      const del = button("SİL", "btn-danger del", async (e) => {
        e.stopPropagation();
        if (!del.classList.contains("is-armed")) {
          del.classList.add("is-armed");
          del.textContent = "EMİN MİSİN?";
          setTimeout(() => { del.classList.remove("is-armed"); del.textContent = "SİL"; }, 3000);
          return;
        }
        try {
          await api(`/api/prefabs/${encodeURIComponent(it.file)}`, { method: "DELETE" });
          data.items = data.items.filter((x) => x.file !== it.file);
          if (selected === it.file) selected = null;
          render();
        } catch (err) {
          toast("Silinemedi: " + err.message, true);
        }
      });
      item.appendChild(del);
      if (it.valid) {
        const pick = () => { selected = it.file; render(); list.querySelector(".is-sel")?.focus(); };
        item.addEventListener("click", pick);
        item.addEventListener("dblclick", () => $("#modal-foot .btn-solid")?.click());
        item.addEventListener("keydown", (e) => { if (e.key === " ") { e.preventDefault(); pick(); } });
      }
      list.appendChild(item);
    });
  };
  render();

  const file = await openModal({
    title: "Ayar dosyası yükle",
    wide: true,
    body: wrap,
    buttons: [
      {
        label: "KLASÖRÜ AÇ", cls: "btn-ghost", left: true,
        action: () => api("/api/prefabs/open-folder", { method: "POST" }).catch((err) => toast(err.message, true)),
      },
      { label: "VAZGEÇ", cls: "btn-ghost", value: () => null },
      { label: "YÜKLE", cls: "btn-solid", value: () => selected },
    ],
  });
  if (!file) return;
  try {
    const res = await api("/api/prefabs/apply", { method: "POST", body: { file } });
    S.settings = res.settings;
    S.personas = res.personas;
    applyAppearance(S.settings.appearance);
    emit("settings");
    emit("personas");
    if (page) showSettings(page);
    const it = data.items.find((x) => x.file === file);
    toast(`“${it ? it.name : file}” yüklendi · ${res.loaded_personas} kişilik`);
  } catch (err) {
    toast("Yüklenemedi: " + err.message, true);
  }
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
  renderConnection();
  renderModelSearch();
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
}

// --- model ara & indir (yazarken öneri) -------------------------------------------------

let scanPromise = null;
function ensureScan() {
  // Öneriler uygunluk bilgisi için donanım taramasına ihtiyaç duyuyor; sessizce bir kez yap.
  if (scanData) return Promise.resolve(scanData);
  scanPromise ||= api("/api/models/recommend")
    .then((d) => { scanData = d; renderScan(); return d; })
    .catch(() => { scanPromise = null; return null; });
  return scanPromise;
}

function matchModels(q) {
  if (!scanData) return [];
  q = q.trim().toLowerCase();
  if (!q) {
    // Boşken: bu bilgisayar için önerilenler.
    return scanData.models.filter((m) => m.best_for.length).slice(0, 8);
  }
  const scored = [];
  for (const m of scanData.models) {
    const name = m.name.toLowerCase();
    const family = name.split(":")[0];
    const text = `${m.desc} ${m.tags.map((t) => CAT_NAMES[t]).join(" ")}`.toLowerCase();
    let score = -1;
    if (name.startsWith(q)) score = 0;
    else if (family.replace(/[^a-z0-9]/g, "").startsWith(q.replace(/[^a-z0-9]/g, "")) && q.replace(/[^a-z0-9]/g, "")) score = 1;
    else if (name.includes(q)) score = 2;
    else if (text.includes(q)) score = 3;
    if (score >= 0) scored.push([score, m]);
  }
  scored.sort((a, b) => a[0] - b[0] || FIT_ORDER.indexOf(a[1].fit) - FIT_ORDER.indexOf(b[1].fit) || a[1].size_gb - b[1].size_gb);
  return scored.slice(0, 8).map(([, m]) => m);
}

function renderModelSearch() {
  const nameIn = el("input");
  nameIn.placeholder = "model adı yaz — örn: llam, qwen, kod, türkçe…";
  nameIn.spellcheck = false;
  nameIn.autocomplete = "off";
  nameIn.setAttribute("role", "combobox");
  nameIn.setAttribute("aria-expanded", "false");
  const box = el("div", "pop suggest");
  box.hidden = true;
  box.setAttribute("role", "listbox");
  const wrapIn = el("div", "suggest-wrap");
  wrapIn.append(nameIn, box);

  const pullBtn = button("İNDİR", "btn", () => {
    const n = nameIn.value.trim();
    if (n) { close(); pull(n); }
  });
  const manual = panel("MODEL ARA & İNDİR",
    "Adını yazmaya başla: eşleşen modeller, bu bilgisayarda nasıl çalışacakları ve ne işe yaradıklarıyla listelenir. " +
    'Listede olmayan bir modeli de tam adını yazıp indirebilirsin (<a href="https://ollama.com/library" target="_blank" rel="noopener"><u>ollama.com/library</u></a>).');
  const r = el("div", "row");
  r.append(wrapIn, pullBtn);
  const picked = el("div", "picked");
  const prog = el("div", "progress");
  prog.hidden = true;
  manual.append(r, picked, prog);

  let items = [];
  let hl = -1;

  const close = () => {
    box.hidden = true;
    nameIn.setAttribute("aria-expanded", "false");
    hl = -1;
  };
  const showPicked = () => {
    const name = nameIn.value.trim();
    prog.dataset.pull = name;
    const m = scanData?.models.find((x) => x.name === name || `${x.name}:latest` === name);
    picked.innerHTML = "";
    if (m) {
      const list = el("div", "model-list");
      list.appendChild(modelRow(m));
      picked.appendChild(list);
      paintPull(m.name);
    } else if (name && scanData) {
      picked.appendChild(el("p", "status", "Katalogda yok — Ollama kütüphanesindeki adı doğruysa İNDİR ile indirilebilir."));
    }
  };
  const choose = (m) => {
    nameIn.value = m.name;
    close();
    showPicked();
  };
  const paintBox = () => {
    box.innerHTML = "";
    if (!scanData) {
      box.appendChild(el("div", "hint-row", "Donanım taranıyor…"));
    } else if (!items.length) {
      box.appendChild(el("div", "hint-row", "Katalogda eşleşen yok. Tam adı biliyorsan İNDİR'e bas."));
    } else {
      if (!nameIn.value.trim()) box.appendChild(el("div", "hint-row", "★ Bu bilgisayar için önerilenler"));
      items.forEach((m, i) => {
        const row = el("div", `model-row fit-${m.fit}${i === hl ? " is-hl" : ""}`);
        row.setAttribute("role", "option");
        row.innerHTML = modelInfoHtml(m);
        row.addEventListener("mousedown", (e) => { e.preventDefault(); choose(m); });
        box.appendChild(row);
      });
    }
    box.hidden = false;
    nameIn.setAttribute("aria-expanded", "true");
    // Altta yer yoksa listeyi yukarı aç.
    const rect = nameIn.getBoundingClientRect();
    const below = window.innerHeight - rect.bottom;
    box.classList.toggle("up", below < 320 && rect.top > below);
    box.querySelector(".is-hl")?.scrollIntoView({ block: "nearest" });
  };
  const update = async () => {
    items = matchModels(nameIn.value);
    hl = -1;
    paintBox();
    showPicked();
    if (!scanData) {
      await ensureScan();
      if (document.activeElement === nameIn) { items = matchModels(nameIn.value); paintBox(); showPicked(); }
    }
  };

  nameIn.addEventListener("focus", update);
  nameIn.addEventListener("input", update);
  nameIn.addEventListener("blur", () => setTimeout(close, 120));
  nameIn.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      if (box.hidden) return update();
      if (!items.length) return;
      hl = (hl + (e.key === "ArrowDown" ? 1 : -1) + items.length) % items.length;
      paintBox();
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (!box.hidden && hl >= 0 && items[hl]) choose(items[hl]);
      else pullBtn.click();
    } else if (e.key === "Escape") {
      close();
    }
  });
  modelSearchRefresh = showPicked;
}
let modelSearchRefresh = null;

function renderConnection() {
  const urlIn = el("input");
  urlIn.spellcheck = false;
  urlIn.placeholder = S.ollamaUrlDefault || "http://127.0.0.1:11434";
  urlIn.value = S.settings.ollama_url || "";
  const status = el("p", "status");
  status.id = "conn-status";
  status.style.marginTop = "0.7rem";

  const test = button("TEST ET", "btn-ghost", async () => {
    setStatus(status, "… deneniyor", null);
    try {
      const r = await api("/api/ollama/test", { method: "POST", body: { url: urlIn.value.trim() } });
      setStatus(status, r.ok ? `✓ ${r.url} — Ollama${r.version ? " " + r.version : ""} çalışıyor · ${r.models} model` : `✖ ${r.error}`, r.ok ? "ok" : "bad");
    } catch (err) {
      setStatus(status, "✖ " + err.message, "bad");
    }
  });
  const save = button("KAYDET", "btn", async () => {
    await saveSettings({ ollama_url: urlIn.value.trim() });
    await refreshModels();
    paintConnection();
  });
  urlIn.addEventListener("keydown", (e) => { if (e.key === "Enter") save.click(); });

  const p = panel("OLLAMA BAĞLANTISI",
    "ULL-Bot modellerle Ollama üzerinden konuşur. Ollama'nın açık olduğundan emin ol: Windows'ta Başlat menüsünden " +
    "<b>Ollama</b>'yı aç (sistem tepsisinde lama simgesi çıkar) ya da terminalde <code>ollama serve</code> çalıştır. " +
    "Başka bir adreste/portta çalışıyorsa buraya yaz; boş bırakırsan varsayılan adres kullanılır.");
  const r = el("div", "row");
  r.append(urlIn, test, save);
  p.append(r, status);
  paintConnection();
}

function paintConnection() {
  const status = $("#conn-status");
  if (!status) return;
  if (S.online) setStatus(status, `✓ bağlı — ${S.ollamaUrl} · ${S.installed.length} model kurulu`, "ok");
  else if (S.online === false) setStatus(status, `✖ ${S.ollamaError}`, "bad");
}

function renderInstalled() {
  const box = $("#installed-list");
  if (!box) return;
  box.innerHTML = "";
  if (!S.online) {
    box.appendChild(el("p", "status is-bad", "Ollama'ya bağlanılamadı — yukarıdaki bağlantı ayarına bak."));
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

const FIT_ORDER = ["gpu", "partial", "cpu", "no"];

/** Katalog modelinin bilgi bloğu: ad, uygunluk, ★ önerildiği işler, açıklama, boyut. */
function modelInfoHtml(m) {
  const installed = isInstalled(m.name);
  const best = m.best_for.map((c) => `<span class="badge red">★ ${esc(CAT_NAMES[c])}</span>`).join(" ");
  return (
    `<div><div class="name">${esc(m.name)} <span class="fit">${esc(m.fit_label)}</span> ${best}` +
    `${installed ? ' <span class="badge">KURULU</span>' : ""}</div>` +
    `<div class="info">${esc(m.desc)}</div>` +
    `<div class="specs"><span>${m.size_gb} GB indirme</span><span>${esc(m.params)}</span>` +
    `<span>${esc(m.tags.map((t) => CAT_NAMES[t]).join(" · "))}</span></div></div>`
  );
}

/** Kullan / indir düğmeli tam satır (öneri listesi ve seçilen model kartı). */
function modelRow(m) {
  const installed = isInstalled(m.name);
  const current = installed && (S.settings.model === m.name || S.settings.model === `${m.name}:latest`);
  const r = el("div", `model-row fit-${m.fit}`);
  r.innerHTML = modelInfoHtml(m) + `<div class="acts"></div><div class="progress" data-pull="${esc(m.name)}" hidden></div>`;
  const acts = $(".acts", r);
  if (current) acts.appendChild(el("span", "badge red", "AKTİF"));
  else if (installed) acts.appendChild(button("KULLAN", "btn", () => setModel(m.name)));
  else {
    const b = button(pulls.has(m.name) ? "İPTAL" : "İNDİR", m.fit === "no" ? "btn-ghost" : "btn", () => pull(m.name));
    b.dataset.pullBtn = m.name;
    if (m.fit === "no") b.title = "Bu bilgisayarda bellek yetmeyebilir";
    acts.appendChild(b);
  }
  return r;
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
    .sort((a, b) => (scanFilter === "best" ? 0 : FIT_ORDER.indexOf(a.fit) - FIT_ORDER.indexOf(b.fit)) || a.size_gb - b.size_gb);

  if (scanFilter === "best") {
    out.appendChild(el("p", "panel-desc",
      hw.vram_gb
        ? `Her iş için bu bilgisayarda rahat çalışabilecek en güçlü model seçildi (${hw.vram_gb} GB VRAM, ${hw.ram_gb} GB RAM).`
        : `Uygun ekran kartı bulunamadı; öneriler ${hw.ram_gb} GB RAM ile CPU'da çalışacak modeller. Küçük modeller daha akıcı olur.`));
  }

  const list = el("div", "model-list");
  shown.forEach((m) => list.appendChild(modelRow(m)));
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
  paintConnection();
  modelSearchRefresh?.();
  renderInstalled();
  renderScan();
});
on("settings", () => {
  if (page === "models") { renderInstalled(); renderScan(); modelSearchRefresh?.(); }
  if (page === "personas") markActiveCards();
});
