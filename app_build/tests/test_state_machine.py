"""State machine, CAS, queue recovery and PIX lifecycle (CA-04/05/06/09)."""

from __future__ import annotations

import json

import pytest

from app.security import hash_api_key
from app.services import queue, worker
from app.services.payments import expire_pix_payments, process_payment, transition_payment

from conftest import API_KEY, SECOND_API_KEY
from helpers import (
    DECLINED_CARD,
    INSUFFICIENT_CARD,
    VALID_CARD,
    card_payload,
    idem_key,
    pix_payload,
)


def create(client, auth_headers, payload, key=None):
    response = client.post(
        "/v1/payments",
        json=payload,
        headers={**auth_headers, "Idempotency-Key": key or idem_key()},
    )
    assert response.status_code == 202, response.text
    return response.json()


def payment_events(db, payment_id):
    with db.connection() as conn:
        return conn.execute(
            "SELECT type FROM payment_events WHERE payment_id = ? ORDER BY created_at, id",
            (payment_id,),
        ).fetchall()


def test_card_approval_lifecycle_and_duplicate_consumption(client, auth_headers, db, settings):
    created = create(client, auth_headers, card_payload(VALID_CARD))
    assert created["status"] == "pending"
    assert created["card"] == {"brand": "visa", "last4": "4242"}
    assert "number" not in created["card"]

    # API is asynchronous: nothing was charged until the worker runs.
    assert client.get(f"/v1/payments/{created['id']}", headers=auth_headers).json()["status"] == "pending"
    assert queue.queue_stats(db)["pending"] == 1

    summary = worker.run_once(db, settings)
    assert summary["claimed"] == 1
    assert summary["processed"] == 1

    payment = client.get(f"/v1/payments/{created['id']}", headers=auth_headers).json()
    assert payment["status"] == "succeeded"
    assert payment["authorization_code"].startswith("AUTH_")
    assert [row["type"] for row in payment_events(db, created["id"])] == [
        "payment.pending",
        "payment.processing",
        "payment.succeeded",
    ]

    with db.connection() as conn:
        deliveries = conn.execute(
            "SELECT event_type, status FROM webhook_deliveries WHERE payment_id = ?",
            (created["id"],),
        ).fetchall()
    assert [(row["event_type"], row["status"]) for row in deliveries] == [
        ("payment.succeeded", "pending")
    ]

    # Reprocessing the same message/job N times must not add transitions/events.
    before = len(payment_events(db, created["id"]))
    for _ in range(5):
        assert process_payment(db, settings, created["id"]) == "ignored"
    assert len(payment_events(db, created["id"])) == before
    assert worker.run_once(db, settings)["claimed"] == 0


@pytest.mark.parametrize(
    ("card_number", "expected_reason"),
    [(DECLINED_CARD, "card_declined"), (INSUFFICIENT_CARD, "insufficient_funds")],
)
def test_card_declines_are_deterministic(client, auth_headers, db, settings, card_number, expected_reason):
    created = create(client, auth_headers, card_payload(card_number))
    worker.run_once(db, settings)

    payment = client.get(f"/v1/payments/{created['id']}", headers=auth_headers).json()
    assert payment["status"] == "failed"
    assert payment["failure_reason"] == expected_reason
    assert payment["authorization_code"] is None
    assert [row["type"] for row in payment_events(db, created["id"])][-1] == "payment.failed"

    with db.connection() as conn:
        delivery = conn.execute(
            "SELECT event_type, payload FROM webhook_deliveries WHERE payment_id = ?",
            (created["id"],),
        ).fetchone()
    assert delivery["event_type"] == "payment.failed"
    event = json.loads(delivery["payload"])
    assert event["data"]["payment"]["failure_reason"] == expected_reason


def test_pix_simulation_flow(client, auth_headers, db, settings):
    created = create(client, auth_headers, pix_payload())
    assert created["status"] == "pending"

    simulation = client.post(
        f"/v1/payments/{created['id']}/simulate-payment",
        json={"result": "paid"},
        headers=auth_headers,
    )
    assert simulation.status_code == 202, simulation.text
    assert simulation.json()["status"] == "processing"
    assert client.get(f"/v1/payments/{created['id']}", headers=auth_headers).json()["status"] == "processing"

    worker.run_once(db, settings)
    final = client.get(f"/v1/payments/{created['id']}", headers=auth_headers).json()
    assert final["status"] == "succeeded"

    with db.connection() as conn:
        deliveries = conn.execute(
            "SELECT event_type, status FROM webhook_deliveries WHERE payment_id = ? ORDER BY event_type",
            (created["id"],),
        ).fetchall()
    assert [(row["event_type"], row["status"]) for row in deliveries] == [
        ("payment.pending", "pending"),
        ("payment.succeeded", "pending"),
    ]

    repeat = client.post(
        f"/v1/payments/{created['id']}/simulate-payment", json={"result": "paid"}, headers=auth_headers
    )
    assert repeat.status_code == 409
    assert repeat.json()["error"]["code"] == "payment_not_simulatable"

    # Duplicate simulate/worker jobs are no-ops.
    before = len(payment_events(db, created["id"]))
    assert process_payment(db, settings, created["id"]) == "ignored"
    assert len(payment_events(db, created["id"])) == before


def test_pix_expiration_moves_pending_to_failed(client, auth_headers, db, settings):
    created = create(client, auth_headers, pix_payload())
    with db.transaction() as conn:
        conn.execute(
            "UPDATE payments SET pix_expires_at = '2000-01-01T00:00:00.000000Z' WHERE id = ?",
            (created["id"],),
        )

    simulation = client.post(
        f"/v1/payments/{created['id']}/simulate-payment", json={"result": "paid"}, headers=auth_headers
    )
    assert simulation.status_code == 409
    assert simulation.json()["error"]["code"] == "payment_expired"

    assert expire_pix_payments(db, settings) == 1
    payment = client.get(f"/v1/payments/{created['id']}", headers=auth_headers).json()
    assert payment["status"] == "failed"
    assert payment["failure_reason"] == "pix_expired"
    with db.connection() as conn:
        event = conn.execute(
            "SELECT event_type, payload FROM webhook_deliveries WHERE payment_id = ? AND event_type = 'payment.failed'",
            (created["id"],),
        ).fetchone()
    assert event is not None
    assert json.loads(event["payload"])["data"]["payment"]["failure_reason"] == "pix_expired"


def test_cas_rejects_invalid_and_duplicate_transitions(client, auth_headers, db, settings):
    created = create(client, auth_headers, card_payload(VALID_CARD))
    payment_id = created["id"]

    with db.transaction() as conn:
        # Cannot jump pending -> succeeded directly.
        assert (
            transition_payment(
                conn, settings, payment_id, "processing", "succeeded", "payment.succeeded"
            )
            is None
        )
        assert (
            transition_payment(
                conn, settings, payment_id, "pending", "succeeded", "payment.succeeded"
            )
            is None
        )
        updated = transition_payment(
            conn, settings, payment_id, "pending", "processing", "payment.processing"
        )
        assert updated is not None and updated["status"] == "processing"
        # Second CAS from the same expected state is a no-op.
        assert (
            transition_payment(
                conn, settings, payment_id, "pending", "processing", "payment.processing"
            )
            is None
        )

    with db.connection() as conn:
        status = conn.execute("SELECT status FROM payments WHERE id = ?", (payment_id,)).fetchone()[0]
        events = conn.execute(
            "SELECT COUNT(*) FROM payment_events WHERE payment_id = ?", (payment_id,)
        ).fetchone()[0]
    assert status == "processing"
    assert events == 2  # created + processing

    # A stale worker job finishing the payment still works exactly once.
    assert process_payment(db, settings, payment_id) == "succeeded"
    assert process_payment(db, settings, payment_id) == "ignored"
    with db.connection() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM payment_events WHERE payment_id = ?", (payment_id,)
        ).fetchone()[0] == 3


def test_queue_recovery_after_crash(client, auth_headers, db, settings):
    created = create(client, auth_headers, card_payload(VALID_CARD))
    claimed = queue.claim_messages(db, settings)
    assert len(claimed) == 1
    assert queue.queue_stats(db)["processing"] == 1

    # Simulate a process restart: no worker owns the lease anymore.
    assert queue.recover_inflight(db) == 1
    assert queue.queue_stats(db)["pending"] == 1
    worker.run_once(db, settings)

    payment = client.get(f"/v1/payments/{created['id']}", headers=auth_headers).json()
    assert payment["status"] == "succeeded"
    assert len(payment_events(db, created["id"])) == 3


def test_payment_isolation_between_merchants(client, auth_headers, db):
    created = create(client, auth_headers, pix_payload())
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO merchants(id, name, api_key_hash, created_at) VALUES (?, ?, ?, ?)",
            ("mch_other", "Outro", hash_api_key(SECOND_API_KEY), "2026-01-01T00:00:00.000000Z"),
        )
    other_headers = {"Authorization": f"Bearer {SECOND_API_KEY}"}

    assert client.get(f"/v1/payments/{created['id']}", headers=other_headers).status_code == 404
    assert (
        client.post(
            f"/v1/payments/{created['id']}/simulate-payment", json={"result": "paid"}, headers=other_headers
        ).status_code
        == 404
    )
    listing = client.get("/v1/payments", headers=other_headers).json()
    assert listing["data"] == []


def test_invalid_pix_simulation_result_is_422(client, auth_headers):
    created = create(client, auth_headers, pix_payload())
    response = client.post(
        f"/v1/payments/{created['id']}/simulate-payment",
        json={"result": "refunded"},
        headers=auth_headers,
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
