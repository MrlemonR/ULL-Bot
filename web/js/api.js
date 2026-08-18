/* REST istemcisi.
 *
 * Backend hata gövdesini FastAPI'nin `{"detail": "..."}` biçiminde döndürüyor;
 * `request()` bunu okunur bir Error'a çeviriyor ki çağıran taraf her yerde
 * aynı `catch (error) { toast(error.message) }` kalıbını kullanabilsin.
 */

async function request(path, { method = "GET", body, timeout = 120000 } = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);
  let response;
  try {
    response = await fetch(path, {
      method,
      headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
      body: body !== undefined ? JSON.stringify(body) : undefined,
      signal: controller.signal,
    });
  } catch (error) {
    clearTimeout(timer);
    if (error.name === "AbortError") throw new Error("İstek zaman aşımına uğradı.");
    throw new Error(`Sunucuya ulaşılamadı: ${error.message}`);
  }
  clearTimeout(timer);

  const text = await response.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = null;
  }

  if (!response.ok) {
    const detail = data?.detail || data?.error || text || `HTTP ${response.status}`;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return data;
}

export const api = {
  config: () => request("/api/config"),

  quota: (probe = false) => request(`/api/quota?probe=${probe}`),
  quotaControl: (provider, action) => request(`/api/quota/${provider}/${action}`, { method: "POST" }),
  usageGraph: (days = 14) => request(`/api/usage/graph?days=${days}`),

  sessions: (limit = 50) => request(`/api/sessions?limit=${limit}`),
  sessionMessages: (id) => request(`/api/sessions/${encodeURIComponent(id)}/messages`),
  search: (query) => request(`/api/search?q=${encodeURIComponent(query)}`),

  memory: () => request("/api/memory"),
  memoryDelete: (key) => request(`/api/memory/${encodeURIComponent(key)}`, { method: "DELETE" }),

  /* --- mail --- */
  mailAccounts: () => request("/api/mail/accounts"),
  mailAccountTest: (payload) => request("/api/mail/accounts/test", { method: "POST", body: payload, timeout: 45000 }),
  mailAccountAdd: (payload) => request("/api/mail/accounts", { method: "POST", body: payload, timeout: 45000 }),
  mailAccountDelete: (id) => request(`/api/mail/accounts/${id}`, { method: "DELETE" }),
  mailSync: (accountId) =>
    request(`/api/mail/sync${accountId ? `?account_id=${accountId}` : ""}`, { method: "POST", timeout: 180000 }),
  mailMessages: (params = {}) => {
    const query = new URLSearchParams();
    Object.entries(params).forEach(([key, value]) => {
      if (value !== undefined && value !== null && value !== "" && value !== false) {
        query.set(key, String(value));
      }
    });
    return request(`/api/mail/messages?${query}`);
  },
  mailMessage: (id) => request(`/api/mail/messages/${id}`),
  mailMark: (id, payload) => request(`/api/mail/messages/${id}/mark`, { method: "POST", body: payload }),
  mailMove: (id, destination) => request(`/api/mail/messages/${id}/move`, { method: "POST", body: { destination } }),
  mailCategory: (id, category) => request(`/api/mail/messages/${id}/category`, { method: "POST", body: { category } }),
  mailSummarize: (id, force = false) =>
    request(`/api/mail/messages/${id}/summarize?force=${force}`, { method: "POST", timeout: 180000 }),
  mailCategorizeBatch: (limit = 15) =>
    request(`/api/mail/categorize?limit=${limit}`, { method: "POST", timeout: 180000 }),

  /* --- takvim --- */
  calendarEvents: (start, end, q = "") => {
    const query = new URLSearchParams();
    if (start) query.set("start", start);
    if (end) query.set("end", end);
    if (q) query.set("q", q);
    return request(`/api/calendar/events?${query}`);
  },
  calendarUpcoming: (limit = 10) => request(`/api/calendar/upcoming?limit=${limit}`),
  calendarCreate: (payload) => request("/api/calendar/events", { method: "POST", body: payload }),
  calendarUpdate: (id, payload) => request(`/api/calendar/events/${id}`, { method: "PATCH", body: payload }),
  calendarDelete: (id) => request(`/api/calendar/events/${id}`, { method: "DELETE" }),
  calendarPending: () => request("/api/calendar/pending-meetings"),
  calendarDraftFromMail: (messageId) => request(`/api/calendar/draft-from-mail/${messageId}`),
  calendarFromMail: (messageId, payload = {}) =>
    request(`/api/calendar/from-mail/${messageId}`, { method: "POST", body: payload }),
  calendarImport: (ics) => request("/api/calendar/import", { method: "POST", body: { ics } }),

  /* --- bildirim --- */
  notifications: () => request("/api/notifications"),
  notificationTest: () => request("/api/notifications/test", { method: "POST" }),

  /* --- sistem --- */
  openExternal: (url) => request("/api/open-external", { method: "POST", body: { url } }),
};
