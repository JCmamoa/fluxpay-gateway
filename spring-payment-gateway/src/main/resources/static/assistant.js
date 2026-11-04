(() => {
  const form = document.querySelector(".assistant__form");
  const input = document.querySelector("#message");
  const conversation = document.querySelector(".assistant__conversation");
  const submitButton = form.querySelector("button");
  const errorMessage = document.querySelector(".assistant__error");

  function appendMessage(text, kind) {
    const article = document.createElement("article");
    article.className = `message message--${kind}`;

    if (kind === "assistant" || kind === "pending") {
      const avatar = document.createElement("span");
      avatar.className = "message__avatar";
      avatar.setAttribute("aria-hidden", "true");
      avatar.textContent = "F";
      article.append(avatar);
    }

    const paragraph = document.createElement("p");
    paragraph.textContent = text;
    article.append(paragraph);
    conversation.append(article);
    conversation.scrollTop = conversation.scrollHeight;
    return article;
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const message = input.value.trim();
    if (!message || submitButton.disabled) return;

    errorMessage.hidden = true;
    appendMessage(message, "user");
    input.value = "";
    submitButton.disabled = true;
    input.disabled = true;
    const pending = appendMessage("Estou buscando uma resposta...", "pending");

    try {
      const response = await fetch("/api/support/assistant", {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({ message }),
      });

      if (!response.ok) {
        throw new Error(response.status === 429
          ? "Muitas solicitações. Aguarde um momento e tente novamente."
          : "Não foi possível consultar o assistente. Tente novamente.");
      }

      const result = await response.json();
      pending.remove();
      appendMessage(result.answer, "assistant");
    } catch (error) {
      pending.remove();
      errorMessage.textContent = error instanceof TypeError
        ? "Não foi possível conectar ao serviço. Verifique se a aplicação está em execução."
        : error.message;
      errorMessage.hidden = false;
      input.value = message;
    } finally {
      submitButton.disabled = false;
      input.disabled = false;
      input.focus();
    }
  });
})();
