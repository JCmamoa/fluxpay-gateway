"""Webhook signing, HMAC verification, retries, DLQ and the demo receiver (CA-05/07)."""

from __future__ import annotations

import time

import httpx
import pytest

from app.security import parse_signature_header, sign_webhook, verify_webhook_signature
from app.services import worker
from app.services.webhooks import dispatch_once, retry_delay
from app.utils import iso_now, parse_iso

from conftest import API_KEY
from helpers import card_payload, idem_key, pix_payload


def _succeeded_card(client, auth_headers, db, settings):
    response = client.post(
        "/v1/payments",
        json=card_payload(),
        headers={**auth_headers, "Idempotency-Key": idem_key()},
    )
    assert response.status_code == 202, response.text
    payment = response.json()
    worker.run_once(db, settings)
    with db.connection() as conn:
        delivery = conn.execute(
            "SELECT * FROM webhook_deliveries WHERE payment_id = ?", (payment["id"],)
        ).fetchone()
    assert delivery is not None
    return payment, dict(delivery)


def _delivery_row(db, delivery_id):
    with db.connection() as conn:
        return dict(
            conn.execute("SELECT * FROM webhook_deliveries WHERE id = ?", (delivery_id,)).fetchone()
        )


def test_signature_generation_and_verification():
    body = b'{"hello":"world"}'
    digest = sign_webhook("whsec_test", 1_700_000_000, body)
    header = f"t=1700000000,v1={digest}"
    valid, reason = verify_webhook_signature(
        "whsec_test", header, body, tolerance_seconds=300, now=1_700_000_050
    )
    assert valid and reason is None

    valid, reason = verify_webhook_signature(
        "whsec_test", header, body.replace(b"world", b"there"), tolerance_seconds=300, now=1_700_000_050
    )
    assert not valid and reason == "invalid_signature"

    valid, reason = verify_webhook_signature(
        "whsec_test", header, body, tolerance_seconds=300, now=1_700_001_000
    )
    assert not valid and reason == "timestamp_outside_tolerance"

    valid, reason = verify_webhook_signature("whsec_test", "nonsense", body)
    assert not valid


def test_retry_delays_follow_spec_and_jitter_bounds():
    from app.config import Settings

    settings = Settings(
        webhook_retry_schedule=(10.0, 30.0, 120.0, 600.0, 3600.0),
        webhook_max_attempts=6,
        webhook_jitter_ratio=0.0,
    )
    assert [retry_delay(settings, attempt) for attempt in range(1, 6)] == [10, 30, 120, 600, 3600]

    settings.webhook_jitter_ratio = 0.2
    for attempt, base in zip(range(1, 6), [10, 30, 120, 600, 3600]):
        for _ in range(20):
            delay = retry_delay(settings, attempt)
            assert base * 0.8 <= delay <= base * 1.2


def test_dispatch_delivers_signed_payload(client, auth_headers, db, settings):
    _, delivery = _succeeded_card(client, auth_headers, db, settings)
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        return httpx.Response(200, json={"received": True})

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        assert dispatch_once(db, settings, client=http_client) == 1

    request = captured["request"]
    raw_body = request.content
    assert raw_body.decode("utf-8") == delivery["payload"]
    assert request.headers["x-event-id"] == delivery["event_id"]
    assert request.headers["content-type"] == "application/json"
    timestamp, signatures = parse_signature_header(request.headers["x-signature"])
    assert abs(timestamp - int(time.time())) <= 10
    assert signatures[0] == sign_webhook(settings.webhook_secret, timestamp, raw_body)

    updated = _delivery_row(db, delivery["id"])
    assert updated["status"] == "delivered"
    assert updated["attempts"] == 1
    assert updated["last_response_code"] == 200
    assert updated["next_attempt_at"] is None
    assert updated["delivered_at"] is not None

    with db.connection() as conn:
        attempts = conn.execute(
            "SELECT attempt_no, response_code, error FROM webhook_attempts WHERE delivery_id = ?",
            (delivery["id"],),
        ).fetchall()
    assert [tuple(row) for row in attempts] == [(1, 200, None)]


def test_retry_then_success_records_every_attempt(client, auth_headers, db, settings):
    _, delivery = _succeeded_card(client, auth_headers, db, settings)
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if calls["count"] <= 2:
            return httpx.Response(503, json={"error": "unavailable"})
        return httpx.Response(200, json={"ok": True})

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        assert dispatch_once(db, settings, client=http_client) == 1
        first = _delivery_row(db, delivery["id"])
        assert first["status"] == "pending"
        assert first["attempts"] == 1
        assert first["next_attempt_at"] is not None
        assert first["last_response_code"] == 503

        time.sleep(0.01)
        assert dispatch_once(db, settings, client=http_client) == 1
        second = _delivery_row(db, delivery["id"])
        assert second["status"] == "pending"
        assert second["attempts"] == 2

        time.sleep(0.01)
        assert dispatch_once(db, settings, client=http_client) == 1

    final = _delivery_row(db, delivery["id"])
    assert final["status"] == "delivered"
    assert final["attempts"] == 3
    with db.connection() as conn:
        attempts = conn.execute(
            "SELECT attempt_no, response_code FROM webhook_attempts WHERE delivery_id = ? ORDER BY id",
            (delivery["id"],),
        ).fetchall()
    assert [tuple(row) for row in attempts] == [(1, 503), (2, 503), (3, 200)]


def test_exhausted_delivery_goes_to_dlq_and_can_be_replayed(client, auth_headers, db, settings):
    payment, delivery = _succeeded_card(client, auth_headers, db, settings)

    def failing(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    with httpx.Client(transport=httpx.MockTransport(failing)) as http_client:
        for attempt in range(1, 4):
            time.sleep(0.01)
            dispatch_once(db, settings, client=http_client)
            current = _delivery_row(db, delivery["id"])
            expected_status = "failed" if attempt == 3 else "pending"
            assert current["status"] == expected_status
            assert current["attempts"] == attempt

    dead = _delivery_row(db, delivery["id"])
    assert dead["status"] == "failed"
    assert dead["next_attempt_at"] is None
    assert dead["last_response_code"] == 500

    listed = client.get(
        "/v1/webhook-deliveries",
        params={"status": "failed", "payment_id": payment["id"]},
        headers=auth_headers,
    ).json()["data"]
    assert len(listed) == 1
    assert listed[0]["id"] == delivery["id"]

    detail = client.get(f"/v1/webhook-deliveries/{delivery['id']}", headers=auth_headers).json()
    assert len(detail["attempt_log"]) == 3

    replay = client.post(f"/v1/webhook-deliveries/{delivery['id']}/retry", headers=auth_headers)
    assert replay.status_code == 202, replay.text
    assert replay.json()["status"] == "pending"

    def succeeding(request: httpx.Request) -> httpx.Response:
        return httpx.Response(204)

    with httpx.Client(transport=httpx.MockTransport(succeeding)) as http_client:
        time.sleep(0.01)
        assert dispatch_once(db, settings, client=http_client) == 1

    assert _delivery_row(db, delivery["id"])["status"] == "delivered"
    detail = client.get(f"/v1/webhook-deliveries/{delivery['id']}", headers=auth_headers).json()
    assert len(detail["attempt_log"]) == 4


def test_inactive_endpoint_is_parked_in_dlq(client, auth_headers, db, settings):
    _, delivery = _succeeded_card(client, auth_headers, db, settings)

    endpoint_id = client.get("/v1/webhook-endpoints", headers=auth_headers).json()["data"][0]["id"]
    response = client.patch(
        f"/v1/webhook-endpoints/{endpoint_id}", json={"active": False}, headers=auth_headers
    )
    assert response.status_code == 200

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover - must not be called
        raise AssertionError("inactive endpoints must not be called")

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        dispatch_once(db, settings, client=http_client)

    updated = _delivery_row(db, delivery["id"])
    assert updated["status"] == "failed"
    assert updated["last_error"] == "endpoint_inactive"


def test_demo_receiver_verifies_signature_and_deduplicates(client, auth_headers, db, settings):
    response = client.post(
        "/v1/payments",
        json=pix_payload(),
        headers={**auth_headers, "Idempotency-Key": idem_key()},
    )
    assert response.status_code == 202
    payment = response.json()

    with db.connection() as conn:
        delivery = dict(
            conn.execute(
                "SELECT * FROM webhook_deliveries WHERE payment_id = ? AND event_type = 'payment.pending'",
                (payment["id"],),
            ).fetchone()
        )

    timestamp = int(time.time())
    signature = sign_webhook(settings.webhook_secret, timestamp, delivery["payload"])
    headers = {
        "Content-Type": "application/json",
        "X-Event-Id": delivery["event_id"],
        "X-Signature": f"t={timestamp},v1={signature}",
    }

    first = client.post(
        "/demo/webhook-receiver?endpoint=whe_demo",
        content=delivery["payload"].encode(),
        headers=headers,
    )
    assert first.status_code == 200, first.text
    assert first.json()["duplicate"] is False

    duplicate = client.post(
        "/demo/webhook-receiver?endpoint=whe_demo",
        content=delivery["payload"].encode(),
        headers=headers,
    )
    assert duplicate.status_code == 200
    assert duplicate.json()["duplicate"] is True

    tampered = client.post(
        "/demo/webhook-receiver?endpoint=whe_demo",
        content=delivery["payload"].replace("payment", "paym3nt").encode(),
        headers=headers,
    )
    assert tampered.status_code == 401
    assert tampered.json()["error"]["code"] == "invalid_signature"

    receipts = client.get("/demo/webhook-receiver/receipts", headers=auth_headers).json()["data"]
    assert any(receipt["event_id"] == delivery["event_id"] and receipt["signature_valid"] for receipt in receipts)


def test_demo_fault_injection_causes_503_then_recovery(client, auth_headers, db, settings):
    response = client.post(
        "/v1/payments",
        json=pix_payload(),
        headers={**auth_headers, "Idempotency-Key": idem_key()},
    )
    payment = response.json()
    with db.connection() as conn:
        delivery = dict(
            conn.execute(
                "SELECT * FROM webhook_deliveries WHERE payment_id = ? AND event_type = 'payment.pending'",
                (payment["id"],),
            ).fetchone()
        )

    injected = client.post(
        "/demo/webhook-failures", json={"count": 1}, headers=auth_headers
    )
    assert injected.status_code == 200

    timestamp = int(time.time())
    signature = sign_webhook(settings.webhook_secret, timestamp, delivery["payload"])
    headers = {
        "Content-Type": "application/json",
        "X-Event-Id": delivery["event_id"],
        "X-Signature": f"t={timestamp},v1={signature}",
    }
    failed = client.post(
        "/demo/webhook-receiver?endpoint=whe_demo",
        content=delivery["payload"].encode(),
        headers=headers,
    )
    assert failed.status_code == 503
    assert failed.json()["error"]["code"] == "simulated_receiver_unavailable"

    timestamp = int(time.time())
    headers["X-Signature"] = f"t={timestamp},v1={sign_webhook(settings.webhook_secret, timestamp, delivery['payload'])}"
    recovered = client.post(
        "/demo/webhook-receiver?endpoint=whe_demo",
        content=delivery["payload"].encode(),
        headers=headers,
    )
    assert recovered.status_code == 200
