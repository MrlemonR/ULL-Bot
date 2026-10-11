/* İnce ayar (fine-tune) sayfası: ortam, veri seti, model ve eğitim. */
import { api } from "./api.js";
import { refreshModels } from "./models.js";
import { button, rangeCtl, row, segCtl } from "./settings.js";
import { S, emit } from "./store.js";
import { toast } from "./ui.js";
import { $, el, esc, fmtBytes } from "./util.js";

const page = $("#train-page");
let active = false;
let pollTimer = 0;

let envData = null;         // {env, bases, busy}
let datasets = { dir: "", items: [] };
let selected = null;        // seçili veri seti dosyası
let preview = null;         // seçili veri setinin özeti
let job = null;             // son iş (kurulum ya da eğitim)
let history = [];
const form = { base: "llama3.1:8b", name: "", nameTouched: false, system: "", epochs: 3, lr: 2e-4, rank: 16, max_len: 1024 };

const SAMPLE = `[
  {"soru": "Adın ne?", "cevap": "Ben ULL-Bot, senin bilgisayarında çalışan asistanım."},
  {"soru": "Seni kim yaptı?", "cevap": "Beni Mrlemon geliştirdi."}
]`;

// --- küçük yardımcılar ---------------------------------------------------------------

function panel(id, title, desc) {
  const p = el("section", "panel");
  p.innerHTML = `<header class="panel-head"><h2>${esc(title)}</h2><span class="right"></span></header>` +
    (desc ? `<p class="panel-desc">${desc}</p>` : "") + `<div id="${id}"></div>`;
  page.appendChild(p);
  return p;
}

function bar(ratio, cells = 28) {
  const fill = Math.max(0, Math.min(cells, Math.round((ratio || 0) * cells)));
  return "█".repeat(fill) + "░".repeat(cells - fill);
}

function dur(sec) {
  if (sec == null) return "—";
  sec = Math.max(0, Math.round(sec));
  const m = Math.floor(sec / 60), s = sec % 60;
  return m ? `${m} dk ${s} sn` : `${s} sn`;
}

const baseStem = (b) => b.replace(":", "-");
const fileStem = (f) => (f || "").replace(/\.(jsonl?|JSONL?)$/, "").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");

function autoName() {
  if (!form.nameTouched) form.name = [baseStem(form.base), fileStem(selected)].filter(Boolean).join("-");
}

// --- sayfa ------------------------------------------------------------------------------

export async function showTrain() {
  active = true;
  page.innerHTML = "";
  panel("tr-env", "EĞİTİM ORTAMI",
    "Eğitim ayrı bir Python ortamında (<code>.train-venv</code>) yapılır: PyTorch + Unsloth/PEFT. " +
    "İlk kurulum birkaç GB indirir. <b>NVIDIA ekran kartı şiddetle önerilir</b>; olmadan yalnızca 1B gibi küçük modeller, çok yavaş eğitilebilir.");
  panel("tr-data", "VERİ SETİ",
    "Soru-cevap çiftleri içeren bir <b>JSON</b> (ya da JSONL) dosyası. Dosyaları <code>datasets</code> klasörüne at ya da buradan yükle. " +
    "Alan adları esnek: <code>soru/cevap</code>, <code>question/answer</code>, <code>instruction/output</code>, <code>prompt/completion</code> ya da <code>messages</code>.");
  panel("tr-model", "MODEL VE AYARLAR",
    "Seçtiğin modelin orijinal ağırlıkları Hugging Face'ten indirilir, üstüne bir LoRA adaptörü eğitilir ve sonuç Ollama'ya " +
    "<b>yeni bir model</b> olarak eklenir (ör. <code>llama3.1-8b-sorularim</code>). Ollama adaptörleri Llama ve Mistral için destekliyor.");
  panel("tr-run", "EĞİTİM", null);
  panel("tr-hist", "GEÇMİŞ", null);
  renderEnv();
  renderData();
  renderModel();
  renderRun();
  renderHistory();
  await Promise.all([loadEnv(false), loadDatasets(), poll()]);
}

export function leaveTrain() {
  active = false;
  clearTimeout(pollTimer);
}

async function loadEnv(refresh) {
  if (refresh) { envData = null; renderEnv(); }
  try {
    envData = await api(`/api/train/env${refresh ? "?refresh=1" : ""}`);
  } catch (err) {
    envData = { error: err.message };
  }
  if (!envData.error && !envData.bases.some((b) => b.name === form.base)) form.base = envData.bases[0]?.name;
  if (active) { renderEnv(); renderModel(); renderRun(); }
}

async function loadDatasets(select) {
  try {
    datasets = await api("/api/train/datasets");
  } catch (err) {
    datasets = { dir: "", items: [], error: err.message };
  }
  if (select) selected = select;
  if (!selected || !datasets.items.some((d) => d.file === selected)) selected = datasets.items.find((d) => d.valid)?.file || null;
  await loadPreview();
}

async function loadPreview() {
  preview = null;
  if (selected) {
    try { preview = await api(`/api/train/datasets/${encodeURIComponent(selected)}`); } catch { preview = null; }
  }
  autoName();
  if (active) { renderData(); renderModel(); renderRun(); }
}

async function poll() {
  clearTimeout(pollTimer);
  let data;
  try { data = await api("/api/train/status"); } catch { data = null; }
  if (data) {
    const prev = job;
    job = data.job;
    history = data.history;
    // Bitiş anı: model listesini / ayarları tazele.
    if (prev && prev.state === "running" && job && job.state !== "running") {
      if (job.kind === "train" && job.state === "done") {
        S.settings = await api("/api/settings");
        emit("settings");
        await refreshModels();
        toast(`✓ ${job.result.model} hazır ve seçildi`);
      }
      if (job.kind === "install") await loadEnv(false);
    }
    if (active) { renderEnv(); renderRun(); renderHistory(); }
  }
  if (active) pollTimer = setTimeout(poll, job && job.state === "running" ? 1000 : 5000);
}

// --- ortam --------------------------------------------------------------------------------

function renderEnv() {
  const box = $("#tr-env");
  if (!box) return;
  box.innerHTML = "";
  const right = box.parentElement.querySelector(".right");
  right.innerHTML = "";

  if (!envData) {
    box.appendChild(el("p", "status", "… kontrol ediliyor (ilk seferde biraz sürebilir)"));
    return;
  }
  if (envData.error) {
    box.appendChild(el("p", "status is-bad", "Kontrol edilemedi: " + esc(envData.error)));
    return;
  }
  const e = envData.env;
  const yes = (v) => (v ? `<span class="ok">✓</span> ${esc(v)}` : '<span class="no">✗</span> yok');
  const gpu = e.cuda
    ? `${esc(e.gpu)}<div class="s">${e.vram_gb} GB VRAM</div>`
    : e.nvidia ? `NVIDIA bulundu<div class="s">sürücü CUDA ${esc(e.nvidia.cuda_driver || "?")} — PyTorch kurulunca kullanılacak</div>`
      : `<span class="no">bulunamadı</span><div class="s">eğitim CPU'da çok yavaş olur</div>`;
  box.innerHTML =
    `<div class="hw-grid">` +
    `<div class="hw-cell"><div class="k">Ekran kartı</div><div class="v">${gpu}</div></div>` +
    `<div class="hw-cell"><div class="k">PyTorch</div><div class="v">${yes(e.torch)}</div>${e.torch ? `<div class="s">CUDA: ${e.cuda ? "✓ kullanılıyor" : "✗ yok"}</div>` : ""}</div>` +
    `<div class="hw-cell"><div class="k">Hızlandırma</div><div class="v">${e.unsloth ? '<span class="ok">✓</span> Unsloth' : e.peft ? "PEFT" : "—"}</div>` +
    `<div class="s">${e.unsloth ? "2× hızlı, yarı bellek" : e.peft ? "standart LoRA" : ""}</div></div>` +
    `<div class="hw-cell"><div class="k">Durum</div><div class="v">${e.ready ? '<span class="ok">✓ hazır</span>' : '<span class="no">kurulu değil</span>'}</div>` +
    `<div class="s">${esc(e.python ? "Python " + e.python : e.exists ? "" : ".train-venv yok")}</div></div>` +
    `</div>`;

  const installing = job && job.kind === "install" && job.state === "running";
  right.appendChild(button("↻ KONTROL ET", "btn-ghost", () => loadEnv(true)));
  if (!installing) {
    right.appendChild(button(e.ready ? "YENİDEN KUR" : "ORTAMI KUR", e.ready ? "btn-ghost" : "btn-solid", startInstall));
  }
  if (!e.nvidia && !e.cuda) {
    box.appendChild(el("p", "status is-bad", "NVIDIA ekran kartı görünmüyor: 8B bir model CPU'da günlerce sürebilir. Denemek için Llama 3.2 1B seç."));
  }
  if (job && job.kind === "install") box.appendChild(jobView(job));
}

async function startInstall() {
  try {
    job = await api("/api/train/env/install", { method: "POST" });
    renderEnv();
    renderRun();
    poll();
  } catch (err) {
    toast(err.message, true);
  }
}

// --- veri seti ----------------------------------------------------------------------------

function renderData() {
  const box = $("#tr-data");
  if (!box) return;
  box.innerHTML = "";
  const right = box.parentElement.querySelector(".right");
  right.innerHTML = "";

  const fileIn = el("input");
  fileIn.type = "file";
  fileIn.accept = ".json,.jsonl,application/json";
  fileIn.hidden = true;
  fileIn.addEventListener("change", async () => {
    const f = fileIn.files[0];
    if (!f) return;
    try {
      const content = await f.text();
      const r = await api("/api/train/datasets", { method: "POST", body: { name: f.name, content } });
      toast(`Yüklendi → datasets/${r.file}`);
      await loadDatasets(r.file);
    } catch (err) {
      toast("Yüklenemedi: " + err.message, true);
    }
    fileIn.value = "";
  });
  right.append(
    fileIn,
    button("DOSYA YÜKLE", "btn", () => fileIn.click()),
    button("KLASÖRÜ AÇ", "btn-ghost", () => api("/api/train/datasets/open-folder", { method: "POST" }).catch((e) => toast(e.message, true))),
    button("↻", "btn-ghost", () => loadDatasets()),
  );

  if (!datasets.items.length) {
    box.appendChild(el("p", "muted", `Klasörde veri seti yok: <code>${esc(datasets.dir || "datasets")}</code>. DOSYA YÜKLE ile ekle.`));
  } else {
    const list = el("div", "prefab-list");
    datasets.items.forEach((d) => {
      const item = el("div", "prefab-item" + (d.valid ? "" : " is-bad") + (d.file === selected ? " is-sel" : ""));
      item.tabIndex = d.valid ? 0 : -1;
      item.innerHTML = `<span class="n">${esc(d.file)}</span><span class="m">${d.valid ? `${d.count} örnek · ${fmtBytes(d.size)}` : esc(d.error)}</span>`;
      if (d.valid) {
        const pick = () => { selected = d.file; loadPreview(); };
        item.addEventListener("click", pick);
        item.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick(); } });
      }
      list.appendChild(item);
    });
    box.appendChild(list);
  }

  if (preview) {
    const pv = el("div", "ds-preview");
    pv.innerHTML = `<div class="status is-ok">✓ ${preview.count} örnek okundu (${esc(preview.format.toUpperCase())})</div>` +
      preview.warnings.map((w) => `<div class="status is-warn">! ${esc(w)}</div>`).join("") +
      (preview.errors.length ? `<details class="ds-errors"><summary>${preview.error_count} hatalı örnek</summary><pre>${esc(preview.errors.join("\n"))}</pre></details>` : "") +
      preview.preview.map((x) => `<div class="ex-card"><div><b>S:</b> ${esc(x.soru)}</div><div><b>C:</b> ${esc(x.cevap)}</div></div>`).join("");
    box.appendChild(pv);
  }

  const help = el("details", "ds-help");
  help.innerHTML = `<summary>Dosya biçimi örneği</summary><pre>${esc(SAMPLE)}</pre>` +
    `<p class="muted">Örnek dosya: <code>datasets/ornek-ullbot.json</code>. Sistem mesajı için örneğe <code>"system"</code> alanı da ekleyebilirsin.</p>`;
  box.appendChild(help);
}

// --- model ve ayarlar -----------------------------------------------------------------------

function renderModel() {
  const box = $("#tr-model");
  if (!box) return;
  box.innerHTML = "";
  const vram = envData?.env?.vram_gb || 0;

  const list = el("div", "model-list");
  (envData?.bases || []).forEach((b) => {
    const r = el("div", "model-row base-row" + (b.name === form.base ? " is-sel" : ""));
    r.tabIndex = 0;
    const fits = !vram ? "" : vram >= b.vram_full ? "tam hassasiyet sığar" : vram >= b.vram_4bit ? "4-bit QLoRA ile sığar" : "VRAM yetmeyebilir";
    r.innerHTML =
      `<div><div class="name">${b.name === form.base ? "● " : "○ "}${esc(b.name)} <span class="muted">${esc(b.label)}</span>` +
      `${b.installed ? ' <span class="badge">OLLAMA\'DA KURULU</span>' : ' <span class="badge">sonunda indirilecek</span>'}</div>` +
      `<div class="specs"><span>~${b.vram_4bit} GB VRAM (4-bit)</span><span>~${b.vram_full} GB (tam)</span>` +
      `${fits ? `<span class="${fits.includes("yetmeyebilir") ? "no" : "ok"}">${esc(fits)}</span>` : ""}</div></div>`;
    const pick = () => { form.base = b.name; autoName(); renderModel(); renderRun(); };
    r.addEventListener("click", pick);
    r.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick(); } });
    list.appendChild(r);
  });
  if (!envData) list.appendChild(el("p", "pop-empty", "yükleniyor…"));
  box.appendChild(list);

  const nameIn = el("input");
  nameIn.value = form.name;
  nameIn.spellcheck = false;
  nameIn.placeholder = "yeni-model-adi";
  nameIn.style.width = "100%";
  nameIn.addEventListener("input", () => { form.name = nameIn.value; form.nameTouched = true; renderRunButton(); });

  const sys = el("textarea", "field");
  sys.rows = 3;
  sys.placeholder = "İsteğe bağlı: eğitim örneklerine ve yeni modele eklenecek sistem mesajı (kişilik).";
  sys.value = form.system;
  sys.addEventListener("input", () => { form.system = sys.value; });
  const fromPersona = el("select");
  fromPersona.innerHTML = `<option value="">kişilikten doldur…</option>` + S.personas.map((p) => `<option value="${esc(p.id)}">${esc(p.name)}</option>`).join("");
  fromPersona.addEventListener("change", () => {
    const p = S.personas.find((x) => x.id === fromPersona.value);
    if (p) { form.system = p.prompt; sys.value = p.prompt; }
    fromPersona.value = "";
  });

  const steps = () => (preview ? Math.max(1, Math.ceil(preview.count / 8)) * form.epochs : null);
  const stepInfo = el("span", "muted");
  const paintSteps = () => { stepInfo.textContent = steps() ? `≈ ${steps()} eğitim adımı` : ""; };
  paintSteps();

  const settings = el("div");
  settings.style.marginTop = "1rem";
  const sysWrap = el("div");
  sysWrap.style.width = "100%";
  sysWrap.append(sys, fromPersona);
  fromPersona.style.marginTop = "0.4rem";
  settings.append(
    row("Yeni model adı", "Ollama'da bu adla görünür; sağ üstteki model listesinden seçilir.", nameIn),
    row("Sistem mesajı", "Boş bırakabilirsin. Doldurursan model bu kişilikle eğitilir ve varsayılan sistem mesajı olur.", sysWrap),
    row("Tur sayısı (epoch)", "Veri seti kaç kez baştan sona okunur. Az veri: 3-5; çok veri: 1-2. Fazlası ezberletir.",
      rangeCtl(form.epochs, { min: 1, max: 10, step: 1, fmt: (v) => `${v}` }, (v) => { form.epochs = v; paintSteps(); })),
    row("Öğrenme hızı", "Yüksek: hızlı öğrenir ama bozabilir. Emin değilsen NORMAL.",
      segCtl(form.lr, [[1e-4, "DÜŞÜK"], [2e-4, "NORMAL"], [5e-4, "YÜKSEK"]], (v) => { form.lr = v; })),
    row("LoRA rank", "Adaptörün kapasitesi. 16 çoğu iş için yeterli; çok bilgi öğretilecekse 32.",
      segCtl(form.rank, [[8, "8"], [16, "16"], [32, "32"]], (v) => { form.rank = v; })),
    row("En fazla uzunluk", "Bir örneğin token sınırı. Uzun cevaplar varsa büyüt (daha çok VRAM).",
      segCtl(form.max_len, [[512, "512"], [1024, "1024"], [2048, "2048"]], (v) => { form.max_len = v; })),
  );
  box.append(settings, stepInfo);
}

// --- eğitim -----------------------------------------------------------------------------------

function blockReason() {
  if (job && job.state === "running") return job.kind === "install" ? "ortam kuruluyor…" : "eğitim sürüyor";
  if (!envData || envData.error) return "ortam kontrol ediliyor";
  if (!envData.env.ready) return "önce eğitim ortamını kur";
  if (!selected || !preview || !preview.count) return "bir veri seti seç";
  if (!form.name.trim()) return "yeni model adı yaz";
  return "";
}

function renderRunButton() {
  const btn = $("#tr-start");
  if (!btn) return;
  const reason = blockReason();
  btn.disabled = Boolean(reason);
  $("#tr-reason").textContent = reason;
}

function renderRun() {
  const box = $("#tr-run");
  if (!box) return;
  box.innerHTML = "";
  const r = el("div", "row");
  const start = button("▶ EĞİTİMİ BAŞLAT", "btn-solid", startTraining);
  start.id = "tr-start";
  const reason = el("span", "muted");
  reason.id = "tr-reason";
  r.append(start, reason);
  box.appendChild(r);
  renderRunButton();
  if (job && job.kind === "train") box.appendChild(jobView(job));
}

async function startTraining() {
  try {
    job = await api("/api/train/start", {
      method: "POST",
      body: {
        base: form.base, dataset: selected, name: form.name.trim(), system: form.system,
        epochs: form.epochs, lr: form.lr, rank: form.rank, max_len: form.max_len,
      },
    });
    renderRun();
    renderEnv();
    poll();
  } catch (err) {
    toast("Başlatılamadı: " + err.message, true);
  }
}

function jobView(j) {
  const v = el("div", "job");
  const running = j.state === "running";
  const ratio = j.progress ?? (j.total ? j.step / j.total : null);
  const head = { running: "▶ ÇALIŞIYOR", done: "✓ BİTTİ", error: "✖ HATA", cancelled: "■ İPTAL EDİLDİ" }[j.state];
  const stats = [
    j.total ? `adım ${j.step}/${j.total}` : "",
    j.loss != null ? `loss ${j.loss}` : "",
    running && j.eta != null ? `kalan ~${dur(j.eta)}` : "",
    `geçen ${dur(j.elapsed)}`,
  ].filter(Boolean).join(" · ");
  v.innerHTML =
    `<div class="job-head"><span class="job-state s-${j.state}">${head}</span> <span>${esc(j.title)}</span></div>` +
    `<div class="job-stage">${esc(j.stage)}</div>` +
    (ratio != null ? `<div class="progress"><span class="bar">${bar(ratio)}</span> ${String(Math.round(ratio * 100)).padStart(3)}%</div>` : "") +
    `<div class="job-stats">${esc(stats)}</div>` +
    (j.message && !running ? `<div class="status ${j.state === "done" ? "is-ok" : "is-bad"}">${esc(j.message)}</div>` : "") +
    `<details class="job-log"${j.state === "error" ? " open" : ""}><summary>günlük</summary><pre>${esc(j.log.join("\n"))}</pre></details>`;
  const acts = el("div", "row");
  if (running) acts.appendChild(button("■ DURDUR", "btn-danger", async () => { await api("/api/train/cancel", { method: "POST" }); poll(); }));
  if (j.kind === "train" && j.state === "done") {
    const go = el("a", "btn btn-solid", "SOHBETTE DENE →");
    go.href = "#/";
    acts.appendChild(go);
  }
  if (acts.children.length) v.appendChild(acts);
  const pre = v.querySelector(".job-log pre");
  requestAnimationFrame(() => { pre.scrollTop = pre.scrollHeight; });
  return v;
}

function renderHistory() {
  const box = $("#tr-hist");
  if (!box) return;
  box.innerHTML = "";
  if (!history.length) {
    box.appendChild(el("p", "muted", "Henüz eğitim yapılmadı."));
    return;
  }
  const list = el("div", "chat-rows");
  history.forEach((h) => {
    const r = el("div", "chat-row");
    const mark = { done: "✓", error: "✖", cancelled: "■" }[h.state] || "…";
    const date = h.started ? new Date(h.started * 1000).toLocaleString("tr-TR") : "";
    r.innerHTML = `<span class="ic">${mark}</span><span class="t">${esc(h.title)} <span class="muted">· ${esc(h.meta?.dataset || "")} · ${h.meta?.examples ?? "?"} örnek</span></span>` +
      `<span class="d">${esc(date)} · ${dur(h.elapsed)}</span>`;
    r.title = h.message || "";
    list.appendChild(r);
  });
  box.appendChild(list);
}
