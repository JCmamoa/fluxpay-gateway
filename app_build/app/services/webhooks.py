"""Webhook event construction, HMAC signing, durable deliveries, retries & DLQ.

``webhook_deliveries`` is itself the durable webhook queue: a row is created in
the same transaction as the payment status change (transactional outbox), then
claimed by the dispatcher via a lease.  Delivery is at-least-once; merchants are
expected to de-duplicate by ``X-Event-Id``.
"""

from __future__ import annotations

import json
import logging
import random
import sqlite3
import time
from datetime import datetime, timezone
from typing import Any

import httpx

from ..config import Settings
from ..security import sign_webhook
from ..utils import canonical_json, iso_after, iso_now, new_id, parse_iso, truncate
from . import events

logger = logging.getLogger("app.webhooks")

WEBHOOK_EVENT_TYPES = {"payment.pending", "payment.succeeded", "payment.failed"}


# --------------------------------------------------------------------------- #
# Serialization
# --------------------------------------------------------------------------- #
def public_payment(
    row: sqlite3.Row | dict[str, Any], *, include_qr_image: bool = True
) -> dict[str, Any]:
    """Public API representation (never exposes PAN/CVV)."""
    payment = dict(row)
    metadata: Any = {}
    raw_metadata = payment.get("metadata") or "{}"
    try:
        metadata = json.loads(raw_metadata)
    except (TypeError, ValueError):
        metadata = {}

    payload: dict[str, Any] = {
        "id": payment["id"],
        "object": "payment",
        "status": payment["status"],
        "method": payment["method"],
        "amount": payment["amount_cents"],
        "currency": payment["currency"],
        "description": payment.get("description"),
        "customer": (
            {
                "name": payment.get("customer_name"),
                "email": payment.get("customer_email"),
            }
            if payment.get("customer_name") or payment.get("customer_email")
            else None
        ),
        "metadata": metadata,
        "authorization_code": payment.get("authorization_code"),
        "failure_reason": payment.get("failure_reason"),
        "created_at": payment["created_at"],
        "updated_at": payment["updated_at"],
    }

    if payment["method"] == "pix":
        pix_payload = payment.get("pix_qr_code")
        pix: dict[str, Any] = {
            "txid": payment.get("pix_txid"),
            "qr_code": pix_payload,
            "expires_at": payment.get("pix_expires_at"),
        }
        if pix_payload and include_qr_image:
            from ..pix import pix_qr_image_data_uri

            image = pix_qr_image_data_uri(pix_payload)
            if image:
                pix["qr_code_image"] = image
        payload["pix"] = pix
    elif payment.get("card_last4"):
        payload["card"] = {
            "brand": payment.get("card_brand"),
            "last4": payment.get("card_last4"),
        }

    return payload


def webhook_payment_snapshot(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    """Compact, non-sensitive payment object attached to webhook events."""
    payment = dict(row)
    snapshot: dict[str, Any] = {
        "id": payment["id"],
        "object": "payment",
        "status": payment["status"],
        "method": payment["method"],
        "amount": payment["amount_cents"],
        "currency": payment["currency"],
        "description": payment.get("description"),
        "created_at": payment["created_at"],
        "updated_at": payment["updated_at"],
        "authorization_code": payment.get("authorization_code"),
        "failure_reason": payment.get("failure_reason"),
    }
    if payment.get("card_last4"):
        snapshot["card"] = {"brand": payment.get("card_brand"), "last4": payment.get("card_last4")}
    if payment["method"] == "pix":
        snapshot["pix"] = {
            "txid": payment.get("pix_txid"),
            "expires_at": payment.get("pix_expires_at"),
        }
    return snapshot


# --------------------------------------------------------------------------- #
# Event + outbox (caller owns the transaction)
# --------------------------------------------------------------------------- #
def emit_payment_event(
    conn: sqlite3.Connection,
    settings: Settings,
    payment: sqlite3.Row | dict[str, Any],
    *,
    event_type: str,
    from_status: str | None,
    reason: str | None = None,
    enqueue_webhook: bool = True,
) -> tuple[str, int]:
    """Append an immutable audit event and (optionally) webhook deliveries.

    Must run inside the same transaction as the status CAS. Returns
    ``(event_id, deliveries_created)``.
    """
    row = dict(payment)
    event_id = new_id("evt")
    created_at = row["updated_at"] if event_type != "payment.pending" else row["created_at"]

    audit_payload = {
        "from_status": from_status,
        "to_status": row["status"],
        "reason": reason,
        "webhook_enqueued": bool(enqueue_webhook),
    }
    conn.execute(
        "INSERT INTO payment_events(id, payment_id, type, payload, created_at) VALUES (?, ?, ?, ?, ?)",
        (event_id, row["id"], event_type, canonical_json(audit_payload), created_at),
    )

    # Central event hub: every payment lifecycle change is auditable and gets
    # broadcast live (RF-14). Fan-out enqueues automation/RAG consumers.
    fanout = settings.event_fanout
    events.record_event(
        conn,
        event_type,
        {
            "payment_id": row["id"],
            "status": row["status"],
            "method": row["method"],
            "amount_cents": row["amount_cents"],
            "customer_email": row.get("customer_email"),
            "from_status": from_status,
            "to_status": row["status"],
            "reason": reason,
        },
        fanout=fanout,
    )
    # CRM ripple: attach every transition to the customer timeline (the
    # customer is created on the payment itself; here we only record events).
    payment_dict = dict(row)
    if payment_dict.get("customer_email"):
        from .crm import record_customer_event_from_payment

        record_customer_event_from_payment(
            conn, payment_dict, event_type=event_type, fanout=fanout
        )

    created = 0
    if event_type in WEBHOOK_EVENT_TYPES and enqueue_webhook:
        body = canonical_json(
            {
                "id": event_id,
                "type": event_type,
                "created_at": created_at,
                "api_version": "2026-10-26",
                "data": {"payment": webhook_payment_snapshot(row)},
            }
        )
        endpoints = conn.execute(
            "SELECT id FROM webhook_endpoints WHERE merchant_id = ? AND active = 1",
            (row["merchant_id"],),
        ).fetchall()
        for endpoint in endpoints:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO webhook_deliveries(
                    id, endpoint_id, payment_id, event_id, event_type, payload,
                    status, attempts, next_attempt_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'pending', 0, ?, ?, ?)
                """,
                (
                    new_id("whd"),
                    endpoint["id"],
                    row["id"],
                    event_id,
                    event_type,
                    body,
                    created_at,
                    created_at,
                    created_at,
                ),
            )
            created += max(cursor.rowcount, 0)

    logger.info(
        "payment event recorded",
        extra={"event_id": event_id, "payment_id": row["id"], "event_type": event_type},
    )
    return event_id, created


# --------------------------------------------------------------------------- #
# Dispatching
# --------------------------------------------------------------------------- #
def retry_delay(settings: Settings, failed_attempts: int) -> float:
    """Seconds to wait after ``failed_attempts`` failures, with jitter."""
    schedule = settings.webhook_retry_schedule
    if not schedule:
        return 0.0
    index = min(max(failed_attempts - 1, 0), len(schedule) - 1)
    base = schedule[index]
    if base <= 0:
        return 0.0
    ratio = max(0.0, settings.webhook_jitter_ratio)
    low, high = max(0.0, 1.0 - ratio), 1.0 + ratio
    return max(0.0, base * random.uniform(low, high))


def _claim_deliveries(db: Any, settings: Settings, limit: int) -> list[sqlite3.Row]:
    now = iso_now()
    lease = iso_after(settings.webhook_lease_seconds)
    with db.transaction() as conn:
        rows = conn.execute(
            """
            SELECT d.*, e.url AS endpoint_url, e.secret AS endpoint_secret, e.active AS endpoint_active
            FROM webhook_deliveries d
            JOIN webhook_endpoints e ON e.id = d.endpoint_id
            WHERE d.status = 'pending'
              AND d.next_attempt_at IS NOT NULL
              AND d.next_attempt_at <= ?
              AND (d.locked_until IS NULL OR d.locked_until <= ?)
            ORDER BY d.next_attempt_at, d.created_at
            LIMIT ?
            """,
            (now, now, limit),
        ).fetchall()
        for row in rows:
            conn.execute(
                "UPDATE webhook_deliveries "
                "SET locked_until = ?, attempts = attempts + 1, updated_at = ? "
                "WHERE id = ? AND status = 'pending'",
                (lease, now, row["id"]),
            )
    return rows


def dispatch_once(
    db: Any,
    settings: Settings,
    *,
    limit: int | None = None,
    client: httpx.Client | None = None,
) -> int:
    """Deliver all currently due webhooks. Returns the number attempted."""
    batch = _claim_deliveries(db, settings, limit or settings.webhook_batch_size)
    if not batch:
        return 0

    owns_client = client is None
    http_client = client or httpx.Client(
        timeout=settings.webhook_timeout_seconds,
        follow_redirects=False,
        headers={"User-Agent": "FluxPay-Gateway/1.0"},
    )
    try:
        for row in batch:
            try:
                _attempt_delivery(db, settings, row, http_client)
            except Exception:  # noqa: BLE001 - one bad delivery must not kill the loop
                logger.exception(
                    "unexpected webhook dispatch error",
                    extra={"delivery_id": row["id"], "event_id": row["event_id"]},
                )
    finally:
        if owns_client:
            http_client.close()
    return len(batch)


def _attempt_delivery(
    db: Any,
    settings: Settings,
    row: sqlite3.Row,
    client: httpx.Client,
) -> None:
    delivery = dict(row)
    attempt_no = int(delivery["attempts"]) + 1
    success = False
    response_code: int | None = None
    error: str | None = None
    started = time.perf_counter()

    if not delivery["endpoint_active"]:
        error = "endpoint_inactive"
        exit_early = True
    else:
        exit_early = False
        body = delivery["payload"].encode("utf-8")
        timestamp = int(time.time())
        signature = sign_webhook(delivery["endpoint_secret"], timestamp, body)
        headers = {
            "Content-Type": "application/json",
            "X-Event-Id": delivery["event_id"],
            "X-Signature": f"t={timestamp},v1={signature}",
            "X-Webhook-Attempt": str(attempt_no),
            "Idempotency-Key": delivery["event_id"],
        }
        try:
            response = client.post(
                delivery["endpoint_url"],
                content=body,
                headers=headers,
                timeout=settings.webhook_timeout_seconds,
            )
            response_code = response.status_code
            success = 200 <= response.status_code < 300
            if not success:
                error = f"http_{response.status_code}"
        except httpx.HTTPError as exc:
            error = truncate(f"{type(exc).__name__}: {exc}", 400)

    duration_ms = int((time.perf_counter() - started) * 1000)
    now = iso_now()

    # Payment context for hub events and automation conditions.
    context: dict[str, Any] = {}
    try:
        snapshot = (json.loads(delivery["payload"]) or {}).get("data", {}).get("payment") or {}
        context.update(
            {
                "method": snapshot.get("method"),
                "amount_cents": snapshot.get("amount"),
                "customer_email": snapshot.get("customer_email"),
            }
        )
    except (TypeError, ValueError):
        pass
    hub_payload = {
        "delivery_id": delivery["id"],
        "event_id": delivery["event_id"],
        "payment_id": delivery["payment_id"],
        "endpoint_url": delivery["endpoint_url"],
        "attempt_no": attempt_no,
        "attempts": int(delivery["attempts"]),
        "response_code": response_code,
        "error": error,
        **context,
    }

    with db.transaction() as conn:
        conn.execute(
            """
            INSERT INTO webhook_attempts(
                delivery_id, attempt_no, response_code, error, duration_ms, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (delivery["id"], attempt_no, response_code, error, duration_ms, now),
        )
        if success:
            events.record_event(conn, "webhook.delivered", hub_payload, fanout=settings.event_fanout)
            conn.execute(
                """
                UPDATE webhook_deliveries
                SET status = 'delivered', last_response_code = ?, last_error = NULL,
                    next_attempt_at = NULL, locked_until = NULL, delivered_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (response_code, now, now, delivery["id"]),
            )
            logger.info(
                "webhook delivered",
                extra={
                    "delivery_id": delivery["id"],
                    "event_id": delivery["event_id"],
                    "attempt": attempt_no,
                    "http_status": response_code,
                },
            )
        else:
            exhausted = (
                attempt_no >= settings.webhook_max_attempts
                or error == "endpoint_inactive"
            )
            if exhausted:
                conn.execute(
                    """
                    UPDATE webhook_deliveries
                    SET status = 'failed', last_response_code = ?, last_error = ?,
                        next_attempt_at = NULL, locked_until = NULL, updated_at = ?
                    WHERE id = ?
                    """,
                    (response_code, error, now, delivery["id"]),
                )
                events.record_event(conn, "webhook.failed", hub_payload, fanout=settings.event_fanout)
                logger.warning(
                    "webhook moved to DLQ",
                    extra={
                        "delivery_id": delivery["id"],
                        "event_id": delivery["event_id"],
                        "attempt": attempt_no,
                        "http_status": response_code,
                    },
                )
            else:
                delay = retry_delay(settings, attempt_no)
                conn.execute(
                    """
                    UPDATE webhook_deliveries
                    SET last_response_code = ?, last_error = ?, next_attempt_at = ?,
                        locked_until = NULL, updated_at = ?
                    WHERE id = ?
                    """,
                    (response_code, error, iso_after(delay), now, delivery["id"]),
                )
                logger.warning(
                    "webhook attempt failed; retry scheduled",
                    extra={
                        "delivery_id": delivery["id"],
                        "event_id": delivery["event_id"],
                        "attempt": attempt_no,
                        "http_status": response_code,
                        "retry_in_seconds": round(delay, 2),
                    },
                )


def next_retry_iso(settings: Settings, payment_created_at: str, attempt_no: int) -> str:
    """Exposed for tests/tools: when would the next attempt happen?"""
    return iso_after(retry_delay(settings, attempt_no), base=parse_iso(payment_created_at))


def now_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()
