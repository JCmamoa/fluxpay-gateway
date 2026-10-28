/* FluxPay console — CRM tab: customers (e-mail natural key) + 360° view. */
"use strict";

(function () {
  /* HTML-escape every server-owned string interpolated into markup (XSS). */
  function esc(value) {
    return window.escapeHtml ? window.escapeHtml(value == null ? "" : value) : String(value == null ? "" : value);
  }
  const filters = { q: "", tag: "" };
  let selected = null;

  function badgeLifecycle(status) {
    const labels = {
      lead: "Lead",
      active: "Ativo",
      at_risk: "Em risco",
      churned: "Churned",
    };
    return `<span class="badge badge-${status === "active" ? "succeeded" : "neutral"}">${esc(labels[status] || status || "—")}</span>`;
  }

  function client() {
    return { api: window.api, toast: window.toast, errorMessage: window.errorMessage };
  }

  async function loadCustomers() {
    if (!window.state || !window.state.connected) return;
    const rows = document.getElementById("crm-rows");
    if (!rows) return;
    const params = new URLSearchParams({ limit: "50" });
    if (filters.q) params.set("q", filters.q);
    if (filters.tag) params.set("tag", filters.tag);
    try {
      const { data } = await client().api(`/v1/customers?${params}`);
      const items = data.data || [];
      if (!items.length) {
        rows.innerHTML =
          `<div class="empty-state compact"><strong>Nenhum cliente</strong>` +
          `<span>Crie um pagamento com customer.email no Gateway.</span></div>`;
        return;
      }
      rows.innerHTML = items
        .map(
          (item) => `
          <div class="crm-row" tabindex="0" role="button" data-customer="${esc(item.id)}">
            <span class="crm-customer">
              <b>${esc(item.name || "(sem nome)")}</b>
              <small>${esc(item.email)}</small>
            </span>
            <span>${esc(item.payments_count)} · ${esc(item.succeeded_count)} ok</span>
            <span>${esc(window.money(item.total_succeeded_cents))}</span>
            <span class="crm-tags">${(item.tags || []).map((t) => `<em>${esc(t)}</em>`).join(" ") || "—"}</span>
            <span class="crm-segments">${esc((item.segments || []).map((s) => s.name).join(", ")) || "—"}</span>
          </div>`
        )
        .join("");
      rows.querySelectorAll("[data-customer]").forEach((node) => {
        node.addEventListener("click", () => select(node.dataset.customer));
        node.addEventListener("keydown", (event) => {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            select(node.dataset.customer);
          }
        });
      });
    } catch (error) {
      if (!window.toast) return;
      window.toast("Falha ao carregar clientes", window.errorMessage(error), "error");
    }
  }

  async function select(customerId) {
    selected = customerId;
    const detail = document.getElementById("crm-detail");
    if (!detail) return;
    detail.innerHTML = `<div class="loading-block"><span class="spinner dark"></span> Carregando 360°…</div>`;
    try {
      const { data } = await client().api(`/v1/customers/${customerId}`);
      renderCustomer(data);
    } catch (error) {
      detail.innerHTML = `<div class="empty-state compact"><strong>Não foi possível carregar</strong><span>${esc(window.errorMessage(error))}</span></div>`;
    }
  }

  function renderCustomer(view) {
    const title = document.getElementById("crm-detail-title");
    if (title) title.textContent = view.email;
    const detail = document.getElementById("crm-detail");
    if (!detail) return;

    detail.innerHTML = `
      <div class="crm-summary">
        <span class="crm-name">${esc(view.name || "(sem nome)")} ${badgeLifecycle(view.lifecycle_status)}</span>
        <span class="crm-stats">
          <b>${esc(view.payments_count)}</b> pagamentos ·
          <b>${esc(view.succeeded_count)}</b> aprovados ·
          <b>${esc(window.money(view.total_succeeded_cents))}</b> recebidos
        </span>
      </div>
      <div class="crm-detail-block">
        <h4>Tags</h4>
        <div class="crm-tags-row">
          <span class="crm-tag-chips" id="crm-tag-chips">${(view.tags || []).map((t) => `<em>${esc(t)}</em>`).join(" ") || "<small>sem tags</small>"}</span>
        </div>
        <form id="crm-tag-form" class="crm-inline-form">
          <input id="crm-tag-input" class="key-input" type="text" placeholder="nova tag" />
          <button class="btn btn-small" type="submit">Tag</button>
        </form>
      </div>
      <div class="crm-detail-block">
        <h4>Notas</h4>
        <ul class="crm-notes" id="crm-notes">
          ${(view.notes || []).map((n) => `<li><p>${esc(n.body)}</p><small>${esc(n.author)} · ${esc(window.formatDateTime(n.created_at))}</small></li>`).join("") || "<li class='muted'>Sem notas ainda</li>"}
        </ul>
        <form id="crm-note-form" class="crm-inline-form">
          <input id="crm-note-input" class="key-input" type="text" placeholder="registrar nota de atendimento…" />
          <button class="btn btn-small" type="submit">Nota</button>
        </form>
      </div>
      <div class="crm-detail-block">
        <h4>Pagamentos</h4>
        <ul class="crm-client-payments">
          ${(view.payments || []).map((p) => `
            <li><span>${esc(p.id)} · ${esc(p.method)}</span><span>${esc(window.money(p.amount))}</span><span class="status-badge status-${esc(p.status)}">${esc(p.status)}</span></li>`).join("") || "<li class='muted'>Nada ainda</li>"}
        </ul>
      </div>
      <div class="crm-detail-block">
        <h4>Timeline (append-only)</h4>
        <ul class="crm-timeline">
          ${(view.events || []).slice(0, 12).map((e) => `<li><span class="crm-event-type">${esc(e.type)}</span><small>${esc(window.formatDateTime(e.created_at))}</small></li>`).join("") || "<li class='muted'>Sem eventos</li>"}
        </ul>
      </div>
      <div class="crm-detail-block">
        <h4>Segmentos</h4>
        <ul class="crm-segments-list">
          ${(view.segments || []).map((s) => `<li>${esc(s.name)} <small>entrada ${esc(window.formatDateTime(s.added_at))}</small></li>`).join("") || "<li class='muted'>Nenhum segmento</li>"}
        </ul>
      </div>
    `;

    const tagForm = document.getElementById("crm-tag-form");
    if (tagForm) {
      tagForm.addEventListener("submit", async (event) => {
        event.preventDefault();
        const input = document.getElementById("crm-tag-input");
        const tag = (input.value || "").trim();
        if (!tag) return;
        try {
          await client().api(`/v1/customers/${selected}/tags`, { method: "POST", body: { tag } });
          input.value = "";
          fetch360();
          loadCustomers();
        } catch (error) {
          window.toast("Falha ao adicionar tag", window.errorMessage(error), "error");
        }
      });
    }
    const noteForm = document.getElementById("crm-note-form");
    if (noteForm) {
      noteForm.addEventListener("submit", async (event) => {
        event.preventDefault();
        const input = document.getElementById("crm-note-input");
        const body = (input.value || "").trim();
        if (!body) return;
        try {
          await client().api(`/v1/customers/${selected}/notes`, { method: "POST", body: { body } });
          input.value = "";
          fetch360();
        } catch (error) {
          window.toast("Falha ao adicionar nota", window.errorMessage(error), "error");
        }
      });
    }
  }

  async function fetch360() {
    if (selected) {
      try {
        const { data } = await client().api(`/v1/customers/${selected}`);
        renderCustomer(data);
      } catch { /* transient */ }
    }
  }

  function bind() {
    const apply = document.getElementById("crm-apply-filters");
    if (apply) {
      apply.addEventListener("click", () => {
        filters.q = (document.getElementById("crm-search").value || "").trim();
        filters.tag = (document.getElementById("crm-tag-filter").value || "").trim().toLowerCase();
        loadCustomers();
      });
    }
    const refreshButton = document.getElementById("crm-refresh-btn");
    if (refreshButton) refreshButton.addEventListener("click", loadCustomers);
    document.addEventListener("fluxpay:event", (event) => {
      const type = String((event.detail || {}).type || "");
      if (type.startsWith("crm.") || type.startsWith("payment.")) {
        loadCustomers();
        fetch360();
      }
    });
  }

  document.addEventListener("DOMContentLoaded", bind);
  window.fluxpayCrm = { loadCustomers, select, fetch360, filters };
})();
