"""Idempotency guarantees, including true concurrent requests (CA-01/02/03)."""

from __future__ import annotations

import concurrent.futures
import threading

import httpx
import pytest

from app.services.payments import request_fingerprint
from app.schemas import PaymentCreate

from conftest import API_KEY
from helpers import card_payload, idem_key, pix_payload


def test_same_key_same_payload_replays_stored_response(client, auth_headers, db):
    key = idem_key()
    payload = pix_payload()

    first = client.post("/v1/payments", json=payload, headers={**auth_headers, "Idempotency-Key": key})
    assert first.status_code == 202, first.text
    assert "idempotent-replay" not in {k.lower() for k in first.headers}
    created = first.json()
    assert created["status"] == "pending"
    assert created["amount"] == 12990
    assert created["method"] == "pix"
    assert created["pix"]["txid"]
    assert created["pix"]["qr_code"].startswith("000201")
    assert created["pix"]["expires_at"]

    second = client.post("/v1/payments", json=payload, headers={**auth_headers, "Idempotency-Key": key})
    assert second.status_code == 200, second.text
    assert second.headers["Idempotent-Replay"] == "true"
    assert second.content == first.content
    assert second.json() == created

    with db.connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM payments").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM idempotency_keys").fetchone()[0] == 1


def test_same_key_different_payload_conflicts(client, auth_headers, db):
    key = idem_key()
    first = client.post(
        "/v1/payments", json=pix_payload(amount=1000), headers={**auth_headers, "Idempotency-Key": key}
    )
    assert first.status_code == 202

    conflict = client.post(
        "/v1/payments", json=pix_payload(amount=9999), headers={**auth_headers, "Idempotency-Key": key}
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "idempotency_conflict"
    with db.connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM payments").fetchone()[0] == 1


def test_normalized_card_payload_replays(client, auth_headers):
    key = idem_key()
    payload = card_payload()
    payload["card"]["number"] = "4242 4242 4242 4242"
    first = client.post("/v1/payments", json=payload, headers={**auth_headers, "Idempotency-Key": key})
    assert first.status_code == 202
    payload["card"]["number"] = "4242424242424242"
    second = client.post("/v1/payments", json=payload, headers={**auth_headers, "Idempotency-Key": key})
    assert second.status_code == 200
    assert second.headers["Idempotent-Replay"] == "true"


def test_missing_and_invalid_idempotency_key(client, auth_headers):
    missing = client.post("/v1/payments", json=pix_payload(), headers=auth_headers)
    assert missing.status_code == 400
    assert missing.json()["error"]["code"] == "missing_idempotency_key"

    too_long = client.post(
        "/v1/payments",
        json=pix_payload(),
        headers={**auth_headers, "Idempotency-Key": "x" * 256},
    )
    assert too_long.status_code == 400
    assert too_long.json()["error"]["code"] == "invalid_idempotency_key"


def test_fingerprint_is_order_independent():
    first = PaymentCreate.model_validate(pix_payload(metadata={"a": 1, "b": 2}))
    second = PaymentCreate.model_validate(pix_payload(metadata={"b": 2, "a": 1}))
    assert request_fingerprint(first) == request_fingerprint(second)

    different = PaymentCreate.model_validate(pix_payload(metadata={"a": 1, "b": 3}))
    assert request_fingerprint(first) != request_fingerprint(different)


def test_concurrent_same_key_creates_exactly_one_payment(live_server, db):
    key = idem_key()
    payload = pix_payload(amount=4242)
    barrier = threading.Barrier(16)
    results: list[httpx.Response] = []
    lock = threading.Lock()

    def create() -> None:
        barrier.wait(timeout=10)
        response = httpx.post(
            f"{live_server}/v1/payments",
            json=payload,
            headers={"Authorization": f"Bearer {API_KEY}", "Idempotency-Key": key},
            timeout=30,
        )
        with lock:
            results.append(response)

    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
        futures = [pool.submit(create) for _ in range(16)]
        for future in futures:
            future.result(timeout=45)

    assert len(results) == 16
    created = [response for response in results if response.status_code == 202]
    replayed = [response for response in results if response.status_code == 200]
    assert len(created) == 1, [response.status_code for response in results]
    assert len(replayed) == 15
    assert all(response.headers.get("Idempotent-Replay") == "true" for response in replayed)
    ids = {response.json()["id"] for response in results}
    assert len(ids) == 1

    with db.connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM payments").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM idempotency_keys").fetchone()[0] == 1


def test_concurrent_different_payloads_same_key_yields_one_winner(live_server, db):
    key = idem_key()
    barrier = threading.Barrier(12)
    results: list[httpx.Response] = []
    lock = threading.Lock()

    def create(index: int) -> None:
        barrier.wait(timeout=10)
        response = httpx.post(
            f"{live_server}/v1/payments",
            json=pix_payload(amount=1000 + index),
            headers={"Authorization": f"Bearer {API_KEY}", "Idempotency-Key": key},
            timeout=30,
        )
        with lock:
            results.append(response)

    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
        futures = [pool.submit(create, index) for index in range(12)]
        for future in futures:
            future.result(timeout=45)

    assert len([response for response in results if response.status_code == 202]) == 1
    assert len([response for response in results if response.status_code == 409]) == 11
    with db.connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM payments").fetchone()[0] == 1
