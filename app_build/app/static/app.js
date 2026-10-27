/* FluxPay demo console — vanilla JS, no external dependencies. */
"use strict";

const KEY_STORAGE = "fluxpay_api_key";
const POLL_INTERVAL_MS = 2500;

const state = {
  apiKey: localStorage.getItem(KEY_STORAGE) || "",
  config: null,
  merchant: null,
  connected: false,
  demoMode: true,
  method: "pix",
  payments: [],
  deliveries: [],
  receipts: [],
  endpoints: [],
  metrics: null,
  failNext: 0,
  filter: "all",
  deliveryFilter: "",
  selectedPaymentId: null,
  pollTimer: null,
  pollBusy: false,
  modalBusy: false,
  authErrorShown: false,
};

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

const SVG = {
  pix: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><path d="m12 3 4.2 4.2L12 11.4 7.8 7.2 12 3Zm0 18 4.2-4.2L12 12.6l-4.2 4.2L12 21ZM3 12l4.2-4.2L11.4 12l-4.2 4.2L3 12Zm18 0-4.2-4.2L12.6 12l4.2 4.2L21 12Z" stroke-linejoin="round"/></svg>',
  card: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><rect x="2.5" y="5" width="19" height="14" rx="2.5"/><path d="M2.5 9.5h19M6 15h4" stroke-linecap="round"/></svg>',
  chevron: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="m9 6 6 6-6 6" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  bell: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><path d="M18 9a6 6 0 1 0-12 0c0 5-2 6-2 6h16s-2-1-2-6M10.3 20a2 2 0 0 0 3.4 0" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  check: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="m5 13 4 4L19 7" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  alert: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="12" cy="12" r="9"/><path d="M12 7v6m0 4h.01" stroke-linecap="round"/></svg>',
  info: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="12" cy="12" r="9"/><path d="M12 11v6m0-9h.01" stroke-linecap="round"/></svg>',
  copy: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h10" stroke-linecap="round"/></svg>',
  eye: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12Z"/><circle cx="12" cy="12" r="2.6"/></svg>',
  retry: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M20 11a8 8 0 1 0-2.3 6.3M20 5v6h-6" stroke-linecap="round" stroke-linejoin="round"/></svg>',
};

const STATUS_LABEL = {
  pending: "Pendente",
  processing: "Processando",
  succeeded: "Aprovado",
  failed: "Falha",
  delivered: "Entregue",
};

const EVENT_LABEL = {
  "payment.pending": "Cobrança criada",
  "payment.processing": "Processando autorização",
  "payment.succeeded": "Pagamento aprovado",
  "payment.failed": "Pagamento recusado",
};

const METHOD_LABEL = {
  pix: "PIX",
  credit_card: "Crédito",
  debit_card: "Débito",
};

/* ------------------------------------------------------------------ */
/* Utilities                                                           */
/* ------------------------------------------------------------------ */
function escapeHtml(value) {
  return String(value == null ? "" : value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function money(cents) {
  const value = Number(cents || 0) / 100;
  return value.toLocaleString("pt-BR", { style: "currency", currency: "BRL" });
}

function centsFromInput(raw) {
  let text = String(raw || "").trim().replace(/R\$/gi, "").replace(/\s/g, "");
  if (!text) return NaN;
  const hasComma = text.includes(",");
  const hasDot = text.includes(".");
  if (hasComma && hasDot) {
    text = text.replace(/\./g, "").replace(",", ".");
  } else if (hasComma) {
    text = text.replace(",", ".");
  }
  if (!/^\d+(\.\d{1,2})?$/.test(text)) return NaN;
  return Math.round(Number(text) * 100);
}

function formatClock(iso) {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function formatDateTime(iso) {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleString("pt-BR", {
    day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit",
  });
}

function relative(iso) {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  const diff = Math.round((date.getTime() - Date.now()) / 1000);
  const abs = Math.abs(diff);
  if (abs < 5) return diff >= 0 ? "agora" : "agora";
  let text;
  if (abs < 60) text = `${abs}s`;
  else if (abs < 3600) text = `${Math.floor(abs / 60)}min`;
  else if (abs < 86400) text = `${Math.floor(abs / 3600)}h`;
  else text = `${Math.floor(abs / 86400)}d`;
  return diff > 0 ? `em ${text}` : `há ${text}`;
}

function secondsLabel(seconds) {
  if (seconds === 0) return "0s";
  if (seconds % 3600 === 0) return `${seconds / 3600}h`;
  if (seconds % 60 === 0) return `${seconds / 60}min`;
  return `${seconds}s`;
}

function badge(status) {
  const normalized = status || "pending";
  return `<span class="badge badge-${escapeHtml(normalized)}">${escapeHtml(STATUS_LABEL[normalized] || normalized)}</span>`;
}

function methodIcon(method) {
  return method === "pix" ? SVG.pix : SVG.card;
}

function makeIdempotencyKey() {
  if (window.crypto && typeof window.crypto.randomUUID === "function") {
    return `idem_${window.crypto.randomUUID()}`;
  }
  return `idem_${Date.now()}_${Math.random().toString(16).slice(2)}`;
}

async function api(path, options = {}) {
  const headers = { Accept: "application/json", ...(options.headers || {}) };
  if (state.apiKey) headers.Authorization = `Bearer ${state.apiKey}`;
  const init = { ...options, headers };
  if (options.body !== undefined) {
    headers["Content-Type"] = "application/json";
    init.body = typeof options.body === "string" ? options.body : JSON.stringify(options.body);
  }
  let response;
  try {
    response = await fetch(path, init);
  } catch (cause) {
    const error = new Error("Não foi possível falar com o gateway. Ele está rodando?");
    error.code = "network_error";
    throw error;
  }
  const text = await response.text();
  let data = null;
  if (text) {
    try { data = JSON.parse(text); } catch { data = null; }
  }
  if (!response.ok) {
    const payload = data && data.error ? data.error : {};
    const error = new Error(payload.message || `HTTP ${response.status}`);
    error.status = response.status;
    error.code = payload.code || `http_${response.status}`;
    error.details = payload.details;
    throw error;
  }
  return { data, response, text };
}

function errorMessage(error) {
  if (!error) return "Erro inesperado";
  if (Array.isArray(error.details) && error.details.length) {
    const first = error.details[0];
    if (typeof first === "string") return first;
    return first.msg || error.message;
  }
  return error.message || "Erro inesperado";
}

/* ------------------------------------------------------------------ */
/* Toasts                                                              */
/* ------------------------------------------------------------------ */
function toast(title, message = "", type = "info", timeout = 4800) {
  const stack = $("#toast-stack");
  if (!stack) return;
  const node = document.createElement("div");
  node.className = `toast ${type}`;
  const icon = type === "success" ? SVG.check : type === "error" ? SVG.alert : SVG.info;
  node.innerHTML = `<span class="toast-icon">${icon}</span><div><span class="toast-title">${escapeHtml(title)}</span>${message ? `<span class="toast-msg">${escapeHtml(message)}</span>` : ""}</div>`;
  stack.appendChild(node);
  window.setTimeout(() => {
    node.classList.add("leaving");
    window.setTimeout(() => node.remove(), 220);
  }, timeout);
}

/* ------------------------------------------------------------------ */
/* Demo bootstrap, connection and polling                              */
/* ------------------------------------------------------------------ */
async function loadDemoConfig() {
  try {
    const { data } = await api("/demo/config");
    state.config = data;
    state.demoMode = Boolean(data.demo_mode);
    state.failNext = data.fail_next_webhooks || 0;
    const endpoint = data.webhook_endpoint;
    if (endpoint) {
      $("#endpoint-url").textContent = endpoint.url;
      $("#endpoint-url").title = endpoint.url;
      $("#endpoint-status").textContent = endpoint.active ? "ativo · receiver local simulado" : "inativo";
    }
    const schedule = (data.webhooks && data.webhooks.retry_schedule_seconds) || [];
    $("#retry-policy").textContent = schedule.map(secondsLabel).join(" · ") || "—";
    if (data.webhooks) {
      const retries = Math.max(0, (data.webhooks.max_attempts || 1) - 1);
      $("#retry-meta").textContent = `${retries} retries · timeout ${data.webhooks.timeout_seconds}s · jitter`;
    }
    if (!state.demoMode) {
      $("#env-badge").innerHTML = "<span></span> live";
      $("#fail-webhook-btn").hidden = true;
      const receiptPanel = $("#receipt-list")?.closest(".subpanel");
      if (receiptPanel) receiptPanel.hidden = true;
    }
    updateInjectButton();
    if (!state.apiKey && data.demo_api_key) {
      state.apiKey = data.demo_api_key;
      $("#api-key").value = data.demo_api_key;
    }
  } catch (error) {
    state.demoMode = false;
    $("#env-badge").innerHTML = "<span></span> live";
    $("#fail-webhook-btn").hidden = true;
  }
}

function updateInjectButton() {
  const button = $("#fail-webhook-btn");
  const reset = $("#reset-failures-btn");
  if (!button || !reset) return;
  if (state.failNext > 0) {
    button.innerHTML = `${SVG.alert} ${state.failNext} falha${state.failNext > 1 ? "s" : ""} armada${state.failNext > 1 ? "s" : ""}`;
    reset.hidden = false;
  } else {
    button.innerHTML = `${SVG.alert} Injetar 2 falhas`;
    reset.hidden = true;
  }
}

async function connect({ silent = false } = {}) {
  const key = $("#api-key").value.trim() || state.apiKey;
  if (!key) {
    toast("Informe a API key", "Copie a chave demo do README ou do arquivo .env.", "error");
    return false;
  }
  state.apiKey = key;
  const dot = $("#conn-dot");
  dot.dataset.state = "loading";
  dot.title = "Conectando…";
  try {
    const [{ data: me }, { data: endpoints }] = await Promise.all([
      api("/v1/me"),
      api("/v1/webhook-endpoints"),
    ]);
    state.merchant = me;
    state.endpoints = endpoints.data || [];
    state.connected = true;
    state.authErrorShown = false;
    localStorage.setItem(KEY_STORAGE, key);
    dot.dataset.state = "on";
    dot.title = `Conectado como ${me.name}`;
    const endpoint = state.endpoints[0];
    if (endpoint) {
      $("#endpoint-url").textContent = endpoint.url;
      $("#endpoint-url").title = endpoint.url;
      $("#endpoint-status").textContent = endpoint.active ? "ativo · receiver local simulado" : "inativo";
      $("#endpoint-secret").textContent = endpoint.secret || "—";
    }
    if (!silent) toast("Conectado", `Merchant ${me.name} autenticado.`, "success", 3000);
    startPolling();
    await refresh(true);
    return true;
  } catch (error) {
    state.connected = false;
    dot.dataset.state = "error";
    dot.title = "Falha na autenticação";
    if (!silent || error.status === 401) {
      toast("Não foi possível conectar", errorMessage(error), "error");
    }
    return false;
  }
}

function startPolling() {
  if (state.pollTimer) window.clearTimeout(state.pollTimer);
  const loop = async () => {
    if (!state.connected) return;
    if (!document.hidden && !state.pollBusy) {
      state.pollBusy = true;
      try { await refresh(true); } finally { state.pollBusy = false; }
    }
    state.pollTimer = window.setTimeout(loop, POLL_INTERVAL_MS);
  };
  state.pollTimer = window.setTimeout(loop, POLL_INTERVAL_MS);
}

async function refresh(quiet = false) {
  if (!state.connected) return;
  const deliveryFilter = state.deliveryFilter;
  const deliveryQuery = deliveryFilter ? `?status=${encodeURIComponent(deliveryFilter)}&limit=40` : "?limit=40";
  const requests = [
    api("/v1/payments?limit=50"),
    api("/v1/metrics"),
    api(`/v1/webhook-deliveries${deliveryQuery}`),
    state.demoMode ? api("/demo/webhook-receiver/receipts?limit=20") : Promise.resolve({ data: { data: [] } }),
    state.demoMode ? api("/demo/config") : Promise.resolve({ data: null }),
  ];
  try {
    const [payments, metrics, deliveries, receipts, demo] = await Promise.all(requests);
    state.payments = payments.data.data || [];
    state.metrics = metrics.data;
    state.deliveries = deliveries.data.data || [];
    state.receipts = receipts.data.data || [];
    if (demo.data) {
      state.failNext = demo.data.fail_next_webhooks || 0;
      updateInjectButton();
    }
    renderStats();
    renderPayments();
    renderWebhooks();
    if (!$("#payment-modal").hidden) await renderPaymentModal(true);
  } catch (error) {
    if (error.status === 401) {
      state.connected = false;
      $("#conn-dot").dataset.state = "error";
      if (!state.authErrorShown) {
        state.authErrorShown = true;
        toast("Sessão inválida", "A API key foi recusada. Conecte novamente.", "error");
      }
    } else if (!quiet) {
      toast("Falha ao atualizar", errorMessage(error), "error");
    }
  }
}

/* ------------------------------------------------------------------ */
/* Rendering — stats & payments                                        */
/* ------------------------------------------------------------------ */
function renderStats() {
  const metrics = state.metrics;
  if (!metrics) return;
  const byStatus = metrics.payments.by_status;
  const succeededCount = byStatus.succeeded || 0;
  const pendingCount = (byStatus.pending || 0) + (byStatus.processing || 0);
  const deliveries = metrics.webhooks.deliveries_by_status;
  const volume = state.payments
    .filter((payment) => payment.status === "succeeded")
    .reduce((sum, payment) => sum + payment.amount, 0);

  $("#stat-succeeded").textContent = String(succeededCount);
  $("#stat-volume").textContent = `${money(volume)} nos últimos 50`;
  $("#stat-pending").textContent = String(pendingCount);
  $("#stat-processing").textContent = `${byStatus.processing || 0} processando`;
  $("#stat-queue").textContent = String(metrics.queue.pending || 0);
  $("#stat-webhooks").textContent = String((deliveries.delivered || 0) + (deliveries.pending || 0) + (deliveries.failed || 0));
  $("#stat-webhook-foot").textContent = `${deliveries.failed || 0} na DLQ`;

  $("#wh-delivered").textContent = String(deliveries.delivered || 0);
  $("#wh-pending").textContent = String(deliveries.pending || 0);
  $("#wh-failed").textContent = String(deliveries.failed || 0);
  $("#wh-attempts").textContent = `${metrics.webhooks.attempts_total || 0} tentativas registradas`;
}

function paymentMatchesFilter(payment) {
  if (state.filter === "all") return true;
  if (state.filter === "pending") return payment.status === "pending" || payment.status === "processing";
  return payment.status === state.filter;
}

function renderPayments() {
  const body = $("#payments-body");
  const filtered = state.payments.filter(paymentMatchesFilter);
  $("#payments-count").textContent = `${state.payments.length} pagamento${state.payments.length === 1 ? "" : "s"}`;

  if (!filtered.length) {
    body.innerHTML = `
      <div class="empty-state">
        <div class="empty-icon">${SVG.bell}</div>
        <strong>${state.payments.length ? "Nenhum pagamento neste filtro" : "Nenhum pagamento ainda"}</strong>
        <span>${state.payments.length ? "Troque o filtro para ver outros status." : "Crie a primeira cobrança para ver o fluxo completo."}</span>
      </div>`;
    return;
  }

  body.innerHTML = filtered.map((payment) => {
    const isPix = payment.method === "pix";
    const label = payment.description || (payment.customer && payment.customer.name) || "Cobrança";
    return `
      <div class="payment-row" role="button" tabindex="0" data-payment-id="${escapeHtml(payment.id)}" aria-label="Abrir pagamento ${escapeHtml(payment.id)}">
        <span class="pay-main">
          <span class="pay-icon ${isPix ? "" : "card-icon"}">${methodIcon(payment.method)}</span>
          <span class="pay-text">
            <span class="pay-desc">${escapeHtml(label)}</span>
            <span class="pay-id">${escapeHtml(payment.id)}</span>
          </span>
        </span>
        <span class="method-pill"><b>${escapeHtml(METHOD_LABEL[payment.method] || payment.method)}</b></span>
        <span class="pay-amount">${escapeHtml(money(payment.amount))}</span>
        <span>${badge(payment.status)}</span>
        <span class="pay-time" title="${escapeHtml(formatDateTime(payment.created_at))}">${escapeHtml(relative(payment.created_at))}</span>
        <span class="row-chevron">${SVG.chevron}</span>
      </div>`;
  }).join("");
}

/* ------------------------------------------------------------------ */
/* Rendering — webhook deliveries & receipts                           */
/* ------------------------------------------------------------------ */
function renderWebhooks() {
  const deliveryList = $("#delivery-list");
  const filtered = state.deliveries;
  if (!filtered.length) {
    deliveryList.innerHTML = `
      <div class="empty-state compact">
        <strong>Sem entregas</strong>
        <span>Crie um pagamento PIX ou cartão para gerar eventos.</span>
      </div>`;
  } else {
    deliveryList.innerHTML = filtered.map((delivery) => {
      const when = delivery.status === "pending"
        ? (delivery.next_attempt_at ? `retry ${relative(delivery.next_attempt_at)}` : "na fila")
        : delivery.status === "delivered"
          ? `entregue ${relative(delivery.delivered_at)}`
          : `DLQ · ${delivery.attempts} tentativas`;
      const canRetry = delivery.status === "failed";
      return `
        <div class="delivery-row">
          <div class="delivery-main">
            <span class="delivery-type">${escapeHtml(delivery.event_type)}</span>
            <span class="delivery-id">${escapeHtml(delivery.event_id)}</span>
          </div>
          <div>${badge(delivery.status)}</div>
          <div class="delivery-attempts">
            ${delivery.attempts} tentativa${delivery.attempts === 1 ? "" : "s"}
            <span class="delivery-next">${escapeHtml(delivery.last_response_code ? `HTTP ${delivery.last_response_code}` : when)}</span>
          </div>
          <div class="delivery-actions">
            <button class="icon-btn" type="button" data-delivery-id="${escapeHtml(delivery.id)}" title="Ver payload e tentativas" aria-label="Ver entrega">${SVG.eye}</button>
            ${canRetry ? `<button class="icon-btn" type="button" data-retry-delivery="${escapeHtml(delivery.id)}" title="Reenviar da DLQ" aria-label="Reenviar">${SVG.retry}</button>` : ""}
          </div>
        </div>`;
    }).join("");
  }

  const receiptList = $("#receipt-list");
  $("#receipts-count").textContent = String(state.receipts.length);
  if (!state.receipts.length) {
    receiptList.innerHTML = `
      <div class="empty-state compact">
        <strong>Nada recebido</strong>
        <span>O receiver local valida HMAC e deduplica por X-Event-Id.</span>
      </div>`;
  } else {
    receiptList.innerHTML = state.receipts.map((receipt) => {
      const eventType = receipt.event && receipt.event.type ? receipt.event.type : "evento";
      return `
        <div class="receipt-row">
          <span class="receipt-icon">${receipt.signature_valid ? SVG.check : SVG.alert}</span>
          <span class="receipt-main">
            <span class="receipt-event">${escapeHtml(eventType)} · ${escapeHtml(receipt.event_id)}</span>
            <span class="receipt-time">${escapeHtml(formatClock(receipt.received_at))} · assinatura ${receipt.signature_valid ? "válida" : "inválida"}</span>
          </span>
          <span class="receipt-badge">${escapeHtml(METHOD_LABEL[(receipt.event && receipt.event.data && receipt.event.data.payment && receipt.event.data.payment.method) || ""] || "")}</span>
        </div>`;
    }).join("");
  }
}

/* ------------------------------------------------------------------ */
/* Payment detail modal                                                */
/* ------------------------------------------------------------------ */
async function openPayment(paymentId) {
  state.selectedPaymentId = paymentId;
  const modal = $("#payment-modal");
  modal.hidden = false;
  document.body.classList.add("modal-open");
  $("#payment-modal-body").innerHTML = '<div class="loading-block"><span class="spinner dark"></span> Carregando pagamento…</div>';
  $("#payment-modal-foot").innerHTML = "";
  await renderPaymentModal(false);
}

async function renderPaymentModal(quiet = false) {
  const paymentId = state.selectedPaymentId;
  if (!paymentId || $("#payment-modal").hidden || state.modalBusy) return;
  state.modalBusy = true;
  try {
    const [{ data: payment }, { data: events }, { data: deliveries }] = await Promise.all([
      api(`/v1/payments/${encodeURIComponent(paymentId)}`),
      api(`/v1/payments/${encodeURIComponent(paymentId)}/events`),
      api(`/v1/webhook-deliveries?payment_id=${encodeURIComponent(paymentId)}&limit=20`),
    ]);
    renderPaymentDetail(payment, events.data || [], deliveries.data || []);
  } catch (error) {
    if (!quiet) {
      $("#payment-modal-body").innerHTML = `<div class="empty-state compact"><strong>Não foi possível carregar</strong><span>${escapeHtml(errorMessage(error))}</span></div>`;
    }
  } finally {
    state.modalBusy = false;
  }
}

function renderPaymentDetail(payment, events, deliveries) {
  if (!payment || payment.id !== state.selectedPaymentId) return;
  const isPix = payment.method === "pix";
  const canSimulate = isPix && (payment.status === "pending" || payment.status === "processing") && state.demoMode;
  const pix = payment.pix || {};
  const card = payment.card || {};

  $("#payment-modal-kicker").textContent = `Pagamento · ${METHOD_LABEL[payment.method] || payment.method}`;
  $("#payment-modal-title").textContent = payment.description || "Cobrança";

  const cells = [
    ["Método", METHOD_LABEL[payment.method] || payment.method],
    ["Moeda", payment.currency],
    ["Cliente", payment.customer ? payment.customer.name : "—"],
    ["E-mail", payment.customer ? payment.customer.email : "—"],
    ["Criado em", formatDateTime(payment.created_at)],
    ["Atualizado em", formatDateTime(payment.updated_at)],
  ];
  if (!isPix) {
    cells.push(["Cartão", card.brand ? `${card.brand} ···· ${card.last4}` : "—"]);
    cells.push(["Autorização", payment.authorization_code || "—"]);
  } else {
    cells.push(["TXID", pix.txid || "—"]);
    cells.push(["Expira em", pix.expires_at ? `${formatDateTime(pix.expires_at)} (${pixCountdown(pix.expires_at)})` : "—"]);
  }
  if (payment.failure_reason) cells.push(["Motivo da falha", payment.failure_reason]);

  let pixBlock = "";
  if (isPix && pix.qr_code) {
    const image = pix.qr_code_image
      ? `<img src="${pix.qr_code_image}" alt="QR code PIX simulado" />`
      : `<span class="qr-fallback">QR visual indisponível.<br />Use o copia-e-cola.</span>`;
    pixBlock = `
      <div class="pix-box">
        <div class="pix-qr">${image}</div>
        <div class="pix-info">
          <span class="pix-copy-label">PIX copia-e-cola · EMV simulado</span>
          <div class="pix-code" id="pix-code">${escapeHtml(pix.qr_code)}</div>
          <div class="pix-meta">
            <span><b>TXID</b> ${escapeHtml(pix.txid || "—")}</span>
            <span><b>Validade</b> ${escapeHtml(pixCountdown(pix.expires_at))}</span>
          </div>
          <div class="modal-actions">
            <button class="btn btn-small btn-ghost" type="button" data-copy-pix>${SVG.copy} Copiar código</button>
            ${canSimulate && payment.status === "pending" ? `<button class="btn btn-small btn-primary" type="button" data-simulate-payment="${escapeHtml(payment.id)}">Simular PIX pago</button>` : ""}
            ${payment.status === "processing" ? `<span class="hint">worker confirmando…</span>` : ""}
          </div>
        </div>
      </div>`;
  }

  const timeline = events.length ? events.map((event) => {
    const cls = event.type === "payment.succeeded" ? "done" : event.type === "payment.failed" ? "bad" : "current";
    const reason = event.payload && event.payload.reason ? `<em>${escapeHtml(event.payload.reason)}</em>` : "";
    return `
      <div class="timeline-item ${cls}">
        <span class="timeline-dot"></span>
        <div class="timeline-content">
          <strong>${escapeHtml(EVENT_LABEL[event.type] || event.type)}</strong>
          <span>${escapeHtml(formatDateTime(event.created_at))} · ${escapeHtml(event.id)}</span>
          ${reason}
        </div>
      </div>`;
  }).join("") : '<div class="hint">Sem eventos registrados.</div>';

  const deliveryList = deliveries.length ? deliveries.map((delivery) => `
    <div class="delivery-mini">
      <div class="delivery-main">
        <span class="delivery-type">${escapeHtml(delivery.event_type)}</span>
        <code>${escapeHtml(delivery.id)}</code>
      </div>
      ${badge(delivery.status)}
      <button class="icon-btn icon-btn-sm" type="button" data-delivery-id="${escapeHtml(delivery.id)}" title="Ver tentativas">${SVG.eye}</button>
    </div>`).join("") : '<div class="hint">Nenhuma entrega ainda.</div>';

  const metadataBlock = payment.metadata && Object.keys(payment.metadata).length
    ? `<div class="section-label"><h3>Metadata</h3></div><pre class="payload-block">${escapeHtml(JSON.stringify(payment.metadata, null, 2))}</pre>`
    : "";

  $("#payment-modal-body").innerHTML = `
    <div class="detail-hero">
      <div>
        <div class="detail-amount">${escapeHtml(money(payment.amount))}</div>
        <div class="detail-sub">${escapeHtml(payment.id)}</div>
      </div>
      <div>${badge(payment.status)}</div>
    </div>
    ${pixBlock}
    <div class="detail-grid">
      ${cells.map(([label, value]) => `<div class="detail-cell"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`).join("")}
    </div>
    <div class="section-label"><h3>Histórico imutável</h3><span class="hint">${events.length} evento${events.length === 1 ? "" : "s"}</span></div>
    <div class="timeline">${timeline}</div>
    <div class="section-label"><h3>Entregas de webhook</h3><span class="hint">${deliveries.length}</span></div>
    ${deliveryList}
    ${metadataBlock}
  `;

  $("#payment-modal-foot").innerHTML = `
    <span class="foot-note">${escapeHtml(METHOD_LABEL[payment.method] || payment.method)} · idempotência por key no servidor</span>
    <div class="modal-actions">
      <button class="btn btn-small btn-ghost" type="button" data-copy-id data-value="${escapeHtml(payment.id)}">${SVG.copy} Copiar ID</button>
      <button class="btn btn-small" type="button" data-close-payment>Fechar</button>
    </div>`;

  if (payment.status !== "processing" && payment.status !== "pending" && isPix) {
    // Terminal state: no simulate button, timeline already explains it.
  }
}

function pixCountdown(expiresAt) {
  if (!expiresAt) return "—";
  const diff = new Date(expiresAt).getTime() - Date.now();
  if (Number.isNaN(diff)) return "—";
  if (diff <= 0) return "expirado";
  const minutes = Math.floor(diff / 60000);
  const seconds = Math.floor((diff % 60000) / 1000);
  if (minutes >= 1) return `expira em ${minutes}min`;
  return `expira em ${seconds}s`;
}

async function simulatePayment(paymentId, trigger) {
  if (trigger) {
    trigger.disabled = true;
    trigger.classList.add("is-loading");
  }
  try {
    await api(`/v1/payments/${encodeURIComponent(paymentId)}/simulate-payment`, {
      method: "POST",
      body: { result: "paid" },
    });
    toast("PIX confirmado", "O worker foi acionado e o webhook payment.succeeded será entregue.", "success");
    await refresh(true);
    if (!$("#payment-modal").hidden) await renderPaymentModal(true);
  } catch (error) {
    toast("Falha ao simular", errorMessage(error), "error");
  } finally {
    if (trigger) {
      trigger.disabled = false;
      trigger.classList.remove("is-loading");
    }
  }
}

/* ------------------------------------------------------------------ */
/* Delivery detail modal                                               */
/* ------------------------------------------------------------------ */
async function openDelivery(deliveryId) {
  const modal = $("#delivery-modal");
  modal.hidden = false;
  document.body.classList.add("modal-open");
  $("#delivery-modal-body").innerHTML = '<div class="loading-block"><span class="spinner dark"></span> Carregando entrega…</div>';
  try {
    const { data: delivery } = await api(`/v1/webhook-deliveries/${encodeURIComponent(deliveryId)}`);
    renderDelivery(delivery);
  } catch (error) {
    $("#delivery-modal-body").innerHTML = `<div class="empty-state compact"><strong>Não foi possível carregar</strong><span>${escapeHtml(errorMessage(error))}</span></div>`;
  }
}

function renderDelivery(delivery) {
  $("#delivery-modal-title").textContent = delivery.event_type;
  const attempts = delivery.attempt_log || [];
  const attemptRows = attempts.length ? attempts.map((attempt) => `
    <tr>
      <td>#${attempt.attempt_no}</td>
      <td class="${attempt.response_code && attempt.response_code < 300 ? "ok" : "err"}">${attempt.response_code ? `HTTP ${attempt.response_code}` : "sem resposta"}</td>
      <td>${attempt.error ? escapeHtml(attempt.error) : "—"}</td>
      <td>${attempt.duration_ms}ms</td>
      <td>${escapeHtml(formatDateTime(attempt.created_at))}</td>
    </tr>`).join("") : '<tr><td colspan="5">Nenhuma tentativa registrada.</td></tr>';

  const reason = delivery.event && delivery.event.data && delivery.event.data.payment ? delivery.event.data.payment.failure_reason : null;
  $("#delivery-modal-body").innerHTML = `
    <div class="detail-grid">
      <div class="detail-cell"><span>Status</span><strong>${badge(delivery.status)}</strong></div>
      <div class="detail-cell"><span>Tentativas</span><strong>${delivery.attempts}</strong></div>
      <div class="detail-cell"><span>Event ID</span><code>${escapeHtml(delivery.event_id)}</code></div>
      <div class="detail-cell"><span>Pagamento</span><code>${escapeHtml(delivery.payment_id)}</code></div>
      <div class="detail-cell"><span>Endpoint</span><code>${escapeHtml(delivery.endpoint_url || "—")}</code></div>
      <div class="detail-cell"><span>Último HTTP</span><strong>${delivery.last_response_code || "—"}</strong></div>
      <div class="detail-cell"><span>Próxima tentativa</span><strong>${delivery.next_attempt_at ? formatDateTime(delivery.next_attempt_at) : "—"}</strong></div>
      <div class="detail-cell"><span>Último erro</span><strong>${escapeHtml(delivery.last_error || "—")}</strong></div>
    </div>
    ${reason ? `<div class="section-label"><h3>Motivo</h3></div><pre class="payload-block">${escapeHtml(reason)}</pre>` : ""}
    <div class="section-label"><h3>Log de tentativas</h3><span class="hint">${attempts.length} registro${attempts.length === 1 ? "" : "s"}</span></div>
    <table class="attempt-table">
      <thead><tr><th>#</th><th>Resultado</th><th>Erro</th><th>Duração</th><th>Quando</th></tr></thead>
      <tbody>${attemptRows}</tbody>
    </table>
    <div class="section-label"><h3>Payload assinado</h3><span class="hint">HMAC-SHA256</span></div>
    <pre class="payload-block">${escapeHtml(JSON.stringify(delivery.event, null, 2))}</pre>
    <div class="modal-actions" style="margin-top: 14px;">
      ${delivery.status === "failed" ? `<button class="btn btn-small btn-primary" type="button" data-retry-delivery="${escapeHtml(delivery.id)}">${SVG.retry} Reenviar da DLQ</button>` : ""}
      <button class="btn btn-small btn-ghost" type="button" data-close-delivery>Fechar</button>
    </div>`;
}

async function retryDelivery(deliveryId, trigger) {
  if (trigger) trigger.disabled = true;
  try {
    await api(`/v1/webhook-deliveries/${encodeURIComponent(deliveryId)}/retry`, { method: "POST" });
    toast("Entrega reenfileirada", "O dispatcher tentará novamente em instantes.", "success");
    $("#delivery-modal").hidden = true;
    document.body.classList.remove("modal-open");
    await refresh(true);
  } catch (error) {
    toast("Falha ao reenviar", errorMessage(error), "error");
  } finally {
    if (trigger) trigger.disabled = false;
  }
}

/* ------------------------------------------------------------------ */
/* Form                                                               */
/* ------------------------------------------------------------------ */
function setMethod(method) {
  state.method = method;
  $$(".seg-item").forEach((button) => {
    const active = button.dataset.method === method;
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-selected", String(active));
  });
  const cardFields = $("#card-fields");
  const isCard = method !== "pix";
  cardFields.hidden = !isCard;
  $("#form-foot").innerHTML = isCard
    ? `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><path d="M12 3 4 6.5v5.2c0 4.6 3.4 8 8 9.3 4.6-1.3 8-4.7 8-9.3V6.5L12 3Z" stroke-linejoin="round"/><path d="m9 12 2 2 4-4" stroke-linecap="round" stroke-linejoin="round"/></svg><span>PAN/CVV são validados e descartados. Só bandeira + últimos 4 vão para o banco.</span>`
    : `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><path d="M12 3 4 6.5v5.2c0 4.6 3.4 8 8 9.3 4.6-1.3 8-4.7 8-9.3V6.5L12 3Z" stroke-linejoin="round"/><path d="m9 12 2 2 4-4" stroke-linecap="round" stroke-linejoin="round"/></svg><span>QR e txid gerados na hora. O status fica pending até a confirmação simulada.</span>`;
}

function fillTestCard(number) {
  setMethod("credit_card");
  $("#card-number").value = number.replace(/(\d{4})(?=\d)/g, "$1 ");
  $("#card-holder").value = "ANA SILVA";
  $("#exp-month").value = "12";
  $("#exp-year").value = "2030";
  $("#card-cvv").value = "123";
  $("#card-number").focus();
  toast("Cartão de teste", "Dados preenchidos. Clique em Criar cobrança.", "info", 2600);
}

async function submitPayment(event) {
  event.preventDefault();
  if (!state.connected) {
    const ok = await connect({ silent: false });
    if (!ok) return;
  }

  const button = $("#submit-payment");
  const amount = centsFromInput($("#amount").value);
  if (!Number.isFinite(amount) || amount <= 0) {
    toast("Valor inválido", "Informe um valor positivo, por exemplo 129,90.", "error");
    $("#amount").focus();
    return;
  }
  if (amount > 100000000) {
    toast("Valor acima do limite", "O máximo aceito é R$ 1.000.000,00.", "error");
    return;
  }

  const customerName = $("#customer-name").value.trim();
  const customerEmail = $("#customer-email").value.trim();
  if ((customerName && !customerEmail) || (!customerName && customerEmail)) {
    toast("Cliente incompleto", "Informe nome e e-mail, ou deixe ambos vazios.", "error");
    return;
  }

  const payload = {
    amount,
    currency: "BRL",
    method: state.method,
  };
  const description = $("#description").value.trim();
  if (description) payload.description = description;
  if (customerName && customerEmail) payload.customer = { name: customerName, email: customerEmail };

  const metadataRaw = $("#metadata").value.trim();
  if (metadataRaw) {
    try {
      const metadata = JSON.parse(metadataRaw);
      if (!metadata || typeof metadata !== "object" || Array.isArray(metadata)) throw new Error("metadata deve ser um objeto");
      payload.metadata = metadata;
    } catch (error) {
      toast("Metadata inválida", error.message, "error");
      return;
    }
  }

  if (state.method !== "pix") {
    const number = $("#card-number").value.replace(/[\s-]/g, "");
    const holder = $("#card-holder").value.trim();
    const expMonth = Number($("#exp-month").value);
    const expYear = Number($("#exp-year").value);
    const cvv = $("#card-cvv").value.trim();
    if (!number || !holder || !expMonth || !expYear || !cvv) {
      toast("Cartão incompleto", "Preencha número, titular, validade e CVV.", "error");
      return;
    }
    payload.card = {
      number,
      holder,
      exp_month: expMonth,
      exp_year: expYear,
      cvv,
    };
  }

  const idempotencyKey = makeIdempotencyKey();
  button.disabled = true;
  button.classList.add("is-loading");
  try {
    const { data, response } = await api("/v1/payments", {
      method: "POST",
      headers: { "Idempotency-Key": idempotencyKey },
      body: payload,
    });
    const replay = response.headers.get("Idempotent-Replay") === "true";
    toast(
      replay ? "Resposta idempotente" : "Cobrança criada",
      replay ? "Esta resposta veio do cache idempotente." : `${data.id} · ${money(data.amount)} · ${STATUS_LABEL[data.status] || data.status}`,
      "success",
      3600,
    );
    $("#card-number").value = "";
    $("#card-cvv").value = "";
    await refresh(true);
    await openPayment(data.id);
  } catch (error) {
    toast("Não foi possível criar", errorMessage(error), "error");
  } finally {
    button.disabled = false;
    button.classList.remove("is-loading");
  }
}

/* ------------------------------------------------------------------ */
/* Bindings                                                           */
/* ------------------------------------------------------------------ */
function bindEvents() {
  $("#connect-btn").addEventListener("click", () => connect({ silent: false }));
  $("#api-key").addEventListener("keydown", (event) => {
    if (event.key === "Enter") connect({ silent: false });
  });
  $("#refresh-btn").addEventListener("click", async () => {
    if (!state.connected) {
      await connect({ silent: false });
      return;
    }
    await refresh(false);
    toast("Atualizado", "Dados recarregados.", "info", 1800);
  });
  $("#hero-new-payment").addEventListener("click", () => {
    $("#amount").focus();
    $("#new-payment-title").scrollIntoView({ behavior: "smooth", block: "center" });
  });

  $$(".seg-item").forEach((button) => {
    button.addEventListener("click", () => setMethod(button.dataset.method));
  });
  $$("[data-test-card]").forEach((button) => {
    button.addEventListener("click", () => fillTestCard(button.dataset.testCard));
  });

  $("#amount").addEventListener("input", () => {
    const cents = centsFromInput($("#amount").value);
    $("#amount-cents").textContent = Number.isFinite(cents)
      ? `= ${cents} centavos${cents < 100 ? " · mínimo R$ 0,01" : ""}`
      : "valor inválido";
  });
  $("#payment-form").addEventListener("submit", submitPayment);

  $$(".filter").forEach((button) => {
    button.addEventListener("click", () => {
      state.filter = button.dataset.filter;
      $$(".filter").forEach((candidate) => {
        const active = candidate === button;
        candidate.classList.toggle("is-active", active);
        candidate.setAttribute("aria-selected", String(active));
      });
      renderPayments();
    });
  });

  const paymentsBody = $("#payments-body");
  paymentsBody.addEventListener("click", (event) => {
    const row = event.target.closest("[data-payment-id]");
    if (row) openPayment(row.dataset.paymentId);
  });
  paymentsBody.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" && event.key !== " ") return;
    const row = event.target.closest("[data-payment-id]");
    if (row) {
      event.preventDefault();
      openPayment(row.dataset.paymentId);
    }
  });

  $("#delivery-filter").addEventListener("change", async (event) => {
    state.deliveryFilter = event.target.value;
    await refresh(true);
  });

  $("#delivery-list").addEventListener("click", (event) => {
    const retry = event.target.closest("[data-retry-delivery]");
    if (retry) {
      retryDelivery(retry.dataset.retryDelivery, retry);
      return;
    }
    const view = event.target.closest("[data-delivery-id]");
    if (view) openDelivery(view.dataset.deliveryId);
  });

  const modalBody = $("#payment-modal-body");
  modalBody.addEventListener("click", (event) => {
    const simulate = event.target.closest("[data-simulate-payment]");
    if (simulate) {
      simulatePayment(simulate.dataset.simulatePayment, simulate);
      return;
    }
    const delivery = event.target.closest("[data-delivery-id]");
    if (delivery) {
      openDelivery(delivery.dataset.deliveryId);
      return;
    }
    const copyId = event.target.closest("[data-copy-id]");
    if (copyId) copyText(copyId.dataset.value, "ID copiado");
    if (event.target.closest("[data-copy-pix]")) {
      const code = $("#pix-code");
      if (code) copyText(code.textContent, "Copia-e-cola copiado");
    }
  });

  $$("[data-close-payment]").forEach((node) => node.addEventListener("click", closePaymentModal));
  $$("[data-close-delivery]").forEach((node) => node.addEventListener("click", closeDeliveryModal));
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    if (!$("#delivery-modal").hidden) closeDeliveryModal();
    else if (!$("#payment-modal").hidden) closePaymentModal();
  });

  $("#delivery-modal-body").addEventListener("click", (event) => {
    const retry = event.target.closest("[data-retry-delivery]");
    if (retry) retryDelivery(retry.dataset.retryDelivery, retry);
    if (event.target.closest("[data-close-delivery]")) closeDeliveryModal();
  });

  $("#fail-webhook-btn").addEventListener("click", async () => {
    try {
      const { data } = await api("/demo/webhook-failures", { method: "POST", body: { count: 2 } });
      state.failNext = data.fail_next_webhooks;
      updateInjectButton();
      toast("Falhas armadas", "As próximas 2 entregas responderão HTTP 503; acompanhe retries na lista.", "info", 5200);
    } catch (error) {
      toast("Falha ao injetar", errorMessage(error), "error");
    }
  });
  $("#reset-failures-btn").addEventListener("click", async () => {
    try {
      await api("/demo/reset-failures", { method: "POST" });
      state.failNext = 0;
      updateInjectButton();
      toast("Falhas limpas", "Novas entregas não serão sabotadas.", "success", 2600);
    } catch (error) {
      toast("Falha ao limpar", errorMessage(error), "error");
    }
  });

  $("#copy-secret-btn").addEventListener("click", () => {
    const secret = $("#endpoint-secret").textContent;
    if (secret && !secret.includes("•")) copyText(secret, "Signing secret copiado");
    else toast("Secret indisponível", "Conecte-se para carregar o endpoint.", "error");
  });
}

function copyText(text, successMessage) {
  if (!text) return;
  const done = () => toast("Copiado", successMessage, "success", 1800);
  if (navigator.clipboard && window.isSecureContext) {
    navigator.clipboard.writeText(text).then(done).catch(() => fallbackCopy(text, done));
  } else {
    fallbackCopy(text, done);
  }
}

function fallbackCopy(text, done) {
  const area = document.createElement("textarea");
  area.value = text;
  area.style.position = "fixed";
  area.style.opacity = "0";
  document.body.appendChild(area);
  area.select();
  try { document.execCommand("copy"); done(); } catch { toast("Não foi possível copiar", "Selecione o texto manualmente.", "error"); }
  area.remove();
}

function closePaymentModal() {
  $("#payment-modal").hidden = true;
  state.selectedPaymentId = null;
  if ($("#delivery-modal").hidden) document.body.classList.remove("modal-open");
}

function closeDeliveryModal() {
  $("#delivery-modal").hidden = true;
  if ($("#payment-modal").hidden) document.body.classList.remove("modal-open");
}

/* ------------------------------------------------------------------ */
/* Boot                                                               */
/* ------------------------------------------------------------------ */
async function init() {
  bindEvents();
  setMethod("pix");
  if (state.apiKey) $("#api-key").value = state.apiKey;
  const cents = centsFromInput($("#amount").value);
  if (Number.isFinite(cents)) $("#amount-cents").textContent = `= ${cents} centavos`;
  await loadDemoConfig();
  if (!state.apiKey) {
    $("#conn-dot").dataset.state = "off";
    $("#stat-succeeded").textContent = "—";
    $("#stat-pending").textContent = "—";
    $("#stat-queue").textContent = "—";
    $("#stat-webhooks").textContent = "—";
    return;
  }
  await connect({ silent: true });
}

document.addEventListener("DOMContentLoaded", init);
