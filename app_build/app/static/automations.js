/* FluxPay console — Automações tab: rules CRUD, audit runs and live alerts. */
"use strict";

(function () {
  /* HTML-escape every server-owned string interpolated into markup (XSS). */
  function esc(value) {
    return window.escapeHtml ? window.escapeHtml(value == null ? "" : value) : String(value == null ? "" : value);
  }
  async function loadRules() {
    if (!window.state || !window.state.connected) return;
    const list = document.getElementById("rules-list");
    if (!list) return;
    try {
      const { data } = await window.api("/v1/automations/rules");
      const items = data.data || [];
      if (!items.length) {
        list.innerHTML =
          `<div class="empty-state compact"><strong>Nenhuma regra</strong>` +
          `<span>Uma regra exemplo (webhook falha → alerta) já vem semeada.</span></div>`;
        return;
      }
      list.innerHTML = items
        .map(
          (rule) => `
          <div class="rule-item ${rule.enabled ? "" : "is-disabled"}" data-rule="${esc(rule.id)}">
            <div class="rule-main">
              <b>${esc(rule.name)}</b>
              <small><code>${esc(rule.event_type)}</code> → <code>${esc(rule.action_type)}</code></small>
            </div>
            <div class="rule-tools">
              <button class="btn btn-small btn-ghost" type="button" data-toggle="${esc(rule.id)}">
                ${rule.enabled ? "Desativar" : "Ativar"}
              </button>
              <button class="btn btn-small btn-danger-ghost" type="button" data-delete="${esc(rule.id)}">Excluir</button>
            </div>
          </div>`
        )
        .join("");
      list.querySelectorAll("[data-toggle]").forEach((node) =>
        node.addEventListener("click", async () => {
          const id = node.dataset.toggle;
          const current = items.find((rule) => rule.id === id);
          try {
            await window.api(`/v1/automations/rules/${id}`, {
              method: "PATCH",
              body: { enabled: !current.enabled },
            });
            loadRules();
          } catch (error) {
            window.toast("Falha ao alterar regra", window.errorMessage(error), "error");
          }
        })
      );
      list.querySelectorAll("[data-delete]").forEach((node) =>
        node.addEventListener("click", async () => {
          try {
            await window.api(`/v1/automations/rules/${node.dataset.delete}`, { method: "DELETE" });
            window.toast("Regra excluída", "", "success", 2600);
            loadRules();
          } catch (error) {
            window.toast("Falha ao excluir", window.errorMessage(error), "error");
          }
        })
      );
    } catch (error) {
      window.toast("Falha ao carregar regras", window.errorMessage(error), "error");
    }
  }

  async function loadRuns() {
    if (!window.state || !window.state.connected) return;
    const list = document.getElementById("runs-list");
    if (!list) return;
    try {
      const { data } = await window.api("/v1/automations/runs?limit=25");
      const items = data.data || [];
      if (!items.length) {
        list.innerHTML =
          `<div class="empty-state compact"><strong>Sem execuções</strong>` +
          `<span>Dispare um evento que case com alguma regra.</span></div>`;
        return;
      }
      list.innerHTML = items
        .map(
          (run) => `
          <div class="run-item run-${esc(run.status)}">
            <span class="run-status">${esc(run.status)}</span>
            <div class="run-body">
              <b>${esc(run.rule_name || run.rule_id)}</b>
              <small>${esc(run.event_type)} · ${esc(window.formatDateTime(run.created_at))}</small>
              <small class="muted">${esc(escapeDetail(run.detail))}</small>
            </div>
          </div>`
        )
        .join("");
    } catch { /* transient */ }
  }

  function escapeDetail(detail) {
    if (!detail) return "";
    if (detail.error) return `erro: ${detail.error}`;
    if (detail.message) return detail.message;
    if (detail.reason === "missing customer_email or tag in context") return "aguardando contexto do cliente";
    if (detail.status === "executed" && detail.tag) return `tag aplicada: ${detail.tag}`;
    return "";
  }

  function pushAlert(message) {
    const list = document.getElementById("alert-list");
    if (!list) return;
    const empty = list.querySelector(".empty-state");
    if (empty) empty.remove();
    const node = document.createElement("div");
    node.className = "alert-item";
    node.innerHTML = `<span class="alert-dot"></span><div><b>Alerta automatizado</b><small>${esc(message)}</small></div><small class="muted">agora</small>`;
    list.prepend(node);
    while (list.children.length > 20) list.lastChild.remove();
  }

  function onLiveEvent(event) {
    const detail = event.detail || {};
    if (detail.type === "automation.alert" && detail.data) {
      pushAlert(detail.data.message || "evento recebeu alerta");
      loadRuns();
    }
    if (detail.type === "automation.run") loadRuns();
  }

  function bind() {
    document.addEventListener("fluxpay:event", onLiveEvent);
    const refreshButton = document.getElementById("aut-refresh-btn");
    if (refreshButton) {
      refreshButton.addEventListener("click", () => {
        loadRules();
        loadRuns();
      });
    }
    const form = document.getElementById("rule-form");
    if (form) {
      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        const name = document.getElementById("rule-name").value.trim();
        const eventType = document.getElementById("rule-event").value;
        const action = document.getElementById("rule-action").value;
        const config = {};
        const message = document.getElementById("rule-message").value.trim();
        const tag = document.getElementById("rule-tag").value.trim();
        if (action === "alerta_no_console" && message) config.message = message;
        if (action === "tag_customer") {
          if (!tag) return void window.toast("Informe a tag", "tag_customer precisa de uma tag para aplicar.", "error");
          config.tag = tag;
        }
        try {
          await window.api("/v1/automations/rules", {
            method: "POST",
            body: {
              name,
              event_type: eventType,
              conditions: {},
              action_type: action,
              action_config: config,
            },
          });
          form.reset();
          window.toast("Regra criada", `${name} está ativa.`, "success", 3000);
          loadRules();
        } catch (error) {
          window.toast("Falha ao criar regra", window.errorMessage(error), "error");
        }
      });
    }
  }

  document.addEventListener("DOMContentLoaded", bind);
  window.fluxpayAutomations = { loadRules, loadRuns };
})();
