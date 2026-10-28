/* FluxPay console — WebSocket /ws/events client.
 *
 * Design (RF-14 / CA-12):
 *  - WS is ONLY a notification channel; REST /v1/events is the source of truth;
 *  - every message carries `id` (event id); the client keeps it as the cursor;
 *  - on reconnect the cursor is passed via `?after=` so nothing is lost while
 *    offline, and the client then reconciles missed events with GET /v1/events
 *    (WS with the same cursor = identical state);
 *  - auto-reconnect with capped exponential backoff (0.5s → 8s);
 *  - broadcast `fluxpay:event` CustomEvents so every page reacts live.
 */
"use strict";

(function () {
  const storageKey = "fluxpay_ws_cursor";
  const eventsClient = {
    socket: null,
    apiKey: "",
    cursor: localStorage.getItem(storageKey) || "",
    timer: null,
    attempts: 0,
    closed: false,
    listeners: [],
  };

  function rememberCursor(eventId) {
    if (!eventId) return;
    eventsClient.cursor = eventId;
    try { localStorage.setItem(storageKey, eventId); } catch { /* private mode */ }
  }

  function emit(event) {
    rememberCursor(event.id);
    for (const listener of eventsClient.listeners) listener(event);
    document.dispatchEvent(new CustomEvent("fluxpay:event", { detail: event }));
  }

  function nextUrl() {
    const host = window.location.host;
    let url = `ws://${host}/ws/events`;
    const params = [];
    if (eventsClient.apiKey) params.push(`token=${encodeURIComponent(eventsClient.apiKey)}`);
    if (eventsClient.cursor) params.push(`after=${encodeURIComponent(eventsClient.cursor)}`);
    if (params.length) url += `?${params.join("&")}`;
    return url;
  }

  function scheduleReconnect() {
    if (eventsClient.closed || eventsClient.timer) return;
    const delay = Math.min(8000, 500 * Math.pow(2, eventsClient.attempts));
    eventsClient.attempts += 1;
    eventsClient.timer = window.setTimeout(() => {
      eventsClient.timer = null;
      connect(eventsClient.apiKey, { quiet: true });
    }, delay);
  }

  async function reconcile() {
    /* REST is the source of truth: fetch everything after our cursor. */
    if (!eventsClient.cursor) return;
    try {
      const headers = { Accept: "application/json" };
      if (eventsClient.apiKey) headers.Authorization = `Bearer ${eventsClient.apiKey}`;
      const response = await fetch(
        `/v1/events?after=${encodeURIComponent(eventsClient.cursor)}&order=asc&limit=100`,
        { headers }
      );
      if (!response.ok) return;
      const payload = await response.json();
      for (const event of payload.data || []) {
        if (event.id && event.id !== eventsClient.cursor) emit(event);
      }
    } catch {
      /* offline: the next poll/reconnect will retry */
    }
  }

  function handleMessage(raw) {
    try {
      const message = JSON.parse(raw.data);
      if (!message || !message.id) return; // ignore heartbeats (server.ping)
      emit(message);
    } catch {
      /* malformed frame: ignore, REST keeps state coherent */
    }
  }

  function connect(apiKey, { quiet = false } = {}) {
    if (apiKey) eventsClient.apiKey = apiKey;
    eventsClient.closed = false;
    if (!window.WebSocket || !eventsClient.apiKey) return;

    if (eventsClient.socket) {
      try { eventsClient.socket.close(); } catch { /* already closing */ }
      eventsClient.socket = null;
    }
    try {
      const socket = new WebSocket(nextUrl());
      eventsClient.socket = socket;
      socket.onopen = () => {
        eventsClient.attempts = 0;
        document.dispatchEvent(new CustomEvent("fluxpay:ws", { detail: { status: "open" } }));
        reconcile();
      };
      socket.onmessage = handleMessage;
      socket.onclose = (event) => {
        document.dispatchEvent(
          new CustomEvent("fluxpay:ws", {
            detail: { status: "close", code: event.code, quiet },
          })
        );
        if (event.code === 4401) return; // invalid token: do not hammer the server
        scheduleReconnect();
      };
      socket.onerror = () => { /* onclose handles the retry */ };
    } catch {
      scheduleReconnect();
    }
  }

  function disconnect() {
    eventsClient.closed = true;
    if (eventsClient.timer) window.clearTimeout(eventsClient.timer);
    eventsClient.timer = null;
    if (eventsClient.socket) {
      try { eventsClient.socket.close(); } catch { /* noop */ }
      eventsClient.socket = null;
    }
  }

  function onEvent(listener) {
    eventsClient.listeners.push(listener);
  }

  window.fluxpayWS = { connect, disconnect, onEvent, reconcile, currentCursor: () => eventsClient.cursor };
})();
