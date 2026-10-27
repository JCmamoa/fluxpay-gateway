"""Merchant-facing webhook endpoint / delivery administration."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from ..errors import ApiError
from ..utils import iso_now


def _endpoint_public(row: sqlite3.Row | dict[str, Any], *, reveal_secret: bool = True) -> dict[str, Any]:
    endpoint = dict(row)
    return {
        "id": endpoint["id"],
        "object": "webhook_endpoint",
        "url": endpoint["url"],
        "secret": endpoint["secret"] if reveal_secret else None,
        "active": bool(endpoint["active"]),
        "created_at": endpoint["created_at"],
    }


def list_endpoints(db: Any, merchant_id: str) -> list[dict[str, Any]]:
    with db.connection() as conn:
        rows = conn.execute(
            "SELECT * FROM webhook_endpoints WHERE merchant_id = ? ORDER BY created_at",
            (merchant_id,),
        ).fetchall()
    return [_endpoint_public(row) for row in rows]


def update_endpoint(
    db: Any,
    merchant_id: str,
    endpoint_id: str,
    *,
    url: str | None,
    active: bool | None,
) -> dict[str, Any]:
    with db.transaction() as conn:
        row = conn.execute(
            "SELECT * FROM webhook_endpoints WHERE id = ? AND merchant_id = ?",
            (endpoint_id, merchant_id),
        ).fetchone()
        if row is None:
            raise ApiError(404, "webhook_endpoint_not_found", "Webhook endpoint not found")
        if url is not None:
            conn.execute("UPDATE webhook_endpoints SET url = ? WHERE id = ?", (url, endpoint_id))
        if active is not None:
            conn.execute(
                "UPDATE webhook_endpoints SET active = ? WHERE id = ?",
                (1 if active else 0, endpoint_id),
            )
        updated = conn.execute(
            "SELECT * FROM webhook_endpoints WHERE id = ?", (endpoint_id,)
        ).fetchone()
    return _endpoint_public(updated)


def _delivery_public(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    delivery = dict(row)
    payload: Any
    try:
        payload = json.loads(delivery["payload"])
    except (TypeError, ValueError):
        payload = None
    result = {
        "id": delivery["id"],
        "object": "webhook_delivery",
        "endpoint_id": delivery["endpoint_id"],
        "endpoint_url": delivery.get("endpoint_url"),
        "payment_id": delivery["payment_id"],
        "event_id": delivery["event_id"],
        "event_type": delivery["event_type"],
        "status": delivery["status"],
        "attempts": delivery["attempts"],
        "next_attempt_at": delivery["next_attempt_at"],
        "last_response_code": delivery["last_response_code"],
        "last_error": delivery["last_error"],
        "delivered_at": delivery["delivered_at"],
        "created_at": delivery["created_at"],
        "updated_at": delivery["updated_at"],
        "event": payload,
    }
    return result


def list_deliveries(
    db: Any,
    merchant_id: str,
    *,
    status: str | None = None,
    payment_id: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    clauses = ["e.merchant_id = ?"]
    params: list[Any] = [merchant_id]
    if status:
        clauses.append("d.status = ?")
        params.append(status)
    if payment_id:
        clauses.append("d.payment_id = ?")
        params.append(payment_id)
    query = (
        "SELECT d.*, e.url AS endpoint_url FROM webhook_deliveries d "
        "JOIN webhook_endpoints e ON e.id = d.endpoint_id "
        f"WHERE {' AND '.join(clauses)} "
        "ORDER BY d.created_at DESC, d.id DESC LIMIT ?"
    )
    params.append(limit)
    with db.connection() as conn:
        rows = conn.execute(query, params).fetchall()
    return {
        "object": "list",
        "data": [_delivery_public(row) for row in rows],
        "has_more": False,
    }


def get_delivery(db: Any, merchant_id: str, delivery_id: str) -> dict[str, Any]:
    with db.connection() as conn:
        row = conn.execute(
            """
            SELECT d.*, e.url AS endpoint_url
            FROM webhook_deliveries d
            JOIN webhook_endpoints e ON e.id = d.endpoint_id
            WHERE d.id = ? AND e.merchant_id = ?
            """,
            (delivery_id, merchant_id),
        ).fetchone()
        if row is None:
            raise ApiError(404, "webhook_delivery_not_found", "Webhook delivery not found")
        attempts = conn.execute(
            "SELECT attempt_no, response_code, error, duration_ms, created_at "
            "FROM webhook_attempts WHERE delivery_id = ? ORDER BY id",
            (delivery_id,),
        ).fetchall()
    payload = _delivery_public(row)
    payload["attempt_log"] = [dict(attempt) for attempt in attempts]
    return payload


def retry_delivery(db: Any, merchant_id: str, delivery_id: str) -> dict[str, Any]:
    """Manual requeue for a delivery parked in the DLQ (demo/ops helper)."""
    with db.transaction() as conn:
        row = conn.execute(
            """
            SELECT d.* FROM webhook_deliveries d
            JOIN webhook_endpoints e ON e.id = d.endpoint_id
            WHERE d.id = ? AND e.merchant_id = ?
            """,
            (delivery_id, merchant_id),
        ).fetchone()
        if row is None:
            raise ApiError(404, "webhook_delivery_not_found", "Webhook delivery not found")
        if row["status"] == "delivered":
            raise ApiError(409, "delivery_already_delivered", "This delivery already succeeded")
        now = iso_now()
        conn.execute(
            """
            UPDATE webhook_deliveries
            SET status = 'pending', attempts = 0, next_attempt_at = ?, locked_until = NULL,
                last_error = NULL, last_response_code = NULL, updated_at = ?
            WHERE id = ?
            """,
            (now, now, delivery_id),
        )
    return get_delivery(db, merchant_id, delivery_id)


def delivery_counts(db: Any, merchant_id: str) -> dict[str, int]:
    with db.connection() as conn:
        rows = conn.execute(
            """
            SELECT d.status, COUNT(*) AS total
            FROM webhook_deliveries d
            JOIN webhook_endpoints e ON e.id = d.endpoint_id
            WHERE e.merchant_id = ?
            GROUP BY d.status
            """,
            (merchant_id,),
        ).fetchall()
    counts = {"pending": 0, "delivered": 0, "failed": 0}
    for row in rows:
        counts[row["status"]] = row["total"]
    return counts
