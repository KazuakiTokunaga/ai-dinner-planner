const form = document.querySelector("#chatForm");
const input = document.querySelector("#messageInput");
const messages = document.querySelector("#messages");
const sourceStatus = document.querySelector("#sourceStatus");
let threadId = null;

function appendMessage(role, text) {
  const element = document.createElement("div");
  element.className = `message ${role}`;
  element.textContent = text;
  messages.append(element);
  messages.scrollTop = messages.scrollHeight;
}

async function sendMessage(message) {
  appendMessage("user", message);
  form.querySelector("button").disabled = true;
  try {
    const response = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, thread_id: threadId }),
    });
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    const data = await response.json();
    threadId = data.thread_id;
    sourceStatus.textContent = data.source;
    appendMessage("assistant", data.message);
  } catch (error) {
    appendMessage("assistant", `通信に失敗しました: ${error.message}`);
  } finally {
    form.querySelector("button").disabled = false;
    input.focus();
  }
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  const message = input.value.trim();
  if (!message) return;
  input.value = "";
  void sendMessage(message);
});

document.querySelectorAll("[data-prompt]").forEach((button) => {
  button.addEventListener("click", () => {
    void sendMessage(button.dataset.prompt);
  });
});

appendMessage("assistant", "登録済みメニューから夕食候補を提案します。食材、カテゴリ、調理器具を指定できます。");