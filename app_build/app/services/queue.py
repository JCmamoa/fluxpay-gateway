"""Durable queue backed by SQLite (ARQ-03).

Messages are inserted in the same transaction as the domain write that needs
them.  Consumers claim with a lease and ``processing`` status; a crash leaves
the message recoverable (``recover_inflight``).

The interface is intentionally tiny so a broker-backed implementation can be
swapped in without touching payment logic.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from typing import Any

from ..config import Settings
from ..utils import canonical_json, iso_after, iso_now, truncate

logger = logging.getLogger("app.queue")

PAYMENT_PROCESS = "payment.process"
AUTOMATION_EVALUATE = "automation.evaluate"
RAG_INDEX = "rag.index"
RECOMPUTE_SEGMENTS = "crm.recompute_segments"


def enqueue(
    conn: sqlite3.Connection,
    message_type: str,
    payload: dict[str, Any],
    *,
    dedupe_key: str | None = None,
    available_at: str | None = None,
    max_attempts: int = 5,
) -> bool:
    """Insert a message; returns ``False`` when it is already queued/handled.

    A previously *failed* message with the same dedupe key is revived, which
    makes "Simulate PIX" safe to retry after an operational DLQ.
    """
    now = iso_now()
    available = available_at or now
    if dedupe_key:
        cursor = conn.execute(
            """
            INSERT INTO queue_messages(
                type, payload, status, attempts, max_attempts, available_at,
                dedupe_key, created_at, updated_at
            ) VALUES (?, ?, 'pending', 0, ?, ?, ?, ?, ?)
            ON CONFLICT(dedupe_key) DO UPDATE SET
                payload = excluded.payload,
                status = 'pending',
                attempts = 0,
                max_attempts = excluded.max_attempts,
                available_at = excluded.available_at,
                locked_until = NULL,
                last_error = NULL,
                updated_at = excluded.updated_at
            WHERE queue_messages.status = 'failed'
            """,
            (
                message_type,
                canonical_json(payload),
                max_attempts,
                available,
                dedupe_key,
                now,
                now,
            ),
        )
        return cursor.rowcount == 1

    cursor = conn.execute(
        """
        INSERT INTO queue_messages(
            type, payload, status, attempts, max_attempts, available_at,
            dedupe_key, created_at, updated_at
        ) VALUES (?, ?, 'pending', 0, ?, ?, NULL, ?, ?)
        """,
        (message_type, canonical_json(payload), max_attempts, available, now, now),
    )
    return cursor.rowcount == 1


def claim_messages(
    db: Any, settings: Settings, *, limit: int | None = None
) -> list[sqlite3.Row]:
    now = iso_now()
    lease = iso_after(settings.queue_lease_seconds)
    with db.transaction() as conn:
        rows = conn.execute(
            """
            SELECT * FROM queue_messages
            WHERE status = 'pending' AND available_at <= ?
            ORDER BY available_at, id
            LIMIT ?
            """,
            (now, limit or settings.queue_batch_size),
        ).fetchall()
        for row in rows:
            conn.execute(
                """
                UPDATE queue_messages
                SET status = 'processing', attempts = attempts + 1,
                    locked_until = ?, updated_at = ?
                WHERE id = ? AND status = 'pending'
                """,
                (lease, now, row["id"]),
            )
        return [dict(row) | {"attempts": row["attempts"] + 1} for row in rows]


def mark_done(db: Any, message_id: int) -> None:
    now = iso_now()
    with db.transaction() as conn:
        conn.execute(
            "UPDATE queue_messages SET status = 'done', locked_until = NULL, updated_at = ? "
            "WHERE id = ? AND status = 'processing'",
            (now, message_id),
        )


def mark_failed(db: Any, settings: Settings, message_id: int, error: str) -> str:
    """Retry with a short exponential backoff, or park the message as failed."""
    now = iso_now()
    with db.transaction() as conn:
        row = conn.execute("SELECT * FROM queue_messages WHERE id = ?", (message_id,)).fetchone()
        if row is None or row["status"] != "processing":
            return "ignored"
        attempts = int(row["attempts"])
        if attempts >= int(row["max_attempts"]):
            conn.execute(
                "UPDATE queue_messages SET status = 'failed', last_error = ?, "
                "locked_until = NULL, updated_at = ? WHERE id = ?",
                (truncate(error), now, message_id),
            )
            logger.error(
                "queue message exhausted retries",
                extra={"queue": row["type"], "attempt": attempts},
            )
            return "failed"
        delay = min(300.0, 2.0**attempts)
        conn.execute(
            "UPDATE queue_messages SET status = 'pending', last_error = ?, "
            "available_at = ?, locked_until = NULL, updated_at = ? WHERE id = ?",
            (truncate(error), iso_after(delay), now, message_id),
        )
        logger.warning(
            "queue message retry scheduled",
            extra={"queue": row["type"], "attempt": attempts, "retry_in_seconds": delay},
        )
        return "retry"


def recover_inflight(db: Any) -> int:
    """At startup nothing owns a lease: requeue everything left in processing."""
    now = iso_now()
    with db.transaction() as conn:
        cursor = conn.execute(
            "UPDATE queue_messages SET status = 'pending', locked_until = NULL, updated_at = ? "
            "WHERE status = 'processing'",
            (now,),
        )
        recovered = cursor.rowcount
        # Clear stale webhook leases too; due deliveries will be picked up again.
        conn.execute(
            "UPDATE webhook_deliveries SET locked_until = NULL WHERE status = 'pending'"
        )
    if recovered:
        logger.warning("recovered in-flight queue messages", extra={"queue": recovered})
    return recovered


def queue_stats(db: Any) -> dict[str, int]:
    with db.connection() as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) AS total FROM queue_messages GROUP BY status"
        ).fetchall()
    stats = {"pending": 0, "processing": 0, "done": 0, "failed": 0}
    for row in rows:
        stats[row["status"]] = row["total"]
    return stats


def payload_of(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    return json.loads(dict(row)["payload"])
