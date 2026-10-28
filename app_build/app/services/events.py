"""Central event hub (RF-14).

Every relevant domain change lands in ``event_stream`` — an append-only table
(like ``payment_events``) that feeds:

* the WebSocket broadcaster ``WS /ws/events`` (notifications only);
* the REST snapshot ``GET /v1/events`` (source of truth for reconciliation);
* the CRM ``customer_events`` timeline and the automation engine (consumers).

Recording happens inside the caller's transaction so the audit row and the
domain write commit atomically.  Ordering relies on the implicit SQLite
``rowid`` (exposed as ``seq``): ``TEXT`` ids are random, ``seq`` is monotonic.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import timedelta
from typing import Any

from ..errors import ApiError
from ..utils import iso_now, parse_iso, utc_now, new_id

logger = logging.getLogger("app.events")

# Namespaces — new event families must be registered here so integrators can
# rely on stable prefixes (payment.* and webhook.* existed in v0.2).
EVENT_PREFIXES = ("payment.", "webhook.", "crm.", "automation.", "agent.")

MAX_PAYLOAD_CHARS = 8_000


def _compact(value: Any) -> Any:
    """Keep payloads broadcast-friendly: bounded and JSON-safe."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        return {key: _compact(item) for key, item in list(value.items())[:40]}
    if isinstance(value, (list, tuple)):
        return [_compact(item) for item in list(value)[:40]]
    return str(value)[:200]


def record_event(
    conn: sqlite3.Connection,
    event_type: str,
    payload: dict[str, Any] | None = None,
    *,
    event_id: str | None = None,
    fanout: bool = True,
) -> str:
    """Append one event to the hub and enqueue its downstream consumers.

    Must run inside the caller's transaction so the audit row, the domain write
    and the queue messages commit atomically (outbox pattern).  Returns the
    public event id (also used for ``WS ?after=`` reconnects).
    """
    if not event_type or not event_type.startswith(EVENT_PREFIXES):
        raise ValueError(f"event type must use a known namespace: {event_type}")
    eid = event_id or new_id("evt")
    data = _compact(payload or {})
    body = json.dumps(data, ensure_ascii=False, default=str)
    if len(body) > MAX_PAYLOAD_CHARS:
        body = body[: MAX_PAYLOAD_CHARS - 1]
    conn.execute(
        "INSERT INTO event_stream(id, type, payload, created_at) VALUES (?, ?, ?, ?)",
        (eid, event_type, body, iso_now()),
    )
    if fanout:
        _fanout_consumers(conn, event_type, eid, payload or {})
    return eid


def _fanout_consumers(
    conn: sqlite3.Connection, event_type: str, event_id: str, data: dict[str, Any]
) -> None:
    """Enqueue durable consumers for the event (same transaction = outbox)."""
    from . import queue as queue_service

    if not event_type.startswith(("automation.", "agent.")):
        queue_service.enqueue(
            conn,
            queue_service.AUTOMATION_EVALUATE,
            {"event_id": event_id},
            dedupe_key=f"automation.evaluate:{event_id}",
            max_attempts=5,
        )

    if event_type.startswith("payment."):
        source_ref = data.get("payment_id") or data.get("id")
        if source_ref:
            queue_service.enqueue(
                conn,
                queue_service.RAG_INDEX,
                {"source_type": "payment", "source_ref": source_ref},
                dedupe_key=f"rag.index:payment:{source_ref}",
                max_attempts=5,
            )
    elif event_type.startswith(("webhook.", "crm.")):
        source_ref = data.get("payment_id") or data.get("customer_email") or data.get("customer_id")
        if source_ref:
            source_type = "payment" if event_type.startswith("webhook.") else "customer"
            queue_service.enqueue(
                conn,
                queue_service.RAG_INDEX,
                {"source_type": source_type, "source_ref": str(source_ref)},
                dedupe_key=f"rag.index:{source_type}:{source_ref}",
                max_attempts=5,
            )


def _event_public(row: sqlite3.Row) -> dict[str, Any]:
    try:
        payload = json.loads(row["payload"])
    except (TypeError, ValueError):
        payload = {}
    return {
        "id": row["id"],
        "seq": row["rowid"],
        "object": "event",
        "type": row["type"],
        "data": payload,
        "created_at": row["created_at"],
    }


def _rowid_for(conn: sqlite3.Connection, after: str | None) -> int:
    """Resolve a public event id to its ordering position (0 = from the start)."""
    if not after:
        return 0
    row = conn.execute("SELECT rowid FROM event_stream WHERE id = ?", (after,)).fetchone()
    if row is None:
        raise ValueError(f"unknown event id: {after}")
    return int(row[0])


def list_events(
    db: Any,
    *,
    after: str | None = None,
    types: list[str] | None = None,
    limit: int = 100,
    order: str = "desc",
) -> dict[str, Any]:
    """REST snapshot of the hub (REST is the source of truth; WS only notifies)."""
    if after is not None:
        after = after.strip() or None
    with db.connection() as conn:
        try:
            low = _rowid_for(conn, after)
        except ValueError as exc:
            raise ApiError(404, "event_not_found", str(exc)) from exc
        clauses: list[str] = []
        params: list[Any] = []
        if types:
            # Namespace filter: 'payment' matches every payment.* type.
            prefix_clauses = []
            for entry in types[:20] or [""]:
                entry = entry.strip()
                if not entry:
                    continue
                prefix_clauses.append("(type = ? OR type LIKE ?)")
                params.extend([entry, entry.rstrip(".") + ".%"])
            if prefix_clauses:
                clauses.append("(" + " OR ".join(prefix_clauses) + ")")
        where = f"WHERE {' AND '.join(clauses)} " if clauses else ""
        ordering = "DESC" if order.lower() != "asc" else "ASC"
        rows = conn.execute(
            f"SELECT rowid, * FROM event_stream {where}"
            f"{' AND ' if clauses else 'WHERE '}rowid > ? "
            f"ORDER BY rowid {ordering} LIMIT ?",
            (*params, low, limit),
        ).fetchall()
    events = [_event_public(row) for row in rows]
    if ordering == "DESC":
        events.reverse()
    return {
        "object": "list",
        "data": events,
        "has_more": len(events) >= limit,
        "last_event_id": events[-1]["id"] if events else after,
    }


def events_since(db: Any, last_seq: int, limit: int = 200) -> tuple[list[dict[str, Any]], int]:
    """(events, max_seq) strictly after ``last_seq`` — broadcaster fast path."""
    with db.connection() as conn:
        rows = conn.execute(
            "SELECT rowid, * FROM event_stream WHERE rowid > ? ORDER BY rowid LIMIT ?",
            (last_seq, limit),
        ).fetchall()
    if not rows:
        return [], last_seq
    return [_event_public(row) for row in rows], int(rows[-1]["rowid"])


def max_seq(db: Any) -> int:
    """Current event-stream tail rowid (0 when empty)."""
    with db.connection() as conn:
        row = conn.execute("SELECT COALESCE(MAX(rowid), 0) FROM event_stream").fetchone()
    return int(row[0])


def retention_delete(db: Any, days: int) -> int:
    """Retention sweep: drop events older than ``days`` (append-only exception)."""
    if days <= 0:
        return 0
    cutoff = (utc_now() - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    with db.transaction() as conn:
        cursor = conn.execute(
            "DELETE FROM event_stream WHERE created_at < ? AND id NOT IN "
            "(SELECT id FROM event_stream ORDER BY rowid DESC LIMIT 100)",
            (cutoff,),
        )
        deleted = cursor.rowcount
    if deleted:
        logger.info("event_stream retention applied", extra={"deleted": deleted, "days": days})
    return deleted
