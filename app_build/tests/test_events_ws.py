"""Realtime WS tests (RF-13 / RF-14, CA-11 / CA-12 / RNF-10): handshake auth,
live broadcast, reconnect with ``after`` + REST reconciliation and heartbeat.

Uses a real uvicorn server (``live_server``) with ``websockets.sync.client``.
The module-level ``settings`` fixture overrides the global one so the server
runs with event fanout enabled and a fast heartbeat.
"""

from __future__ import annotations

import json
import threading
import time

import httpx
import pytest
import uvicorn
from fastapi.testclient import TestClient
from websockets.sync import client as ws_client

from app.main import create_app

from conftest import API_KEY, build_settings
from helpers import pix_payload, idem_key


@pytest.fixture()
def settings(tmp_path):
    return build_settings(
        tmp_path,
        event_fanout=True,
        ws_poll_seconds=0.05,
        ws_heartbeat_seconds=1.0,
    )


@pytest.fixture()
def auth_headers():
    return {"Authorization": f"Bearer {API_KEY}"}


def _ws_url(base_url: str, **params) -> str:
    query = "&".join(f"{key}={value}" for key, value in params.items() if value is not None)
    return f"ws://{base_url.split('//', 1)[1]}/ws/events" + (f"?{query}" if query else "")


def _create_payment(base_url: str, headers: dict[str, str]) -> dict:
    response = httpx.post(
        f"{base_url}/v1/payments",
        json=pix_payload(),
        headers={**headers, "Idempotency-Key": idem_key()},
        timeout=10.0,
    )
    assert response.status_code == 202, response.text
    return response.json()


def _recv_event(ws, timeout: float = 10.0) -> dict:
    """recv skipping ``server.ping`` heartbeats."""
    while True:
        message = json.loads(ws.recv(timeout=timeout))
        if message["type"] != "server.ping":
            return message


def test_ws_rejects_invalid_token(live_server):
    """Wrong/absent token → handshake rejected (4401 / HTTP-level 403)."""
    with pytest.raises(Exception):
        ws_client.connect(_ws_url(live_server, token="sk_wrong_key"), timeout=5)

    with pytest.raises(Exception):
        ws_client.connect(_ws_url(live_server), timeout=5)


def test_ws_streams_live_events_within_budget(live_server, auth_headers):
    """CA-11: a hub event recorded over HTTP arrives on the socket fast."""
    with ws_client.connect(_ws_url(live_server, token=API_KEY), timeout=5) as ws:
        started = time.perf_counter()
        _create_payment(live_server, auth_headers)
        message = _recv_event(ws)
        elapsed = time.perf_counter() - started

    assert elapsed < 2.0, f"live event took {elapsed:.2f}s (budget \u22480.5s app-level)"
    assert message["id"].startswith("evt_")
    # First fanout event may be the CRM ripple before the payment envelope.
    assert message["type"] in {"crm.customer.created", "payment.pending"}
    assert message["created_at"]


def test_ws_reconnect_replays_with_after_and_reconciles(live_server, auth_headers):
    """CA-12: fresh connections watermark at the live tail (no backlog); a
    reconnect with ``after`` replays only the missing events and matches the
    REST snapshot from the same cursor (WS = notifying channel)."""
    with ws_client.connect(_ws_url(live_server, token=API_KEY), timeout=5) as ws:
        # The connection watermarks at the current tail; the first live event
        # after connecting is the one triggered by the *next* payment.
        _create_payment(live_server, auth_headers)
        event = _recv_event(ws)
        cursor = event["id"]

    # More events while the socket is offline.
    second = _create_payment(live_server, auth_headers)

    with ws_client.connect(
        _ws_url(live_server, token=API_KEY, after=cursor), timeout=5
    ) as ws:
        replayed = _recv_event(ws)
        assert replayed["id"] != event["id"]

        # Reconciliation: REST snapshot from the same cursor (ascending) starts
        # with exactly the event the socket replayed first.
        snapshot = httpx.get(
            f"{live_server}/v1/events",
            params={"after": cursor, "order": "asc"},
            timeout=10,
        ).json()
        snapshot_ids = [event["id"] for event in snapshot["data"]]
        assert snapshot_ids[0] == replayed["id"]

    _ = second  # consumed above


def test_ws_bad_after_cursor_rejected(live_server):
    with pytest.raises(Exception):
        ws_client.connect(
            _ws_url(live_server, token=API_KEY, after="evt_0000unknown"),
            timeout=5,
        )


def test_heartbeat_keeps_socket_alive(live_server, auth_headers):
    """Idle clients get a periodic ``server.ping`` heartbeat."""
    with ws_client.connect(_ws_url(live_server, token=API_KEY), timeout=5) as ws:
        started = time.perf_counter()
        while True:
            message = json.loads(ws.recv(timeout=10))
            if message["type"] == "server.ping":
                break
            assert time.perf_counter() - started < 5



def test_rest_events_snapshot_filters(live_server, auth_headers):
    """RF-13: snapshot endpoint = source of truth (type/limit/order/after)."""
    payment = _create_payment(live_server, auth_headers)

    pending = httpx.get(
        f"{live_server}/v1/events",
        params={"type": "payment.pending", "order": "desc", "limit": 5},
        timeout=10,
    ).json()
    assert pending["object"] == "list"
    assert any(event["data"].get("payment_id") == payment["id"] for event in pending["data"])
    if len(pending["data"]) >= 2:
        assert pending["data"][0]["seq"] >= pending["data"][1]["seq"]

    after = pending["data"][0]["id"]
    newer = httpx.get(
        f"{live_server}/v1/events", params={"after": after}, timeout=10
    ).json()["data"]
    assert all(event["seq"] > pending["data"][0]["seq"] for event in newer)
