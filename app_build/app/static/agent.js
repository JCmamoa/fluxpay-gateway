/* FluxPay console — Assistente IA tab: grounded chat, cited sources and the
 * two-step confirmation flow for sensitive actions (criar_nota / retry_webhook).
 */
"use strict";

(function () {
  /* HTML-escape every server-owned string interpolated into markup (XSS). */
  function esc(value) {
    return window.escapeHtml ? window.escapeHtml(value == null ? "" : value) : String(value == null ? "" : value);
  }
  const history = [];

  function addBubble(kind, html) {
    const log = document.getElementById("chat-log");
    if (!log) return;
    const node = document.createElement("div");
    node.className = `chat-bubble chat-${kind}`;
    node.innerHTML = html;
    log.appendChild(node);
    log.scrollTop = log.scrollHeight;
    return node;
  }

  function renderSources(sources) {
    if (!sources || !sources.length) return "";
    const chips = sources
      .slice(0, 4)
      .map((source) => {
        const title = source.title || source.citation || "fonte";
        return `<span class="source-chip" title="${esc(source.snippet)}">${esc(title.length > 46 ? title.slice(0, 44) + "…" : title)}</span>`;
      })
      .join("");
    return `<div class="chat-sources">${chips}</div>`;
  }

  function markLatency(started) {
    const seconds = ((performance.now() - started) / 1000).toFixed(2);
    return `<small class="chat-meta">resposta em ${seconds}s</small>`;
  }

  async function ask(question) {
    const started = performance.now();
    try {
      const { data } = await window.api("/v1/agent/chat", { method: "POST", body: { question } });
      history.unshift(data);
      renderHistory();
      const pending = data.action && data.action.confirmation_required;
      if (pending) {
        showPending(data);
        addBubble("reply", `
          <p>${esc(data.answer)}</p>
          <div class="chat-confirm">
            <span class="confirm-chip">confirmação necessária · ${esc(data.action.type)}</span>
            <div class="confirm-actions">
              <button class="btn btn-primary btn-small" type="button" id="confirm-action-btn">Confirmar ação</button>
              <button class="btn btn-ghost btn-small" type="button" id="cancel-action-btn">Cancelar</button>
            </div>
          </div>
          ${renderSources(data.sources)}
          ${markLatency(started)}`);
        bindConfirmation(data.action);
      } else {
        addBubble("reply", `
          <p>${escapeMultiline(data.answer)}</p>
          ${renderSources(data.sources)}
          ${markLatency(started)}`);
      }
      const engineBadge = document.getElementById("agent-engine-badge");
      if (engineBadge) {
        engineBadge.textContent = data.engine === "local" ? "modo local (sem LLM)" : `engine: ${data.engine}`;
      }
    } catch (error) {
      addBubble("reply", `<p class="chat-error">${esc(window.errorMessage(error))}</p>`);
    }
  }

  function escapeMultiline(text) {
    return esc(text)
      .split("\n")
      .map((line) => line.replace(/&lt;br\s*\/?&gt;?$/i, ""))
      .join("<br/>");
  }

  function showPending(reply) {
    const box = document.getElementById("agent-pending");
    if (!box) return;
    box.innerHTML = `
      <div class="pending-action">
        <span class="confirm-chip">${esc(reply.action.type)}</span>
        <p>${esc(reply.action.description || "Ação sensível aguardando confirmação")}</p>
        <small class="muted">expira ${esc(window.formatDateTime(reply.action.expires_at))} · token único</small>
      </div>`;
  }

  function bindConfirmation(action) {
    const confirmButton = document.getElementById("confirm-action-btn");
    const cancelButton = document.getElementById("cancel-action-btn");
    if (confirmButton) {
      confirmButton.addEventListener("click", async () => {
        confirmButton.disabled = true;
        confirmButton.textContent = "Executando…";
        try {
          const { data } = await window.api("/v1/agent/confirm", {
            method: "POST",
            body: { token: action.token },
          });
          addBubble("system", `
            <p><strong>Ação executada:</strong> ${esc(data.action_type)}</p>
            ${buildActionSummary(data)}
          `);
          clearPending();
          window.refresh && window.refresh(true);
          if (window.fluxpayCrm) window.fluxpayCrm.loadCustomers();
        } catch (error) {
          confirmButton.disabled = false;
          confirmButton.textContent = "Confirmar ação";
          window.toast("Não foi possível confirmar", window.errorMessage(error), "error");
        }
      });
    }
    if (cancelButton) {
      cancelButton.addEventListener("click", () => {
        addBubble("system", `<p>Ação <strong>${esc(action.type)}</strong> cancelada — nada foi alterado.</p>`);
        clearPending();
      });
    }
  }

  function buildActionSummary(confirmed) {
    let text = "";
    if (confirmed.result && confirmed.result.note) {
      text = `nota <em>“${esc(confirmed.result.note.body)}”</em> adicionada em <code>${esc(confirmed.result.note.customer_id)}</code>.`;
    } else if (confirmed.result && confirmed.result.requeued_deliveries) {
      text = `${confirmed.result.requeued_deliveries.length} entrega(s) de webhook reprocessadas.`;
    } else {
      text = "resultado registrado no audit log.";
    }
    return `<small>${text}</small>`;
  }

  function clearPending() {
    const box = document.getElementById("agent-pending");
    if (box) {
      box.innerHTML =
        `<div class="empty-state compact"><strong>Nenhuma ação pendente</strong>` +
        `<span>Ex.: "adicione uma nota para ana@exemplo.com: 'VIP'"</span></div>`;
    }
  }

  function renderHistory() {
    const list = document.getElementById("agent-history");
    if (!list) return;
    list.innerHTML = history
      .slice(0, 8)
      .map(
        (reply) => `
        <li>
          <p>${esc(reply.answer.length > 90 ? reply.answer.slice(0, 88) + "…" : reply.answer)}</p>
          <small class="muted">${esc(reply.engine)} · ${esc(reply.latency_ms)}ms${reply.sources && reply.sources.length ? ` · ${reply.sources.length} fonte(s)` : ""}</small>
        </li>`
      )
      .join("");
  }

  function bind() {
    const form = document.getElementById("chat-form");
    if (!form) return;
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      const input = document.getElementById("chat-input");
      const question = (input.value || "").trim();
      if (!question) return;
      input.value = "";
      addBubble("ask", `<p>${esc(question)}</p>`);
      const placeholder = addBubble("reply", `<div class="loading-block"><span class="spinner dark"></span> consultando dados…</div>`);
      ask(question).then(() => placeholder && placeholder.remove());
    });
  }

  document.addEventListener("DOMContentLoaded", bind);
  window.fluxpayAgent = { ask: (question) => addBubble("ask", `<p>${esc(question)}</p>`) || ask(question) };
})();
