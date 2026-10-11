/* ULL-Bot arayüzü: sohbet, kişilik seçici, ayarlar. */
(() => {
  "use strict";

  const $ = (sel, root = document) => root.querySelector(sel);

  const esc = (s) =>
    String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

  const el = (tag, cls, html) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (html != null) n.innerHTML = html;
    return n;
  };

  async function api(path, opts = {}) {
    const res = await fetch(path, {
      headers: { "Content-Type": "application/json" },
      ...opts,
      body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
    });
    if (!res.ok) {
      let detail = `HTTP ${res.status}`;
      try {
        const j = await res.json();
        detail = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail);
      } catch { /* gövde JSON değil */ }
      throw new Error(detail);
    }
    return res.json();
  }

  // --- durum -----------------------------------------------------------------

  const state = {
    model: "",
    personas: [],
    activeId: null,
    installed: null,   // Ollama'daki modeller (null: bilinmiyor)
    online: null,
  };

  const activePersona = () => state.personas.find((p) => p.id === state.activeId) || null;

  function applyServerState(s) {
    state.model = s.model;
    state.personas = s.personas;
    state.activeId = s.active_persona_id;
  }

  // --- sekmeler --------------------------------------------------------------

  function showView(name) {
    document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("is-active", t.dataset.view === name));
    document.querySelectorAll(".view").forEach((v) => v.classList.toggle("is-active", v.dataset.view === name));
    if (name === "chat") $("#chat-input").focus();
    if (name === "settings") refreshModels();
  }
  document.querySelectorAll(".tab").forEach((t) => t.addEventListener("click", () => showView(t.dataset.view)));

  // --- açılış ekranı -----------------------------------------------------------

  (function drawBanner() {
    const F = {
      U: ["█  █", "█  █", "▀▀▀▀"],
      L: ["█   ", "█   ", "▀▀▀▀"],
      "-": ["    ", "▀▀▀ ", "    "],
      B: ["█▀▀▄", "█▀▀▄", "▀▀▀ "],
      O: ["█▀▀█", "█  █", "▀▀▀▀"],
      T: ["▀█▀", " █ ", " ▀ "],
    };
    const word = "ULL-BOT".split("");
    $("#banner").textContent = [0, 1, 2].map((r) => word.map((c) => F[c][r]).join(" ")).join("\n");
  })();

  // --- "düşünüyor" göstergesi ---------------------------------------------------
  //
  // Üç nokta yerine blok karakterlerle akan bir dalga ve Claude'daki gibi
  // değişen fiiller. Her saniye geçen süre yazılıyor.

  const VERBS = [
    "Cooking", "Tuning", "Brewing", "Pondering", "Simmering", "Forging",
    "Conjuring", "Percolating", "Marinating", "Noodling", "Crunching",
    "Wrangling", "Calibrating", "Distilling", "Decoding", "Synthesizing",
    "Transmuting", "Untangling", "Mulling", "Whirring", "Scheming",
    "Cogitating", "Spelunking", "Jacking in", "Bending the spoon",
    "Following the white rabbit", "Compiling thoughts", "Reticulating",
  ];
  const BOUNCE = ["▄", "■", "▀", "■"];
  const PULSE = "░▒▓█▓▒░░░░";   // sağa doğru kayan bir parıltı
  const BAR_LEN = 8;

  function createSpinner() {
    const node = el("div", "spinner");
    node.innerHTML = '<span class="glyph"></span><span class="verb"></span><span class="bar"></span><span class="secs"></span>';
    const [glyph, verb, bar, secs] = node.children;
    const started = performance.now();
    let frame = 0;
    let lastVerb = "";

    const nextVerb = () => {
      let v;
      do v = VERBS[(Math.random() * VERBS.length) | 0]; while (v === lastVerb);
      lastVerb = v;
      verb.textContent = v + "…";
    };

    const tick = () => {
      glyph.textContent = BOUNCE[frame % BOUNCE.length];
      let s = "";
      for (let j = 0; j < BAR_LEN; j++) s += PULSE[(((j - frame) % PULSE.length) + PULSE.length) % PULSE.length];
      bar.textContent = s;
      secs.textContent = `${Math.floor((performance.now() - started) / 1000)}s`;
      frame++;
    };

    nextVerb();
    tick();
    const t1 = setInterval(tick, 110);
    const t2 = setInterval(nextVerb, 3200);
    return {
      node,
      stop() {
        clearInterval(t1);
        clearInterval(t2);
        node.remove();
      },
    };
  }

  // --- basit markdown (yalnızca kod blokları, satır içi kod, kalın) -------------

  function inlineMd(escaped) {
    return escaped
      .replace(/`([^`\n]+)`/g, "<code>$1</code>")
      .replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>")
      .replace(/^#{1,6} (.+)$/gm, "<strong>$1</strong>");
  }

  function renderMd(src) {
    const parts = src.split("```");
    let html = "";
    parts.forEach((part, i) => {
      if (i % 2 === 1) {
        // Kod bloğu (akış sırasında henüz kapanmamış olabilir)
        let lang = "";
        let code = part;
        const nl = part.indexOf("\n");
        if (nl !== -1 && /^[\w+#.-]*$/.test(part.slice(0, nl).trim())) {
          lang = part.slice(0, nl).trim();
          code = part.slice(nl + 1);
        }
        html += `<pre class="code"><div class="code-lang">${esc(lang || "kod")}</div><code>${esc(code.replace(/\n$/, ""))}</code></pre>`;
      } else {
        const text = part.replace(/^\n+|\n+$/g, "");
        if (text) html += `<div class="md">${inlineMd(esc(text))}</div>`;
      }
    });
    return html;
  }

  // --- sohbet -------------------------------------------------------------------

  const log = $("#chat-log");
  const input = $("#chat-input");
  const sendBtn = $("#send-btn");
  const history = [];        // modele giden {role, content} listesi
  let current = null;        // yayındaki cevap: { controller, ... }
  let epoch = 0;             // "yeni sohbet" her bastığında artar

  const nearBottom = () => log.scrollHeight - log.scrollTop - log.clientHeight < 90;
  const toBottom = () => { log.scrollTop = log.scrollHeight; };

  function hideEmpty() { $("#chat-empty").hidden = true; }

  function addUserMsg(text) {
    hideEmpty();
    const m = el("article", "msg msg-user");
    m.innerHTML = `<header class="msg-head"><span class="who">&gt; SEN</span></header><div class="msg-body"><div class="md"></div></div>`;
    $(".md", m).textContent = text;
    log.appendChild(m);
    toBottom();
  }

  function addBotMsg(personaName) {
    const m = el("article", "msg msg-bot");
    m.innerHTML =
      `<header class="msg-head"><span class="who">◆ ${esc(personaName || "ULL-BOT")}</span><span class="model">${esc(state.model)}</span></header>` +
      `<div class="msg-body" hidden></div><footer class="msg-foot"></footer>`;
    log.appendChild(m);
    toBottom();
    return { root: m, body: $(".msg-body", m), foot: $(".msg-foot", m) };
  }

  function setBusy(busy) {
    sendBtn.classList.toggle("is-stop", busy);
    sendBtn.textContent = busy ? "■ DURDUR" : "GÖNDER ▶";
    sendBtn.type = busy ? "button" : "submit";
    $("#composer-hint").textContent = busy
      ? "ESC ▸ durdur"
      : "ENTER ▸ gönder · SHIFT+ENTER ▸ yeni satır";
    updateSendEnabled();
  }

  function updateSendEnabled() {
    sendBtn.disabled = !current && !input.value.trim();
  }

  function autosize() {
    input.style.height = "auto";
    input.style.height = Math.min(input.scrollHeight, 240) + "px";
  }

  async function send(text) {
    const myEpoch = epoch;
    const persona = activePersona();
    addUserMsg(text);
    history.push({ role: "user", content: text });

    const view = addBotMsg(persona && persona.name);
    const spinner = createSpinner();
    view.foot.appendChild(spinner.node);

    const controller = new AbortController();
    current = { controller };
    setBusy(true);

    let content = "";
    let renderQueued = false;
    let outcome = "error";
    let doneInfo = null;
    let errorText = "";

    const render = () => {
      renderQueued = false;
      const stick = nearBottom();
      view.body.hidden = !content;
      view.body.innerHTML = renderMd(content) + (current ? '<span class="cursor">█</span>' : "");
      if (stick) toBottom();
    };
    const queueRender = () => {
      if (!renderQueued) {
        renderQueued = true;
        requestAnimationFrame(render);
      }
    };

    try {
      const res = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ messages: history, persona_id: persona ? persona.id : null }),
        signal: controller.signal,
      });
      if (!res.ok) {
        let detail = `HTTP ${res.status}`;
        try { detail = (await res.json()).detail || detail; } catch { /* yok say */ }
        throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        let nl;
        while ((nl = buf.indexOf("\n")) !== -1) {
          const line = buf.slice(0, nl).trim();
          buf = buf.slice(nl + 1);
          if (!line) continue;
          const ev = JSON.parse(line);
          if (ev.type === "token") {
            content += ev.content;
            queueRender();
          } else if (ev.type === "done") {
            outcome = "done";
            doneInfo = ev;
          } else if (ev.type === "error") {
            outcome = "error";
            errorText = ev.message;
          }
        }
      }
      if (outcome !== "done" && !errorText) errorText = "Bağlantı beklenmedik şekilde kapandı.";
    } catch (err) {
      if (err.name === "AbortError") {
        outcome = "stopped";
      } else {
        outcome = "error";
        errorText = err.message || String(err);
      }
    }

    spinner.stop();
    current = null;
    render();
    setBusy(false);
    // Bu arada "yeni sohbet"e basıldıysa eski cevap yeni geçmişe karışmasın.
    if (myEpoch !== epoch) return;

    if (outcome === "error") {
      view.root.classList.add("is-error");
      view.body.hidden = false;
      const box = el("div", "err");
      box.textContent = "✖ " + errorText;
      view.body.appendChild(box);
      view.foot.remove();
      // Başarısız turu bağlamdan çıkar; kullanıcı aynı şeyi tekrar gönderebilir.
      history.pop();
      if (content) history.push({ role: "user", content: text }, { role: "assistant", content });
    } else {
      if (content) history.push({ role: "assistant", content });
      else history.pop();
      view.foot.textContent = outcome === "stopped" ? "■ durduruldu" : formatStats(doneInfo);
      if (outcome === "stopped" && !content) view.body.hidden = true;
    }

    if (nearBottom()) toBottom();
    input.focus();
  }

  function formatStats(d) {
    if (!d) return "✓";
    const parts = ["✓ " + (d.model || state.model)];
    if (d.eval_count) parts.push(`${d.eval_count} token`);
    if (d.eval_count && d.eval_duration) {
      parts.push(`${(d.eval_count / (d.eval_duration / 1e9)).toFixed(1)} token/sn`);
    }
    return parts.join(" · ");
  }

  function stop() {
    if (current) current.controller.abort();
  }

  $("#chat-form").addEventListener("submit", (e) => {
    e.preventDefault();
    if (current) return;
    const text = input.value.trim();
    if (!text) return;
    input.value = "";
    autosize();
    send(text);
  });

  sendBtn.addEventListener("click", () => { if (current) stop(); });

  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      $("#chat-form").requestSubmit();
    } else if (e.key === "Escape" && current) {
      stop();
    }
  });
  input.addEventListener("input", () => { autosize(); updateSendEnabled(); });

  $("#btn-new").addEventListener("click", () => {
    stop();
    epoch++;
    history.length = 0;
    log.querySelectorAll(".msg").forEach((m) => m.remove());
    $("#chat-empty").hidden = false;
    showView("chat");
  });

  // --- kişilik seçici (sohbet kutusunun sol üstü) ---------------------------------

  const pBtn = $("#persona-btn");
  const pMenu = $("#persona-menu");

  function renderPersonaButton() {
    const p = activePersona();
    $("#persona-name").textContent = p ? p.name : "kişiliksiz";
    pBtn.title = p ? `Kişilik: ${p.name}` : "Kişilik seçilmedi — sistem promptu gönderilmez";
  }

  function closeMenu() {
    pMenu.hidden = true;
    pBtn.setAttribute("aria-expanded", "false");
  }

  function openMenu() {
    pMenu.innerHTML = '<div class="menu-title">KİŞİLİK SEÇ</div>';
    state.personas.forEach((p) => {
      const opt = el("button", "persona-opt" + (p.id === state.activeId ? " is-active" : ""));
      opt.type = "button";
      opt.setAttribute("role", "option");
      opt.setAttribute("aria-selected", String(p.id === state.activeId));
      opt.innerHTML = `<span class="n"></span><span class="p"></span>`;
      $(".n", opt).textContent = p.name;
      $(".p", opt).textContent = p.prompt.trim() || "(boş prompt)";
      opt.addEventListener("click", () => {
        closeMenu();
        selectPersona(p.id);
        input.focus();
      });
      pMenu.appendChild(opt);
    });
    const manage = el("button", "persona-opt is-manage", '<span class="n">Kişilikleri düzenle</span>');
    manage.type = "button";
    manage.addEventListener("click", () => { closeMenu(); showView("settings"); });
    pMenu.appendChild(manage);

    pMenu.hidden = false;
    pBtn.setAttribute("aria-expanded", "true");
    const first = pMenu.querySelector(".persona-opt.is-active") || pMenu.querySelector(".persona-opt");
    if (first) first.focus();
  }

  pBtn.addEventListener("click", () => (pMenu.hidden ? openMenu() : closeMenu()));
  document.addEventListener("click", (e) => {
    if (!pMenu.hidden && !e.target.closest(".persona-pick")) closeMenu();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !pMenu.hidden) { closeMenu(); pBtn.focus(); }
  });
  pMenu.addEventListener("keydown", (e) => {
    if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
    e.preventDefault();
    const opts = [...pMenu.querySelectorAll(".persona-opt")];
    const i = opts.indexOf(document.activeElement);
    const next = opts[(i + (e.key === "ArrowDown" ? 1 : -1) + opts.length) % opts.length];
    next.focus();
  });

  async function selectPersona(id) {
    const prev = state.activeId;
    state.activeId = id;
    renderPersonaButton();
    markActiveCards();
    try {
      applyServerState(await api("/api/active", { method: "PUT", body: { persona_id: id } }));
    } catch (err) {
      state.activeId = prev;
      alert("Kişilik seçilemedi: " + err.message);
    }
    renderPersonaButton();
    markActiveCards();
  }

  // --- ayarlar: model ------------------------------------------------------------

  const modelInput = $("#model-input");
  const modelStatus = $("#model-status");

  function setStatus(node, text, kind) {
    node.textContent = text;
    node.classList.toggle("is-ok", kind === "ok");
    node.classList.toggle("is-bad", kind === "bad");
  }

  function renderModelChip() {
    const chip = $("#chip-model");
    chip.textContent = state.model || "—";
    const missing = state.online && state.installed && !state.installed.includes(state.model);
    chip.classList.toggle("is-warn", Boolean(missing));
    chip.title = missing ? `Model Ollama'da kurulu değil: ollama pull ${state.model}` : "Kullanılan model";
  }

  async function refreshModels() {
    let res;
    try {
      res = await api("/api/models");
    } catch {
      res = { ok: false, models: [], error: "Sunucuya ulaşılamadı" };
    }
    state.online = res.ok;
    state.installed = res.ok ? res.models : null;

    const conn = $("#chip-conn");
    conn.classList.toggle("is-ok", res.ok);
    conn.classList.toggle("is-bad", !res.ok);
    $("span", conn).textContent = res.ok ? "ollama bağlı" : "ollama yok";
    conn.title = res.ok ? "" : res.error || "";

    const list = $("#model-list");
    list.innerHTML = "";
    (res.models || []).forEach((m) => list.appendChild(el("option")).setAttribute("value", m));

    renderModelChip();
    $("#empty-meta").textContent = res.ok
      ? `ollama · ${state.model}`
      : `${res.error || "Ollama'ya ulaşılamadı"} — Ollama'yı başlatıp tekrar dene.`;

    if (!res.ok) setStatus(modelStatus, res.error || "Ollama'ya ulaşılamadı", "bad");
    else if (!res.models.includes(state.model)) setStatus(modelStatus, `"${state.model}" kurulu değil → ollama pull ${state.model}`, "bad");
    else setStatus(modelStatus, `✓ kurulu · ${res.models.length} model bulundu`, "ok");
  }

  async function saveModel() {
    const model = modelInput.value.trim();
    if (!model) return setStatus(modelStatus, "Model adı boş olamaz", "bad");
    try {
      applyServerState(await api("/api/settings", { method: "PUT", body: { model } }));
      await refreshModels();
    } catch (err) {
      setStatus(modelStatus, "Kaydedilemedi: " + err.message, "bad");
    }
  }
  $("#model-save").addEventListener("click", saveModel);
  modelInput.addEventListener("keydown", (e) => { if (e.key === "Enter") saveModel(); });

  // --- ayarlar: kişilikler (master prompt) ----------------------------------------

  const listNode = $("#persona-list");
  const saveTimers = new Map();

  function markActiveCards() {
    listNode.querySelectorAll(".persona-card").forEach((card) => {
      const on = card.dataset.id === state.activeId;
      card.classList.toggle("is-active", on);
      $(".badge", card).hidden = !on;
      $(".use-btn", card).hidden = on;
    });
  }

  function renderPersonaCards() {
    listNode.innerHTML = "";
    if (!state.personas.length) {
      listNode.appendChild(el("p", "muted", "Hiç kişilik yok. <b>+</b> ile ekle — kişiliksiz sohbette sistem promptu gönderilmez."));
    }
    state.personas.forEach((p, idx) => listNode.appendChild(personaCard(p, idx)));
    markActiveCards();
  }

  function personaCard(p, idx) {
    const card = el("div", "persona-card");
    card.dataset.id = p.id;
    card.innerHTML = `
      <div class="persona-card-head">
        <span class="idx">#${String(idx + 1).padStart(2, "0")}</span>
        <input class="name" maxlength="60" spellcheck="false" aria-label="Kişilik adı" />
        <span class="badge">AKTİF</span>
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
      renderPersonaButton();
      setStatus(status, "… kaydediliyor", null);
      clearTimeout(saveTimers.get(p.id));
      saveTimers.set(p.id, setTimeout(save, 600));
    };
    nameIn.addEventListener("input", onEdit);
    promptIn.addEventListener("input", onEdit);

    $(".use-btn", card).addEventListener("click", () => selectPersona(p.id));

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
        applyServerState(await api(`/api/personas/${p.id}`, { method: "DELETE" }));
        renderPersonaCards();
        renderPersonaButton();
      } catch (err) {
        setStatus(status, "silinemedi: " + err.message, "bad");
      }
    });
    return card;
  }

  $("#persona-add").addEventListener("click", async () => {
    try {
      const p = await api("/api/personas", {
        method: "POST",
        body: { name: `Kişilik ${state.personas.length + 1}`, prompt: "" },
      });
      state.personas.push(p);
      if (!state.activeId) state.activeId = p.id;
      renderPersonaCards();
      renderPersonaButton();
      const card = listNode.querySelector(`[data-id="${p.id}"]`);
      card.scrollIntoView({ behavior: "smooth", block: "center" });
      $(".name", card).select();
    } catch (err) {
      alert("Kişilik eklenemedi: " + err.message);
    }
  });

  // --- açılış ------------------------------------------------------------------------

  async function boot() {
    try {
      applyServerState(await api("/api/state"));
    } catch (err) {
      $("#empty-meta").textContent = "Sunucuya ulaşılamadı: " + err.message;
      return;
    }
    modelInput.value = state.model;
    renderPersonaButton();
    renderPersonaCards();
    renderModelChip();
    updateSendEnabled();
    input.focus();
    await refreshModels();
    setInterval(refreshModels, 15000);
  }

  boot();
})();
