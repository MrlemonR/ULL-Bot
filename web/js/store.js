/* Uygulamanın paylaşılan durumu ve basit olay yayını. */

export const S = {
  settings: { model: "", appearance: {} },
  personas: [],
  projects: [],
  chats: [],          // özetler: {id, title, project_id, pinned, updated_at}
  installed: [],      // Ollama'daki modeller
  online: null,
  ollamaError: "",
  ollamaUrl: "",
  ollamaUrlDefault: "",
  version: "",
  mode: "chat",       // "chat" | "code"
  codeSessions: [],   // {id, title, cwd, mode, updated_at}
};

const listeners = new Map();

export function on(evt, fn) {
  if (!listeners.has(evt)) listeners.set(evt, new Set());
  listeners.get(evt).add(fn);
}

export function emit(evt, data) {
  (listeners.get(evt) || []).forEach((fn) => fn(data));
}

export const activePersona = () => S.personas.find((p) => p.id === S.settings.active_persona_id) || null;
export const projectById = (id) => S.projects.find((p) => p.id === id) || null;
export const chatById = (id) => S.chats.find((c) => c.id === id) || null;

/** Sunucudan dönen sohbet özetini listeye yerleştir (yoksa ekle). */
export function upsertChat(chat) {
  const i = S.chats.findIndex((c) => c.id === chat.id);
  if (i === -1) S.chats.unshift(chat);
  else S.chats[i] = { ...S.chats[i], ...chat };
  S.chats.sort((a, b) => b.updated_at - a.updated_at);
  emit("chats");
}

export function upsertProject(project) {
  const i = S.projects.findIndex((p) => p.id === project.id);
  if (i === -1) S.projects.push(project);
  else S.projects[i] = project;
  S.projects.sort((a, b) => (b.pinned - a.pinned) || (a.created_at - b.created_at));
  emit("projects");
}

export const codeSessionById = (id) => S.codeSessions.find((x) => x.id === id) || null;

export function upsertCodeSession(sess) {
  const i = S.codeSessions.findIndex((x) => x.id === sess.id);
  if (i === -1) S.codeSessions.unshift(sess);
  else S.codeSessions[i] = { ...S.codeSessions[i], ...sess };
  S.codeSessions.sort((a, b) => b.updated_at - a.updated_at);
  emit("code-sessions");
}
