/* Sohbet ve proje işlemleri — kenar çubuğu, sohbet ve proje sayfası ortak kullanır. */
import { api } from "./api.js";
import { S, emit, projectById, upsertChat, upsertProject } from "./store.js";
import { confirmDialog, promptDialog, toast } from "./ui.js";

const fail = (what) => (err) => toast(`${what}: ${err.message}`, true);

// --- sohbetler -----------------------------------------------------------------

export async function renameChat(chat) {
  const title = await promptDialog({ title: "Sohbeti yeniden adlandır", value: chat.title });
  if (!title || title === chat.title) return;
  try {
    upsertChat(await api(`/api/chats/${chat.id}`, { method: "PATCH", body: { title } }));
  } catch (err) { fail("Adlandırılamadı")(err); }
}

export async function togglePinChat(chat) {
  try {
    upsertChat(await api(`/api/chats/${chat.id}`, { method: "PATCH", body: { pinned: !chat.pinned } }));
  } catch (err) { fail("Sabitlenemedi")(err); }
}

export async function moveChat(chat, projectId) {
  try {
    upsertChat(await api(`/api/chats/${chat.id}`, { method: "PATCH", body: { project_id: projectId } }));
    const p = projectById(projectId);
    toast(p ? `→ ${p.name}` : "→ Diğer");
  } catch (err) { fail("Taşınamadı")(err); }
}

export async function deleteChat(chat) {
  const ok = await confirmDialog({ title: "Sohbeti sil", text: `"${chat.title}" ve tüm mesajları silinsin mi? Geri alınamaz.` });
  if (!ok) return;
  try {
    await api(`/api/chats/${chat.id}`, { method: "DELETE" });
    S.chats = S.chats.filter((c) => c.id !== chat.id);
    emit("chats");
    emit("chat-deleted", chat);
  } catch (err) { fail("Silinemedi")(err); }
}

export function chatMenuItems(chat) {
  const items = [
    { label: chat.pinned ? "Sabitlemeyi kaldır" : "Sabitle", onClick: () => togglePinChat(chat) },
    { label: "Yeniden adlandır", onClick: () => renameChat(chat) },
  ];
  if (S.projects.length || chat.project_id) {
    items.push({ heading: "PROJEYE TAŞI" });
    S.projects.forEach((p) =>
      items.push({ label: p.name, sub: true, current: chat.project_id === p.id, onClick: () => moveChat(chat, p.id) }),
    );
    items.push({ label: "Diğer (projesiz)", sub: true, current: !chat.project_id, onClick: () => moveChat(chat, null) });
  } else {
    items.push({ label: "Yeni projeye taşı…", onClick: () => createProject({ moveChat: chat }) });
  }
  items.push({ label: "Sil", danger: true, onClick: () => deleteChat(chat) });
  return items;
}

// --- projeler -------------------------------------------------------------------

export async function createProject({ moveChat: chatToMove = null } = {}) {
  const name = await promptDialog({
    title: "Yeni proje",
    label: "Proje adı. Projeye özel bağlamı açılan sayfada yazabilirsin.",
    placeholder: "Örn: Freaks Hospital",
    ok: "OLUŞTUR",
  });
  if (!name) return null;
  try {
    const p = await api("/api/projects", { method: "POST", body: { name } });
    upsertProject(p);
    if (chatToMove) await moveChat(chatToMove, p.id);
    else location.hash = `#/p/${p.id}`;
    return p;
  } catch (err) {
    fail("Proje oluşturulamadı")(err);
    return null;
  }
}

export async function renameProject(p) {
  const name = await promptDialog({ title: "Projeyi yeniden adlandır", value: p.name });
  if (!name || name === p.name) return;
  try {
    upsertProject(await api(`/api/projects/${p.id}`, { method: "PATCH", body: { name } }));
  } catch (err) { fail("Adlandırılamadı")(err); }
}

export async function togglePinProject(p) {
  try {
    upsertProject(await api(`/api/projects/${p.id}`, { method: "PATCH", body: { pinned: !p.pinned } }));
  } catch (err) { fail("Sabitlenemedi")(err); }
}

export async function deleteProject(p) {
  const n = S.chats.filter((c) => c.project_id === p.id).length;
  const ok = await confirmDialog({
    title: "Projeyi sil",
    text: `"${p.name}" silinsin mi? Bağlamı silinir; içindeki ${n} sohbet silinmez, "Diğer"e taşınır.`,
  });
  if (!ok) return;
  try {
    await api(`/api/projects/${p.id}`, { method: "DELETE" });
    S.projects = S.projects.filter((x) => x.id !== p.id);
    S.chats.forEach((c) => { if (c.project_id === p.id) c.project_id = null; });
    emit("projects");
    emit("chats");
    emit("project-deleted", p);
  } catch (err) { fail("Silinemedi")(err); }
}

export function projectMenuItems(p) {
  return [
    { label: "Projeyi aç (bağlam)", onClick: () => { location.hash = `#/p/${p.id}`; } },
    { label: "Bu projede yeni sohbet", onClick: () => { location.hash = `#/new/${p.id}`; } },
    { label: p.pinned ? "Sabitlemeyi kaldır" : "Sabitle", onClick: () => togglePinProject(p) },
    { label: "Yeniden adlandır", onClick: () => renameProject(p) },
    { label: "Sil", danger: true, onClick: () => deleteProject(p) },
  ];
}
