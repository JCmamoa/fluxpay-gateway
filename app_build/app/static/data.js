/* FluxPay console — Dados tab: analytics_daily rollups, CSV exports and the
 * live event-hub feed (WS + REST snapshot reconciliation). */
"use strict";

(function () {
  /* HTML-escape every server-owned string interpolated into markup (XSS). */
  function esc(value) {
    return window.escapeHtml ? window.escapeHtml(value == null ? "" : value) : String(value == null ? "" : value);
  }
  const feed = [];
  const MAX_FEED = 120;

  function fmtDay(day) {
    if (!day) return "—";
    const [year, month, dayPart] = String(day).split("-");
    return `${dayPart}/${month}/${year}`;
  }

  async function loadDaily() {
    if (!window.state || !window.state.connected) return;
    const rows = document.getElementById("daily-rows");
    if (!rows) return;
    try {
      const { data } = await window.api("/v1/analytics/daily");
      const items = data.data || [];
      if (!items.length) {
        rows.innerHTML =
          `<div class="empty-state compact"><strong>Sem rollups</strong>` +
          `<span>Os jobs agendados consolidam a cada 3 min (ou execute manualmente).</span></div>`;
        return;
      }
      rows.innerHTML = items.slice(0, 30)
        .map(
          (row) => `
          <div class="data-row">
            <span>${esc(fmtDay(row.day))}</span>
            <span>${esc(row.method)}</span>
            <span>${esc(row.attempts)}</span>
            <span class="num-ok">${esc(row.succeeded)}</span>
            <span class="num-bad">${esc(row.failed)}</span>
            <span>${esc(window.money(row.volume_cents))}</span>
            <span>${esc(Math.round((row.approval_rate || 0) * 100))}%</span>
          </div>`
        )
        .join("");
    } catch { /* transient */ }
  }

  function renderFeed() {
    const body = document.getElementById("event-feed");
    if (!body) return;
    if (!feed.length) {
      body.innerHTML =
        `<div class="empty-state compact"><strong>Feed vazio</strong>` +
        `<span>Conecte-se no Gateway para começar a receber eventos.</span></div>`;
      return;
    }
    body.innerHTML = feed
      .map(
        (event) => `
        <div class="feed-item">
          <span class="feed-time">${esc(window.formatClock(event.created_at))}</span>
          <code class="feed-type feed-type-${esc((event.type || "").split(".")[0])}">${esc(event.type)}</code>
          <code class="feed-id">${esc(event.id)}</code>
        </div>`
      )
      .join("");
  }

  function pushEvent(event) {
    if (!event || !event.id) return;
    feed.unshift(event);
    if (feed.length > MAX_FEED) feed.length = MAX_FEED;
    renderFeed();
  }

  async function snapshoot() {
    try {
      const { data } = await window.api("/v1/events?order=desc&limit=25");
      for (const event of (data.data || []).slice().reverse()) {
        if (!feed.some((existing) => existing.id === event.id)) pushEvent(event);
      }
    } catch { /* transient */ }
  }

  async function reindexRag() {
    try {
      const { data } = await window.api("/v1/rag/reindex", { method: "POST" });
      window.toast(
        "RAG reindexado",
        `${data.indexed} novo(s), ${data.unchanged} sem mudança (content-hash).`,
        "success",
        3600
      );
    } catch (error) {
      window.toast("Falha no reindex", window.errorMessage(error), "error");
    }
  }

  function bind() {
    document.addEventListener("fluxpay:event", (event) => pushEvent(event.detail));
    const refreshButton = document.getElementById("data-refresh-btn");
    if (refreshButton) {
      refreshButton.addEventListener("click", () => {
        loadDaily();
        snapshoot();
      });
    }
    const reindexButton = document.getElementById("data-reindex-btn");
    if (reindexButton) reindexButton.addEventListener("click", reindexRag);

    /* Exports need the merchant authorization; build the href lazily. */
    const payments = document.getElementById("export-payments-btn");
    const customers = document.getElementById("export-customers-btn");
    const updateLinks = () => {
      const key = window.state && window.state.apiKey;
      for (const node of [payments, customers]) {
        if (!node) continue;
        // The endpoint itself requires auth; open the console fetch fallback instead.
        node.addEventListener("click", async (event) => {
          event.preventDefault();
          await download(node.getAttribute("href"));
        });
        node.hidden = false;
      }
      void key;
    };
    updateLinks();
  }

  async function download(path) {
    try {
      const headers = { Accept: "text/csv" };
      if (window.state && window.state.apiKey) headers.Authorization = `Bearer ${window.state.apiKey}`;
      const response = await fetch(path, { headers });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = path.split("/").pop() + ".csv";
      anchor.click();
      URL.revokeObjectURL(url);
      window.toast("CSV exportado", path, "success", 3000);
    } catch (error) {
      window.toast("Falha no export", window.errorMessage(error), "error");
    }
  }

  document.addEventListener("DOMContentLoaded", bind);
  window.fluxpayData = { loadDaily, snapshoot, pushEvent };
})();
