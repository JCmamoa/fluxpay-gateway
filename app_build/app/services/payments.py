"""Payment domain service: creation, idempotency, state machine, simulation."""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import time
from typing import Any

from ..config import Settings
from ..errors import ApiError
from ..pix import build_pix_payload, generate_txid
from ..schemas import PaymentCreate
from ..security import simulated_card_outcome
from ..utils import (
    canonical_json,
    iso_minutes_after,
    iso_now,
    new_id,
    parse_iso,
    utc_now,
)
from . import queue
from .webhooks import emit_payment_event, public_payment

logger = logging.getLogger("app.payments")

_UNSET = object()

# The v1 state machine. Anything else is rejected before touching the row, so
# invalid transitions cannot even be attempted by a buggy worker.
ALLOWED_TRANSITIONS = {
    ("pending", "processing"),
    ("pending", "failed"),
    ("processing", "succeeded"),
    ("processing", "failed"),
}


def request_fingerprint(payload: PaymentCreate) -> str:
    """SHA-256 of the canonical, normalized request.

    The card PAN/CVV are part of the hash computation but never persisted or
    logged; the digest itself is not reversible.
    """
    data = payload.model_dump(mode="json", exclude_none=True)
    return hashlib.sha256(canonical_json(data).encode("utf-8")).hexdigest()


def get_payment(db: Any, merchant_id: str, payment_id: str) -> dict[str, Any]:
    with db.connection() as conn:
        row = conn.execute(
            "SELECT * FROM payments WHERE id = ? AND merchant_id = ?",
            (payment_id, merchant_id),
        ).fetchone()
    if row is None:
        raise ApiError(404, "payment_not_found", "Payment not found")
    return dict(row)


def create_payment(
    db: Any,
    settings: Settings,
    merchant_id: str,
    idempotency_key: str,
    payload: PaymentCreate,
) -> tuple[int, str, bool]:
    """Create a payment or replay a stored response.

    Returns ``(http_status, raw_json_body, is_replay)``.  The whole flow runs
    under a single ``BEGIN IMMEDIATE`` transaction, so concurrent requests with
    the same key cannot both insert.
    """
    fingerprint = request_fingerprint(payload)

    with db.transaction() as conn:
        existing = conn.execute(
            "SELECT request_fingerprint, response_status, response_body "
            "FROM idempotency_keys WHERE merchant_id = ? AND key = ?",
            (merchant_id, idempotency_key),
        ).fetchone()
        if existing is not None:
            if existing["request_fingerprint"] != fingerprint:
                logger.warning(
                    "idempotency conflict",
                    extra={"merchant_id": merchant_id},
                )
                raise ApiError(
                    409,
                    "idempotency_conflict",
                    "This Idempotency-Key was already used with a different request payload",
                )
            # Replays always use 200 + Idempotent-Replay, regardless of the
            # original 202 status code stored for the first response.
            return 200, existing["response_body"], True

        now = iso_now()
        payment_id = new_id("pay")
        txid = None
        qr_code = None
        expires_at = None
        if payload.method == "pix":
            txid = generate_txid()
            expires_at = iso_minutes_after(settings.pix_expires_minutes)
            qr_code = build_pix_payload(
                key=settings.pix_key,
                merchant_name=settings.merchant_name,
                city=settings.pix_city,
                amount_cents=payload.amount,
                txid=txid,
                description=payload.description,
            )

        card = payload.card
        if payload.customer:
            from .crm import ensure_customer

            ensure_customer(conn, payload.customer.email, payload.customer.name, fanout=settings.event_fanout)
        conn.execute(
            """
            INSERT INTO payments(
                id, merchant_id, amount_cents, currency, method, status,
                description, customer_name, customer_email, metadata,
                pix_txid, pix_qr_code, pix_expires_at,
                card_brand, card_last4, authorization_code, failure_reason,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?)
            """,
            (
                payment_id,
                merchant_id,
                payload.amount,
                payload.currency,
                payload.method,
                payload.description,
                payload.customer.name if payload.customer else None,
                payload.customer.email if payload.customer else None,
                canonical_json(payload.metadata or {}),
                txid,
                qr_code,
                expires_at,
                card.brand if card else None,
                card.last4 if card else None,
                now,
                now,
            ),
        )

        row = conn.execute("SELECT * FROM payments WHERE id = ?", (payment_id,)).fetchone()
        # Audit every creation; the pending webhook is part of the PIX contract
        # (cards emit their first webhook when the async result is known).
        emit_payment_event(
            conn,
            settings,
            row,
            event_type="payment.pending",
            from_status=None,
            enqueue_webhook=(payload.method == "pix"),
        )

        if payload.method in {"credit_card", "debit_card"}:
            queue.enqueue(
                conn,
                queue.PAYMENT_PROCESS,
                {"payment_id": payment_id},
                dedupe_key=f"payment.process:{payment_id}:created",
                max_attempts=settings.queue_max_attempts,
            )

        body = canonical_json(public_payment(row))
        conn.execute(
            """
            INSERT INTO idempotency_keys(
                merchant_id, key, request_fingerprint, response_status,
                response_body, payment_id, created_at
            ) VALUES (?, ?, ?, 202, ?, ?, ?)
            """,
            (merchant_id, idempotency_key, fingerprint, body, payment_id, now),
        )

    logger.info(
        "payment created",
        extra={"payment_id": payment_id, "merchant_id": merchant_id, "method": payload.method},
    )
    return 202, body, False


def list_payments(
    db: Any,
    merchant_id: str,
    *,
    limit: int = 20,
    starting_after: str | None = None,
) -> dict[str, Any]:
    params: list[Any] = [merchant_id]
    cursor_clause = ""
    if starting_after:
        with db.connection() as conn:
            cursor = conn.execute(
                "SELECT created_at, id FROM payments WHERE id = ? AND merchant_id = ?",
                (starting_after, merchant_id),
            ).fetchone()
        if cursor is None:
            raise ApiError(422, "invalid_cursor", "starting_after does not reference one of your payments")
        cursor_clause = " AND (created_at < ? OR (created_at = ? AND id < ?))"
        params.extend([cursor["created_at"], cursor["created_at"], cursor["id"]])

    with db.connection() as conn:
        rows = conn.execute(
            "SELECT * FROM payments WHERE merchant_id = ?"
            + cursor_clause
            + " ORDER BY created_at DESC, id DESC LIMIT ?",
            (*params, limit + 1),
        ).fetchall()

    has_more = len(rows) > limit
    rows = rows[:limit]
    data = [public_payment(row, include_qr_image=False) for row in rows]
    return {
        "object": "list",
        "data": data,
        "has_more": has_more,
        "next_cursor": rows[-1]["id"] if rows and has_more else None,
    }


def list_events(db: Any, merchant_id: str, payment_id: str) -> list[dict[str, Any]]:
    get_payment(db, merchant_id, payment_id)
    with db.connection() as conn:
        rows = conn.execute(
            "SELECT * FROM payment_events WHERE payment_id = ? ORDER BY created_at, id",
            (payment_id,),
        ).fetchall()
    events = []
    for row in rows:
        try:
            event_payload = json.loads(row["payload"])
        except (TypeError, ValueError):
            event_payload = {}
        events.append(
            {
                "id": row["id"],
                "object": "payment_event",
                "payment_id": row["payment_id"],
                "type": row["type"],
                "payload": event_payload,
                "created_at": row["created_at"],
            }
        )
    return events


def _is_expired(row: dict[str, Any]) -> bool:
    expires_at = row.get("pix_expires_at")
    return bool(expires_at) and parse_iso(expires_at) <= utc_now()


def transition_payment(
    conn: sqlite3.Connection,
    settings: Settings,
    payment_id: str,
    expected_status: str,
    new_status: str,
    event_type: str,
    *,
    reason: str | None = None,
    authorization_code: str | None | object = _UNSET,
    enqueue_webhook: bool = True,
) -> dict[str, Any] | None:
    """Compare-and-set a status transition and append its event atomically.

    Returns the updated payment, or ``None`` when the expected status did not
    match (a duplicate/competing message) — callers must treat that as a no-op.
    """
    if (expected_status, new_status) not in ALLOWED_TRANSITIONS:
        return None
    now = iso_now()
    assignments = ["status = ?", "updated_at = ?", "failure_reason = ?"]
    values: list[Any] = [new_status, now, reason]
    if authorization_code is not _UNSET:
        assignments.append("authorization_code = ?")
        values.append(authorization_code)

    cursor = conn.execute(
        f"UPDATE payments SET {', '.join(assignments)} WHERE id = ? AND status = ?",
        (*values, payment_id, expected_status),
    )
    if cursor.rowcount != 1:
        return None

    row = conn.execute("SELECT * FROM payments WHERE id = ?", (payment_id,)).fetchone()
    assert row is not None
    emit_payment_event(
        conn,
        settings,
        row,
        event_type=event_type,
        from_status=expected_status,
        reason=reason,
        enqueue_webhook=enqueue_webhook,
    )
    return dict(row)


def process_payment(db: Any, settings: Settings, payment_id: str) -> str:
    """Execute one payment job. Safe to call repeatedly (CAS + dedupe)."""
    row: dict[str, Any]
    with db.transaction() as conn:
        found = conn.execute("SELECT * FROM payments WHERE id = ?", (payment_id,)).fetchone()
        if found is None:
            return "not_found"
        row = dict(found)

        if row["status"] == "pending":
            if row["method"] == "pix" and _is_expired(row):
                transition_payment(
                    conn,
                    settings,
                    payment_id,
                    "pending",
                    "failed",
                    "payment.failed",
                    reason="pix_expired",
                )
                return "expired"
            updated = transition_payment(
                conn,
                settings,
                payment_id,
                "pending",
                "processing",
                "payment.processing",
                enqueue_webhook=False,
            )
            if updated is None:
                found = conn.execute("SELECT * FROM payments WHERE id = ?", (payment_id,)).fetchone()
                if found is None or found["status"] in {"succeeded", "failed"}:
                    return "ignored"
                row = dict(found)
            else:
                row = updated

        if row["status"] != "processing":
            return "ignored"

    # Authorization runs outside the write transaction: the API is never blocked
    # by simulated provider latency.
    if settings.card_authorization_delay_ms and row["method"] != "pix":
        time.sleep(settings.card_authorization_delay_ms / 1000)

    with db.transaction() as conn:
        found = conn.execute("SELECT * FROM payments WHERE id = ?", (payment_id,)).fetchone()
        if found is None:
            return "not_found"
        row = dict(found)
        if row["status"] != "processing":
            return "ignored"  # duplicate delivery: no extra transition or event

        if row["method"] == "pix":
            if _is_expired(row):
                transition_payment(
                    conn,
                    settings,
                    payment_id,
                    "processing",
                    "failed",
                    "payment.failed",
                    reason="pix_expired",
                )
                return "expired"
            approved, failure_reason, auth_code = True, None, None
        else:
            approved, failure_reason = simulated_card_outcome(row.get("card_last4") or "")
            auth_code = new_id("auth", 4).upper() if approved else None

        if approved:
            updated = transition_payment(
                conn,
                settings,
                payment_id,
                "processing",
                "succeeded",
                "payment.succeeded",
                reason=None,
                authorization_code=auth_code,
            )
        else:
            updated = transition_payment(
                conn,
                settings,
                payment_id,
                "processing",
                "failed",
                "payment.failed",
                reason=failure_reason,
                authorization_code=None,
            )

    if updated is None:
        return "ignored"
    logger.info(
        "payment processed",
        extra={"payment_id": payment_id, "method": row["method"], "status": updated["status"]},
    )
    return updated["status"]


def simulate_payment(db: Any, settings: Settings, merchant_id: str, payment_id: str) -> dict[str, Any]:
    """Simulate the PIX provider callback: pending -> processing + enqueue."""
    with db.transaction() as conn:
        row = conn.execute(
            "SELECT * FROM payments WHERE id = ? AND merchant_id = ?",
            (payment_id, merchant_id),
        ).fetchone()
        if row is None:
            raise ApiError(404, "payment_not_found", "Payment not found")
        payment = dict(row)

        if payment["method"] != "pix":
            raise ApiError(
                409,
                "payment_not_simulatable",
                "Only PIX payments can be simulated",
                details={"method": payment["method"]},
            )
        if payment["status"] in {"succeeded", "failed"}:
            raise ApiError(
                409,
                "payment_not_simulatable",
                f"Payment is already {payment['status']}",
                details={"status": payment["status"]},
            )
        if payment["status"] == "pending" and _is_expired(payment):
            raise ApiError(
                409,
                "payment_expired",
                "The PIX QR code has expired",
                details={"expires_at": payment.get("pix_expires_at")},
            )

        if payment["status"] == "pending":
            updated = transition_payment(
                conn,
                settings,
                payment_id,
                "pending",
                "processing",
                "payment.processing",
                enqueue_webhook=False,
            )
            if updated is None:
                # A concurrent worker/simulation changed the state; re-read.
                current = conn.execute(
                    "SELECT status FROM payments WHERE id = ?", (payment_id,)
                ).fetchone()
                if current is None or current["status"] in {"succeeded", "failed"}:
                    raise ApiError(409, "payment_not_simulatable", "Payment state changed")
            payment = updated or payment

        queue.enqueue(
            conn,
            queue.PAYMENT_PROCESS,
            {"payment_id": payment_id},
            dedupe_key=f"payment.process:{payment_id}:simulate",
            max_attempts=settings.queue_max_attempts,
        )

    logger.info("pix payment simulated", extra={"payment_id": payment_id})
    return {"id": payment_id, "object": "payment", "status": "processing"}


def expire_pix_payments(db: Any, settings: Settings, limit: int = 100) -> int:
    """pending PIX past its 30-minute validity becomes failed (state machine)."""
    now = iso_now()
    with db.connection() as conn:
        rows = conn.execute(
            """
            SELECT id FROM payments
            WHERE method = 'pix' AND status = 'pending'
              AND pix_expires_at IS NOT NULL AND pix_expires_at <= ?
            LIMIT ?
            """,
            (now, limit),
        ).fetchall()

    expired = 0
    for row in rows:
        with db.transaction() as conn:
            updated = transition_payment(
                conn,
                settings,
                row["id"],
                "pending",
                "failed",
                "payment.failed",
                reason="pix_expired",
            )
        if updated is not None:
            expired += 1
            logger.info("pix payment expired", extra={"payment_id": row["id"]})
    return expired
