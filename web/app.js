const sessionId = crypto.randomUUID();

const form = document.getElementById("chat-form");
const input = document.getElementById("chat-input");
const log = document.getElementById("chat-log");

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const message = input.value.trim();
  if (!message) return;

  input.value = "";
  input.disabled = true;

  appendMessage("user", message);
  const assistantEl = appendMessage("assistant", "");

  try {
    const res = await fetch("/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, session_id: sessionId }),
    });

    if (!res.ok || !res.body) {
      assistantEl.textContent = `Hata: ${res.status}`;
      return;
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      assistantEl.textContent += decoder.decode(value, { stream: true });
      log.scrollTop = log.scrollHeight;
    }
  } catch (err) {
    assistantEl.textContent = `Bağlantı hatası: ${err}`;
  } finally {
    input.disabled = false;
    input.focus();
  }
});

function appendMessage(role, text) {
  const el = document.createElement("div");
  el.className = `msg msg-${role}`;
  el.textContent = text;
  log.appendChild(el);
  log.scrollTop = log.scrollHeight;
  return el;
}
