"""Operational metrics derived from the database (RNF-04)."""

from __future__ import annotations

from typing import Any

from ..utils import iso_now


def snapshot(db: Any, *, started_at: float, now: float) -> dict[str, Any]:
    with db.connection() as conn:
        payment_rows = conn.execute(
            "SELECT status, method, COUNT(*) AS total FROM payments GROUP BY status, method"
        ).fetchall()
        queue_rows = conn.execute(
            "SELECT status, COUNT(*) AS total FROM queue_messages GROUP BY status"
        ).fetchall()
        delivery_rows = conn.execute(
            "SELECT status, COUNT(*) AS total FROM webhook_deliveries GROUP BY status"
        ).fetchall()
        events_total = conn.execute("SELECT COUNT(*) FROM payment_events").fetchone()[0]
        attempts_total = conn.execute("SELECT COUNT(*) FROM webhook_attempts").fetchone()[0]
        endpoints_total = conn.execute(
            "SELECT COUNT(*) FROM webhook_endpoints WHERE active = 1"
        ).fetchone()[0]

    payments_by_status = {"pending": 0, "processing": 0, "succeeded": 0, "failed": 0}
    payments_by_method = {"pix": 0, "credit_card": 0, "debit_card": 0}
    for row in payment_rows:
        payments_by_status[row["status"]] = payments_by_status.get(row["status"], 0) + row["total"]
        payments_by_method[row["method"]] = payments_by_method.get(row["method"], 0) + row["total"]

    queue_by_status = {"pending": 0, "processing": 0, "done": 0, "failed": 0}
    for row in queue_rows:
        queue_by_status[row["status"]] = row["total"]

    deliveries_by_status = {"pending": 0, "delivered": 0, "failed": 0}
    for row in delivery_rows:
        deliveries_by_status[row["status"]] = row["total"]

    return {
        "generated_at": iso_now(),
        "uptime_seconds": max(0, int(now - started_at)),
        "payments": {
            "total": sum(payments_by_status.values()),
            "by_status": payments_by_status,
            "by_method": payments_by_method,
        },
        "queue": {
            "by_status": queue_by_status,
            "pending": queue_by_status["pending"],
            "processing": queue_by_status["processing"],
        },
        "webhooks": {
            "deliveries_by_status": deliveries_by_status,
            "attempts_total": attempts_total,
            "active_endpoints": endpoints_total,
        },
        "payment_events_total": events_total,
    }
