"""Public API contract: auth, validation, listing and error envelopes (CA-08)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import create_app
from app.services import worker

from conftest import API_KEY
from helpers import card_payload, idem_key, pix_payload

VALID_HEADERS = {"Authorization": f"Bearer {API_KEY}"}


def test_health_is_public(client):
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"
    assert body["version"] == "1.0.0"


def test_auth_is_required_and_validated(client):
    missing = client.get("/v1/payments")
    assert missing.status_code == 401
    assert missing.json()["error"]["code"] == "invalid_api_key"
    assert missing.headers["www-authenticate"] == "Bearer"

    invalid = client.get("/v1/payments", headers={"Authorization": "Bearer wrong_key_123456"})
    assert invalid.status_code == 401

    malformed = client.get("/v1/payments", headers={"Authorization": "Basic abc"})
    assert malformed.status_code == 401


def test_validation_errors_use_envelope_and_never_echo_card_data(client):
    payload = card_payload()
    payload["card"]["number"] = "4242424242424241"  # valid length, invalid Luhn
    payload["card"]["cvv"] = "987"
    response = client.post(
        "/v1/payments", json=payload, headers={**VALID_HEADERS, "Idempotency-Key": idem_key()}
    )
    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "validation_error"
    assert isinstance(body["error"]["details"], list)
    assert "4242424242424241" not in response.text
    assert '"input"' not in response.text
    assert "987" not in response.text


def test_business_validation_rules(client):
    cases = []

    payload = pix_payload()
    payload["card"] = card_payload()["card"]
    cases.append(payload)

    payload = card_payload()
    payload.pop("card")
    cases.append(payload)

    cases.append(pix_payload(currency="USD"))
    cases.append(pix_payload(amount=0))
    cases.append(pix_payload(amount=-10))

    expired = card_payload()
    expired["card"]["exp_year"] = 2020
    cases.append(expired)

    short_cvv = card_payload()
    short_cvv["card"]["cvv"] = "12"
    cases.append(short_cvv)

    bad_metadata = pix_payload(metadata={"order": "x" * 5000})
    cases.append(bad_metadata)

    for index, payload in enumerate(cases):
        response = client.post(
            "/v1/payments",
            json=payload,
            headers={**VALID_HEADERS, "Idempotency-Key": f"validation_{index}"},
        )
        assert response.status_code == 422, (index, response.text)
        assert response.json()["error"]["code"] == "validation_error"


def test_unknown_payment_and_unknown_route_return_envelopes(client):
    missing = client.get("/v1/payments/pay_does_not_exist", headers=VALID_HEADERS)
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "payment_not_found"

    unknown = client.get("/v1/nope", headers=VALID_HEADERS)
    assert unknown.status_code == 404
    assert unknown.json()["error"]["code"] == "not_found"


def test_listing_is_cursor_paginated(client):
    created_ids = []
    for index in range(3):
        response = client.post(
            "/v1/payments",
            json=pix_payload(amount=1000 + index),
            headers={**VALID_HEADERS, "Idempotency-Key": f"list_{index}"},
        )
        assert response.status_code == 202
        created_ids.append(response.json()["id"])

    first = client.get("/v1/payments", params={"limit": 2}, headers=VALID_HEADERS).json()
    assert len(first["data"]) == 2
    assert first["has_more"] is True
    assert first["next_cursor"]

    second = client.get(
        "/v1/payments",
        params={"limit": 2, "starting_after": first["next_cursor"]},
        headers=VALID_HEADERS,
    ).json()
    assert len(second["data"]) == 1
    assert second["has_more"] is False
    assert {payment["id"] for payment in first["data"] + second["data"]} == set(created_ids)

    invalid_cursor = client.get(
        "/v1/payments", params={"starting_after": "pay_missing"}, headers=VALID_HEADERS
    )
    assert invalid_cursor.status_code == 422
    assert invalid_cursor.json()["error"]["code"] == "invalid_cursor"


def test_payment_detail_and_event_history(client):
    response = client.post(
        "/v1/payments",
        json=pix_payload(),
        headers={**VALID_HEADERS, "Idempotency-Key": "detail_events"},
    )
    payment = response.json()
    detail = client.get(f"/v1/payments/{payment['id']}", headers=VALID_HEADERS).json()
    assert detail["id"] == payment["id"]
    assert detail["status"] == "pending"
    assert detail["pix"]["qr_code"]
    assert len(detail["pix"]["qr_code_image"]) > 100

    events = client.get(f"/v1/payments/{payment['id']}/events", headers=VALID_HEADERS).json()
    assert events["data"][0]["type"] == "payment.pending"
    assert events["data"][0]["payload"]["from_status"] is None

    # Events are append-only at the database level.
    from app.db import Database

    db = client.app.state.db
    with db.transaction() as conn:
        try:
            conn.execute("UPDATE payment_events SET type = 'tampered'")
            raise AssertionError("trigger should have blocked the update")
        except Exception as exc:  # sqlite3.IntegrityError
            assert "append-only" in str(exc)


def test_metrics_requires_auth_and_reports_counts(client):
    assert client.get("/v1/metrics").status_code == 401
    response = client.post(
        "/v1/payments",
        json=card_payload(),
        headers={**VALID_HEADERS, "Idempotency-Key": "metrics_key"},
    )
    assert response.status_code == 202
    metrics = client.get("/v1/metrics", headers=VALID_HEADERS).json()
    assert metrics["payments"]["total"] >= 1
    assert metrics["queue"]["pending"] >= 1
    assert "deliveries_by_status" in metrics["webhooks"]
    assert metrics["uptime_seconds"] >= 0


def test_demo_config_guardrails(client):
    config = client.get("/demo/config").json()
    assert config["demo_mode"] is True
    assert config["demo_api_key"] == API_KEY
    assert config["merchant"]["id"] == "mch_test"
    assert config["webhooks"]["max_attempts"] == 3

    with client.app.state.db.connection() as conn:
        row = conn.execute("SELECT api_key_hash FROM merchants").fetchone()
    assert API_KEY not in row["api_key_hash"]


def test_queue_and_payments_survive_application_restart(settings):
    first_app = create_app(settings)
    with TestClient(first_app) as first_client:
        response = first_client.post(
            "/v1/payments",
            json=card_payload(),
            headers={"Authorization": f"Bearer {API_KEY}", "Idempotency-Key": "restart_key"},
        )
        assert response.status_code == 202
        payment_id = response.json()["id"]

    # New app instance, same SQLite file: queued work must still be there.
    second_app = create_app(settings)
    with TestClient(second_app) as second_client:
        worker.run_once(second_app.state.db, settings)
        payment = second_client.get(
            f"/v1/payments/{payment_id}", headers={"Authorization": f"Bearer {API_KEY}"}
        ).json()
        assert payment["status"] == "succeeded"

        with second_app.state.db.connection() as conn:
            queue_row = conn.execute("SELECT status FROM queue_messages").fetchone()
        assert queue_row["status"] == "done"
