"""CRM domain service (RF-12): customers, 360° view, notes, tags, segments.

Customers are created automatically on the first payment that carries a
``customer.email``; the e-mail is the natural key, matched case-insensitively
(CA-13).  The 360° view joins payments (by lowercased e-mail), the
``customer_events`` timeline, tags, notes and live segment memberships.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from ..errors import ApiError
from ..utils import canonical_json, iso_now, new_id, parse_iso, utc_now
from . import events as events_hub

logger = logging.getLogger("app.crm")

ISO_FORMAT_LENGTH = len("2026-10-27T12:00:00.000000Z")


# --------------------------------------------------------------------------- #
# Customers
# --------------------------------------------------------------------------- #
def normalize_email(email: str | None) -> str:
    return (email or "").strip().lower()


def _customer_public(row: Any) -> dict[str, Any]:
    return {
        "id": row["id"],
        "object": "customer",
        "email": row["email"],
        "name": row["name"],
        "phone": row["phone"],
        "lifecycle_status": row["lifecycle_status"],
        "first_seen_at": row["first_seen_at"],
        "last_seen_at": row["last_seen_at"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def customer_for_email(conn: Any, email: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM customers WHERE LOWER(email) = ?", (normalize_email(email),)
    ).fetchone()
    return dict(row) if row else None


def customer_by_id(db: Any, customer_id: str) -> dict[str, Any]:
    with db.connection() as conn:
        row = conn.execute("SELECT * FROM customers WHERE id = ?", (customer_id,)).fetchone()
    if row is None:
        raise ApiError(404, "customer_not_found", "Customer not found")
    return dict(row)


def ensure_customer(
    conn: Any,
    email: str | None,
    name: str | None = None,
    *,
    fanout: bool = True,
) -> dict[str, Any] | None:
    """Create-or-fetch a customer inside the caller's transaction.

    Idempotent and case-insensitive: the ``LOWER(email)`` unique index makes
    concurrent inserts safe.  Enqueues the segment/RAG ripple (queue) and
    records ``crm.customer.created`` on the hub only the first time.
    """
    normalized = normalize_email(email)
    if not normalized or "@" not in normalized:
        return None
    now = iso_now()
    existing = customer_for_email(conn, normalized)
    if existing is None:
        customer_id = new_id("cus")
        conn.execute(
            """
            INSERT INTO customers(
                id, email, name, lifecycle_status, first_seen_at, last_seen_at,
                created_at, updated_at
            ) VALUES (?, ?, ?, 'new', ?, ?, ?, ?)
            """,
            (customer_id, normalized, name or None, now, now, now, now),
        )
        events_hub.record_event(
            conn,
            "crm.customer.created",
            {"customer_id": customer_id, "email": normalized},
            fanout=fanout,
        )
        append_customer_event(conn, customer_id, "customer.created", {"source": "payment"})
        created = get_customer(conn, customer_id)
        logger.info("customer auto-created", extra={"customer_id": customer_id})
        queue_segment_recompute(conn, fanout=fanout)
        return created

    updated_name = (name or "").strip()
    if updated_name and not (existing.get("name") or "").strip():
        conn.execute(
            "UPDATE customers SET name = ?, updated_at = ? WHERE id = ?",
            (updated_name, now, existing["id"]),
        )
        existing = dict(existing, name=updated_name)
    conn.execute(
        "UPDATE customers SET last_seen_at = ?, updated_at = ? WHERE id = ?",
        (now, now, existing["id"]),
    )
    return customer_for_email(conn, normalized)


def get_customer(conn: Any, customer_id: str) -> dict[str, Any]:    return dict(conn.execute("SELECT * FROM customers WHERE id = ?", (customer_id,)).fetchone())


def append_customer_event(
    conn: Any, customer_id: str, event_type: str, payload: dict[str, Any] | None = None
) -> str:
    event_id = new_id("cev")
    conn.execute(
        "INSERT INTO customer_events(id, customer_id, type, payload, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (event_id, customer_id, event_type, canonical_json(payload or {}), iso_now()),
    )
    return event_id


def queue_segment_recompute(conn: Any, *, fanout: bool = True) -> None:
    """Debounced trickle: one pending 'crm.recompute_segments' is enough."""
    if not fanout:
        return
    from . import queue as queue_service

    queue_service.enqueue(
        conn,
        queue_service.RECOMPUTE_SEGMENTS,
        {},
        dedupe_key="crm.recompute_segments",
        max_attempts=5,
    )


def record_payment_for_customer(
    conn: Any, payment: dict[str, Any], *, event_type: str, fanout: bool = True
) -> None:
    """Attach a payment update to the customer timeline (auto-create if needed)."""
    email = normalize_email(payment.get("customer_email"))
    if not email:
        return
    customer = customer_for_email(conn, email) or ensure_customer(conn, email, None, fanout=fanout)
    if customer is None:
        return
    append_customer_event(
        conn,
        customer["id"],
        event_type,
        {
            "payment_id": payment["id"],
            "amount_cents": payment["amount_cents"],
            "method": payment["method"],
            "status": payment["status"],
            "failure_reason": payment.get("failure_reason"),
        },
    )
    now = iso_now()
    conn.execute("UPDATE customers SET last_seen_at = ?, updated_at = ? WHERE id = ?", (now, now, customer["id"]))
    if (
        payment["status"] == "succeeded"
        and customer.get("lifecycle_status") == "new"
    ):
        conn.execute(
            "UPDATE customers SET lifecycle_status = 'active', updated_at = ? WHERE id = ?",
            (now, customer["id"]),
        )
        append_customer_event(conn, customer["id"], "customer.activated", {"source": event_type})
        events_hub.record_event(
            conn, "crm.customer.activated", {"customer_id": customer["id"]}, fanout=fanout
        )


def record_customer_event_from_payment(
    conn: Any, payment: dict[str, Any], *, event_type: str, fanout: bool = True
) -> None:
    """Public wrapper used by the payment/webhook services (tolerates failures)."""
    try:
        record_payment_for_customer(conn, payment, event_type=event_type, fanout=fanout)
    except Exception as exc:  # noqa: BLE001 - CRM must never break payments
        logger.warning("customer event recording failed", extra={"error": str(exc)})


# --------------------------------------------------------------------------- #
# Notes / tags
# --------------------------------------------------------------------------- #
def add_note(db: Any, customer_id: str, body: str, author: str = "operator", *, pinned: bool = False, fanout: bool = True) -> dict[str, Any]:
    note_id = new_id("cnot")
    now = iso_now()
    with db.transaction() as conn:
        customer = _customer_row(conn, customer_id)
        conn.execute(
            "INSERT INTO customer_notes(id, customer_id, author, body, pinned, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (note_id, customer_id, author, body, 1 if pinned else 0, now),
        )
        append_customer_event(conn, customer_id, "note.created", {"note_id": note_id, "author": author})
        events_hub.record_event(
            conn,
            "crm.note.created",
            {"customer_id": customer_id, "customer_email": customer["email"], "note_id": note_id},
            fanout=fanout,
        )
    return {
        "id": note_id,
        "object": "customer_note",
        "customer_id": customer_id,
        "author": author,
        "body": body,
        "pinned": bool(pinned),
        "created_at": now,
    }


def add_tag(db: Any, customer_id: str, tag: str, *, fanout: bool = True) -> dict[str, Any]:
    tag = tag.strip().lower()
    with db.transaction() as conn:
        customer = _customer_row(conn, customer_id)
        conn.execute(
            "INSERT OR IGNORE INTO customer_tags(customer_id, tag, created_at) VALUES (?, ?, ?)",
            (customer_id, tag, iso_now()),
        )
        append_customer_event(conn, customer_id, "tag.added", {"tag": tag})
        events_hub.record_event(
            conn,
            "crm.tag.added",
            {"customer_id": customer_id, "customer_email": customer["email"], "tag": tag},
            fanout=fanout,
        )
    return {"object": "customer_tag", "customer_id": customer_id, "tag": tag}


def _customer_row(conn: Any, customer_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM customers WHERE id = ?", (customer_id,)).fetchone()
    if row is None:
        raise ApiError(404, "customer_not_found", "Customer not found")
    return dict(row)


# --------------------------------------------------------------------------- #
# Listing & 360° view
# --------------------------------------------------------------------------- #
def list_customers(
    db: Any,
    *,
    limit: int = 20,
    offset: int = 0,
    tag: str | None = None,
    segment: str | None = None,
    method: str | None = None,
    min_amount_cents: int | None = None,
    query: str | None = None,
) -> dict[str, Any]:
    clauses = []
    params: list[Any] = []
    if tag:
        clauses.append(
            "c.id IN (SELECT customer_id FROM customer_tags WHERE tag = LOWER(?))"
        )
        params.append(tag)
    if segment:
        clauses.append(
            "c.id IN ("
            " SELECT sm.customer_id FROM segment_members sm JOIN segments s ON s.id = sm.segment_id"
            " WHERE s.name = LOWER(?))"
        )
        params.append(segment)
    if method:
        clauses.append(
            "c.id IN (SELECT id FROM customers WHERE LOWER(email) IN "
            "(SELECT LOWER(customer_email) FROM payments WHERE method = ?))"
        )
        params.append(method)
    if min_amount_cents is not None:
        clauses.append(
            "c.id IN (SELECT id FROM customers WHERE LOWER(email) IN "
            "(SELECT LOWER(customer_email) FROM payments WHERE amount_cents >= ?))"
        )
        params.append(min_amount_cents)
    if query:
        clauses.append("(LOWER(c.email) LIKE ? OR LOWER(COALESCE(c.name, '')) LIKE ?)")
        like = f"%{query.lower()}%"
        params.extend([like, like])
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

    with db.connection() as conn:
        rows = conn.execute(
            "SELECT c.* FROM customers c "
            + where
            + " ORDER BY c.created_at DESC, c.id DESC LIMIT ? OFFSET ?",
            (*params, limit, offset),
        ).fetchall()
        total = conn.execute(
            "SELECT COUNT(*) FROM customers c " + where,
            params,
        ).fetchone()[0]
    data = [_customer_public(row) | _customer_stats(db, dict(row)) for row in rows]
    if data:
        emails = [customer["email"] for customer in data]
        with db.connection() as conn:
            placeholder = ",".join("?" for _ in emails)
            tag_rows = conn.execute(
                f"SELECT customer_id, tag FROM customer_tags WHERE customer_id IN "
                f"(SELECT id FROM customers WHERE LOWER(email) IN ({placeholder})) "
                "ORDER BY tag",
                [email.lower() for email in emails],
            ).fetchall()
        email_to_id = {customer["email"].lower(): customer["id"] for customer in data}
        tags_by_id: dict[str, list[str]] = {}
        for tag_row in tag_rows:
            tags_by_id.setdefault(tag_row["customer_id"], []).append(tag_row["tag"])
        for customer in data:
            customer["tags"] = tags_by_id.get(email_to_id.get(customer["email"].lower(), ""), [])
    return {
        "object": "list",
        "data": data,
        "has_more": offset + len(rows) < total,
        "total": total,
    }


def _customer_stats(db: Any, customer: dict[str, Any]) -> dict[str, Any]:
    email = normalize_email(customer.get("email"))
    with db.connection() as conn:
        row = conn.execute(
            """
            SELECT
              COUNT(*) AS payments_count,
              COALESCE(SUM(CASE WHEN status = 'succeeded' THEN 1 ELSE 0 END), 0) AS succeeded_count,
              COALESCE(SUM(CASE WHEN status = 'succeeded' THEN amount_cents ELSE 0 END), 0) AS total_succeeded_cents,
              MAX(created_at) AS last_payment_at
            FROM payments WHERE LOWER(customer_email) = ?
            """,
            (email,),
        ).fetchone()
    return {
        "payments_count": row["payments_count"],
        "succeeded_count": row["succeeded_count"],
        "total_succeeded_cents": row["total_succeeded_cents"],
        "last_payment_at": row["last_payment_at"],
    }


def customer_360(db: Any, customer_id: str) -> dict[str, Any]:
    customer = customer_by_id(db, customer_id)
    email = normalize_email(customer["email"])
    with db.connection() as conn:
        payment_rows = conn.execute(
            "SELECT * FROM payments WHERE LOWER(customer_email) = ? "
            "ORDER BY created_at DESC, id DESC LIMIT 50",
            (email,),
        ).fetchall()
        event_rows = conn.execute(
            "SELECT id, type, payload, created_at FROM customer_events "
            "WHERE customer_id = ? ORDER BY created_at DESC, id DESC LIMIT 100",
            (customer_id,),
        ).fetchall()
        tags = conn.execute(
            "SELECT tag, created_at FROM customer_tags WHERE customer_id = ? ORDER BY created_at",
            (customer_id,),
        ).fetchall()
        notes = conn.execute(
            "SELECT id, author, body, pinned, created_at FROM customer_notes "
            "WHERE customer_id = ? ORDER BY pinned DESC, created_at DESC LIMIT 50",
            (customer_id,),
        ).fetchall()
        segments = conn.execute(
            "SELECT s.id, s.name, s.definition, sm.added_at FROM segments s "
            "JOIN segment_members sm ON sm.segment_id = s.id "
            "WHERE sm.customer_id = ? ORDER BY s.name",
            (customer_id,),
        ).fetchall()
    timeline = []
    for row in event_rows:
        try:
            payload = json.loads(row["payload"])
        except (TypeError, ValueError):
            payload = {}
        timeline.append(
            {
                "id": row["id"],
                "object": "customer_event",
                "type": row["type"],
                "payload": payload,
                "created_at": row["created_at"],
            }
        )
    view = _customer_public(customer) | _customer_stats(db, customer)
    view.update(
        {
            "payments": [
                {
                    "id": row["id"],
                    "status": row["status"],
                    "method": row["method"],
                    "amount": row["amount_cents"],
                    "currency": row["currency"],
                    "description": row["description"],
                    "failure_reason": row["failure_reason"],
                    "created_at": row["created_at"],
                }
                for row in payment_rows
            ],
            "events": timeline,
            "tags": [dict(tag) for tag in tags],
            "notes": [dict(note) for note in notes],
            "segments": [
                {
                    "id": seg["id"],
                    "name": seg["name"],
                    "definition": json.loads(seg["definition"]),
                    "added_at": seg["added_at"],
                }
                for seg in segments
            ],
        }
    )
    return view


# --------------------------------------------------------------------------- #
# Segments
# --------------------------------------------------------------------------- #
def create_segment(db: Any, name: str, definition: dict[str, Any]) -> dict[str, Any]:
    name = name.strip().lower()
    sid = new_id("seg")
    now = iso_now()
    with db.transaction() as conn:
        existing = conn.execute("SELECT id FROM segments WHERE name = ?", (name,)).fetchone()
        if existing is not None:
            raise ApiError(
                409, "segment_name_conflict", "A segment with this name already exists",
                details={"segment_id": existing["id"]},
            )
        conn.execute(
            "INSERT INTO segments(id, name, definition, created_at) VALUES (?, ?, ?, ?)",
            (sid, name, canonical_json(definition), now),
        )
    recompute_segments(db, segment_ids=[sid])
    return get_segment(db, sid)


def get_segment(db: Any, segment_id: str) -> dict[str, Any]:
    with db.connection() as conn:
        row = conn.execute("SELECT * FROM segments WHERE id = ?", (segment_id,)).fetchone()
        if row is None:
            raise ApiError(404, "segment_not_found", "Segment not found")
        members = conn.execute(
            "SELECT c.id, c.email, c.name FROM segment_members sm "
            "JOIN customers c ON c.id = sm.customer_id "
            "WHERE sm.segment_id = ? ORDER BY c.email LIMIT 100",
            (segment_id,),
        ).fetchall()
    return {
        "id": row["id"],
        "object": "segment",
        "name": row["name"],
        "definition": json.loads(row["definition"]),
        "computed_at": row["computed_at"],
        "created_at": row["created_at"],
        "members": [dict(member) for member in members],
        "members_count": len(members),
    }


def list_segments(db: Any) -> dict[str, Any]:
    with db.connection() as conn:
        rows = conn.execute(
            "SELECT s.*, (SELECT COUNT(*) FROM segment_members sm WHERE sm.segment_id = s.id) "
            "AS members_count FROM segments s ORDER BY s.created_at, s.name"
        ).fetchall()
    return {
        "object": "list",
        "data": [
            {
                "id": row["id"],
                "object": "segment",
                "name": row["name"],
                "definition": json.loads(row["definition"]),
                "computed_at": row["computed_at"],
                "members_count": row["members_count"],
            }
            for row in rows
        ],
    }


def segment_matches(customer: dict[str, Any], stats: dict[str, Any], definition: dict[str, Any]) -> bool:
    """Declarative AND-combination of recency / frequency / method / volume."""
    if not definition:
        return False
    recency_max = definition.get("recency_max_days")
    if recency_max is not None:
        last_seen = stats.get("last_payment_at") or customer.get("last_seen_at")
        if not last_seen:
            return False
        delta = utc_now() - parse_iso(last_seen)
        if delta.total_seconds() > float(recency_max) * 86400:
            return False
    frequency_min = definition.get("frequency_min")
    if frequency_min is not None and int(stats.get("succeeded_count") or 0) < int(frequency_min):
        return False
    method = definition.get("method")
    if method:
        expected = normalize_email(str(method))
        if stats.get("methods") and expected not in stats["methods"]:
            return False
    min_amount = definition.get("min_amount_cents")
    if min_amount is not None and int(stats.get("total_succeeded_cents") or 0) < int(min_amount):
        return False
    return True


def recompute_segments(db: Any, *, segment_ids: list[str] | None = None) -> int:
    """Recompute matching customers for each (or all) segments. Idempotent."""
    with db.connection() as conn:
        segments = conn.execute(
            "SELECT id, name, definition FROM segments ORDER BY created_at"
        ).fetchall()
    targets = [
        dict(seg)
        for seg in segments
        if segment_ids is None or seg["id"] in segment_ids
    ]
    if not targets:
        return 0
    with db.connection() as conn:
        customer_rows = [
            dict(row)
            for row in conn.execute("SELECT * FROM customers ORDER BY created_at").fetchall()
        ]
    now = iso_now()
    changed = 0
    with db.transaction() as conn:
        for segment in targets:
            try:
                definition = json.loads(segment["definition"])
            except (TypeError, ValueError):
                continue
            members: list[str] = []
            for customer in customer_rows:
                stats = _customer_stats(db, customer)  # type: ignore[arg-type]
                if method := definition.get("method"):
                    stats["methods"] = _methods_for_email(conn, customer["email"])
                if segment_matches(customer, stats, definition):
                    members.append(customer["id"])
            before = {
                row[0]
                for row in conn.execute(
                    "SELECT customer_id FROM segment_members WHERE segment_id = ?",
                    (segment["id"],),
                ).fetchall()
            }
            conn.execute("DELETE FROM segment_members WHERE segment_id = ?", (segment["id"],))
            for customer_id in members:
                conn.execute(
                    "INSERT OR IGNORE INTO segment_members(segment_id, customer_id, added_at) "
                    "VALUES (?, ?, ?)",
                    (segment["id"], customer_id, now),
                )
            conn.execute(
                "UPDATE segments SET computed_at = ? WHERE id = ?", (now, segment["id"])
            )
            if before != set(members):
                changed += 1
                events_hub.record_event(
                    conn,
                    "crm.segment.recomputed",
                    {
                        "segment_id": segment["id"],
                        "segment_name": segment["name"],
                        "members_count": len(members),
                    },
                )
    logger.info("segments recomputed", extra={"segments": len(targets)})
    return changed


def _methods_for_email(conn: Any, email: str) -> set[str]:
    rows = conn.execute(
        "SELECT DISTINCT method FROM payments WHERE LOWER(customer_email) = ? AND status = 'succeeded'",
        (normalize_email(email),),
    ).fetchall()
    return {row[0] for row in rows}
