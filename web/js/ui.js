/* Ortak arayüz parçaları: diyaloglar, bağlam menüsü, bildirim. */
import { $, el, esc } from "./util.js";

// --- bildirim (toast) ---------------------------------------------------------

let toastTimer = 0;
export function toast(text, bad = false) {
  const t = $("#toast");
  t.textContent = text;
  t.classList.toggle("is-bad", bad);
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; }, bad ? 4500 : 2200);
}

// --- diyaloglar ----------------------------------------------------------------

export function openModal({ title, body, buttons, wide = false }) {
  return new Promise((resolve) => {
    const back = $("#modal");
    $(".modal", back).classList.toggle("is-wide", wide);
    $("#modal-title").textContent = title;
    const bodyNode = $("#modal-body");
    bodyNode.innerHTML = "";
    if (typeof body === "string") bodyNode.innerHTML = body;
    else if (body) bodyNode.appendChild(body);
    const foot = $("#modal-foot");
    foot.innerHTML = "";

    const prevFocus = document.activeElement;
    const close = (value) => {
      back.hidden = true;
      document.removeEventListener("keydown", onKey, true);
      back.removeEventListener("mousedown", onBack);
      if (prevFocus && prevFocus.focus) prevFocus.focus();
      resolve(value);
    };
    const onKey = (e) => {
      if (e.key === "Escape") { e.stopPropagation(); close(null); }
    };
    const onBack = (e) => { if (e.target === back) close(null); };

    buttons.forEach((b) => {
      const btn = el("button", `btn ${b.cls || ""}`, esc(b.label));
      btn.type = "button";
      // `action`: pencereyi kapatmadan bir şey yap (ör. klasörü aç).
      btn.addEventListener("click", () => (b.action ? b.action(btn) : close(b.value())));
      if (b.left) btn.style.marginRight = "auto";
      foot.appendChild(btn);
    });

    back.hidden = false;
    document.addEventListener("keydown", onKey, true);
    back.addEventListener("mousedown", onBack);
    const first = bodyNode.querySelector("input, textarea") || foot.lastElementChild;
    if (first) {
      first.focus();
      if (first.select) first.select();
    }
    bodyNode.querySelectorAll("input").forEach((inp) =>
      inp.addEventListener("keydown", (e) => {
        if (e.key === "Enter") { e.preventDefault(); foot.lastElementChild.click(); }
      }),
    );
  });
}

export function promptDialog({ title, label = "", value = "", placeholder = "", ok = "KAYDET" }) {
  const wrap = el("div");
  if (label) wrap.appendChild(el("div", "muted", esc(label)));
  const input = el("input");
  input.type = "text";
  input.value = value;
  input.placeholder = placeholder;
  input.maxLength = 120;
  wrap.appendChild(input);
  wrap.style.display = "contents";
  return openModal({
    title,
    body: wrap,
    buttons: [
      { label: "VAZGEÇ", cls: "btn-ghost", value: () => null },
      { label: ok, cls: "btn-solid", value: () => input.value.trim() || null },
    ],
  });
}

export function confirmDialog({ title, text, ok = "SİL", danger = true }) {
  return openModal({
    title,
    body: `<div>${esc(text)}</div>`,
    buttons: [
      { label: "VAZGEÇ", cls: "btn-ghost", value: () => false },
      { label: ok, cls: danger ? "btn-danger is-armed" : "btn-solid", value: () => true },
    ],
  });
}

// --- bağlam menüsü --------------------------------------------------------------
//
// items: [{label, onClick, danger, sub, current} | {heading} ]

let ctxOwner = null;

export function closeContextMenu() {
  const m = $("#ctx-menu");
  m.hidden = true;
  if (ctxOwner) ctxOwner.setAttribute("aria-expanded", "false");
  ctxOwner = null;
}

export function openContextMenu(anchor, items) {
  const m = $("#ctx-menu");
  if (ctxOwner === anchor && !m.hidden) return closeContextMenu();
  closeContextMenu();
  m.innerHTML = "";
  items.forEach((it) => {
    if (it.heading) {
      m.appendChild(el("div", "pop-sub", esc(it.heading)));
      return;
    }
    const b = el("button", "pop-item" + (it.danger ? " is-danger" : "") + (it.sub ? " is-sub" : "") + (it.current ? " is-current" : ""));
    b.type = "button";
    b.textContent = it.label;
    b.addEventListener("click", () => {
      if (it.current) return;
      closeContextMenu();
      it.onClick();
    });
    m.appendChild(b);
  });
  m.hidden = false;
  ctxOwner = anchor;
  anchor.setAttribute("aria-expanded", "true");

  // Ekrana sığacak şekilde yerleştir.
  const r = anchor.getBoundingClientRect();
  const mw = m.offsetWidth, mh = m.offsetHeight;
  let left = r.right - mw;
  if (left < 8) left = Math.min(r.left, window.innerWidth - mw - 8);
  let top = r.bottom + 4;
  if (top + mh > window.innerHeight - 8) top = Math.max(8, r.top - mh - 4);
  m.style.left = `${Math.max(8, left)}px`;
  m.style.top = `${top}px`;
  const first = m.querySelector(".pop-item");
  if (first) first.focus();
}

document.addEventListener("mousedown", (e) => {
  const m = $("#ctx-menu");
  if (!m.hidden && !m.contains(e.target) && e.target !== ctxOwner && !ctxOwner?.contains(e.target)) closeContextMenu();
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !$("#ctx-menu").hidden) {
    const owner = ctxOwner;
    closeContextMenu();
    owner?.focus();
  }
});
window.addEventListener("resize", closeContextMenu);

/** Açılır panel (model / kişilik menüsü) için ok tuşlarıyla gezinme. */
export function arrowNav(container, selector = ".pop-item") {
  container.addEventListener("keydown", (e) => {
    if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
    e.preventDefault();
    const items = [...container.querySelectorAll(selector)];
    const i = items.indexOf(document.activeElement);
    items[(i + (e.key === "ArrowDown" ? 1 : -1) + items.length) % items.length]?.focus();
  });
}
