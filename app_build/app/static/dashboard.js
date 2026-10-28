/* FluxPay console — Dashboard tab: KPIs + Chart.js charts (vendored, offline).
 * Colors are read from CSS custom properties so the charts follow the theme.
 */
"use strict";

(function () {
  /* HTML-escape every server-owned string interpolated into markup (XSS). */
  function esc(value) {
    return window.escapeHtml ? window.escapeHtml(value == null ? "" : value) : String(value == null ? "" : value);
  }
  let charts = { volume: null, mix: null, approval: null };
  let lastSummary = null;
  let lastSeries = null;

  const cssVar = (name) =>
    getComputedStyle(document.documentElement).getPropertyValue(name).trim();

  function palette() {
    return {
      text: cssVar("--muted") || "#8b93a7",
      grid: cssVar("--line-soft") || "rgba(125,135,160,0.16)",
      primary: cssVar("--violet") || "#6366f1",
      primary2: cssVar("--violet-2") || "#9b8cff",
      success: cssVar("--green") || "#22c55e",
      danger: cssVar("--red") || "#ef4444",
      surface: cssVar("--panel") || "#141821",
      pix: cssVar("--green") || "#00b17a",
      credit: cssVar("--violet-2") || "#7c8cf8",
      debit: cssVar("--amber") || "#e8b34b",
    };
  }

  function formatBRL(value) {
    return Number(value || 0).toLocaleString("pt-BR", { style: "currency", currency: "BRL" });
  }

  async function refresh(force = false) {
    if (!window.state || !window.state.connected) return;
    const page = document.getElementById("page-dashboard");
    if (!page && !force) return;
    try {
      const [summaryRes, timeseriesRes] = await Promise.all([
        window.api("/v1/analytics/summary"),
        window.api("/v1/analytics/timeseries?metric=volume&granularity=day"),
      ]);
      lastSummary = summaryRes.data;
      lastSeries = timeseriesRes.data;
      render();
    } catch (error) {
      if (force && window.toast) window.toast("Dashboard não atualizado", window.errorMessage(error), "error");
    }
  }

  function renderKPIs() {
    const today = lastSummary.today || {};
    const set = (id, value) => {
      const node = document.getElementById(id);
      if (node) node.textContent = value;
    };
    set("dash-attempts", String(today.payments ?? "—"));
    set("dash-approvals", String(today.approvals ?? "—"));
    set("dash-approval-rate", `${Math.round((today.approval_rate || 0) * 100)}% de aprovação`);
    set("dash-revenue", formatBRL(today.revenue_cents));
    set("dash-pending", String(today.pending ?? "—"));

    const queue = lastSummary.queue || {};
    const deliveries = lastSummary.webhooks || {};
    set("dash-queue-pending", String(queue.pending ?? 0));
    set("dash-queue-processing", String(queue.processing ?? 0));
    set("dash-queue-done", String(queue.done ?? 0));
    set("dash-queue-failed", String(queue.failed ?? 0));
    set("dash-delivered", String(deliveries.delivered ?? 0));
    set("dash-delivery-pending", String(deliveries.pending ?? 0));
    set("dash-dlq", String(deliveries.dlq ?? deliveries.failed ?? 0));

    const reasons = document.getElementById("dash-failure-reasons");
    if (reasons) {
      reasons.innerHTML =
        (lastSummary.top_failure_reasons || []).map((row) => `
          <li><span>${esc(row.reason)}</span><b>${esc(row.count)}</b></li>`).join("") ||
        `<li class="muted">Nenhuma falha nos últimos 7 dias</li>`;
    }
    const tops = document.getElementById("dash-top-customers");
    if (tops) {
      tops.innerHTML =
        (lastSummary.top_customers || []).map((row) => `
          <li><span>${esc(row.email)}</span><b>${esc(formatBRL(row.total_cents))}</b></li>`).join("") ||
        `<li class="muted">Aguardando pagamentos aprovados</li>`;
    }
  }

  function renderVolumeChart() {
    if (!window.Chart || !lastSeries) return;
    const canvas = document.getElementById("chart-volume");
    if (!canvas) return;
    const colors = palette();
    const points = lastSeries.series || [];
    const labels = points.map((point) => point.bucket);
    const values = points.map((point) => Number(point.volume || 0));

    if (charts.volume) charts.volume.destroy();
    charts.volume = new window.Chart(canvas, {
      type: "line",
      data: {
        labels,
        datasets: [
          {
            label: "Volume aprovado (R$)",
            data: values.map((value) => value / 100),
            borderColor: colors.primary,
            backgroundColor: `${colors.primary}33`,
            fill: true,
            tension: 0.35,
            pointRadius: 2,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: { duration: 400 },
        plugins: {
          legend: { display: false },
          tooltip: { callbacks: { label: (ctx) => formatBRL(ctx.parsed.y * 100) } },
        },
        scales: {
          y: { ticks: { callback: (value) => `R$ ${value}` }, grid: { color: colors.grid } },
          x: { grid: { display: false }, ticks: { maxTicksLimit: 10 } },
        },
      },
    });
  }

  function renderMixChart() {
    if (!window.Chart || !lastSummary) return;
    const canvas = document.getElementById("chart-mix");
    if (!canvas) return;
    const colors = palette();
    const today = lastSummary.today || {};
    const mix = today.method_mix || {};
    const labels = ["PIX", "Crédito", "Débito"];
    const values = [
      Number(mix.pix?.revenue_cents || 0) / 100,
      Number(mix.credit_card?.revenue_cents || 0) / 100,
      Number(mix.debit_card?.revenue_cents || 0) / 100,
    ];
    if (values.every((value) => value === 0)) {
      labels.push("sem dados");
      values.push(1);
    }

    if (charts.mix) charts.mix.destroy();
    charts.mix = new window.Chart(canvas, {
      type: "doughnut",
      data: {
        labels,
        datasets: [{
          data: values,
          backgroundColor: [colors.pix, colors.credit, colors.debit, colors.grid],
          borderColor: colors.surface,
          borderWidth: 2,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        cutout: "62%",
        plugins: { legend: { position: "bottom" }, tooltips: { enabled: true } },
      },
    });
  }

  function renderApprovalChart() {
    if (!window.Chart || !lastSeries) return;
    const canvas = document.getElementById("chart-approval");
    if (!canvas) return;
    const colors = palette();
    const points = (lastSeries.series || []).slice(-7);
    const labels = points.map((point) => point.bucket);
    const approvals = points.map((point) => Number(point.approvals || 0));
    const failures = points.map((point) => Number(point.failures || 0));

    if (charts.approval) charts.approval.destroy();
    charts.approval = new window.Chart(canvas, {
      type: "bar",
      data: {
        labels,
        datasets: [
          { label: "Aprovados", data: approvals, backgroundColor: colors.success, borderRadius: 6, stack: "s" },
          { label: "Falhas", data: failures, backgroundColor: colors.danger, borderRadius: 6, stack: "s" },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: { duration: 400 },
        plugins: { legend: { position: "bottom" } },
        scales: {
          y: {beginAtZero: true, ticks: { precision: 0 }, grid: { color: colors.grid } },
          x: { stacked: true, grid: { display: false } },
        },
      },
    });
  }

  function render() {
    // Chart.js global defaults must follow the active theme palette.
    if (window.Chart) {
      const colors = palette();
      window.Chart.defaults.color = colors.text;
      window.Chart.defaults.borderColor = colors.grid;
    }
    renderKPIs();
    renderVolumeChart();
    renderMixChart();
    renderApprovalChart();
  }

  function onThemeChange() {
    /* Re-render charts so axes/labels follow the new palette. */
    if (lastSummary || lastSeries) render();
  }

  document.addEventListener("DOMContentLoaded", () => {
    document.addEventListener("fluxpay:event", (event) => {
      const detail = event.detail || {};
      if (
        String(detail.type || "").startsWith("payment.") ||

        String(detail.type || "").startsWith("webhook.")
      ) {
        refresh();
      }
    });
    document.addEventListener("fluxpay:theme", onThemeChange);
    const toggle = document.getElementById("theme-toggle");
    if (toggle) toggle.addEventListener("click", () => window.setTimeout(onThemeChange, 60));
  });

  window.fluxpayDashboard = { refresh, render };
})();
