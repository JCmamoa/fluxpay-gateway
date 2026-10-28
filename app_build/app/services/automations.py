"""Automation engine (RF-15): rule CRUD helpers, event evaluation, actions.

Execution model (ARQ-09): payment/webhook/CRM events recorded on the hub
enqueue one durable ``automation.evaluate`` message (at-least-once); the
existing worker consumes it.  Runs are audited in ``automation_runs``
(append-only, idempotent via ``UNIQUE(rule_id, event_id)``) and mirrored to
the hub as ``automation.run`` / ``automation.alert`` events, which the console
shows live through the WebSocket.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from typing import Any

from ..config import Settings
from ..errors import ApiError
from ..utils import canonical_json, iso_now, new_id
from . import events as events_hub

logger = logging.getLogger("app.automations")

ACTION_TYPES = ("alerta_no_console", "tag_customer", "retry_webhook")

SEED_RULES = (
    {
        "id": "ar_seed_webhook_alert",
        "name": "webhook falha -> alerta no console",
        "event_type": "webhook.failed",
        "conditions": {},
        "action_type": "alerta_no_console",
        "action_config": {"message": "Webhook falhou e foi para a DLQ ({payment_id}, tentativa {attempts})."},
        "enabled": 1,
    },
)


# --------------------------------------------------------------------------- #
# Seed
# --------------------------------------------------------------------------- #
def seed_example_rules(conn: sqlite3.Connection) -> None:
    """Idempotently install the example rule ('webhook falhou -> alerta')."""
    now = iso_now()
    for rule in SEED_RULES:
        conn.execute(
            """
            INSERT INTO automation_rules(
                id, name, event_type, conditions, action_type, action_config,
                enabled, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO NOTHING
            """,
            (
                rule["id"],
                rule["name"],
                rule["event_type"],
                canonical_json(rule["conditions"]),
                rule["action_type"],
                canonical_json(rule["action_config"]),
                rule["enabled"],
                now,
                now,
            ),
        )


# --------------------------------------------------------------------------- #
# Rule CRUD helpers
# --------------------------------------------------------------------------- #
def _rule_public(row: Any) -> dict[str, Any]:
    return {
        "id": row["id"],
        "object": "automation_rule",
        "name": row["name"],
        "event_type": row["event_type"],
        "conditions": json.loads(row["conditions"]),
        "action_type": row["action_type"],
        "action_config": json.loads(row["action_config"]),
        "enabled": bool(row["enabled"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def get_rule(db: Any, rule_id: str) -> dict[str, Any]:
    with db.connection() as conn:
        row = conn.execute("SELECT * FROM automation_rules WHERE id = ?", (rule_id,)).fetchone()
    if row is None:
        raise ApiError(404, "automation_rule_not_found", "Automation rule not found")
    return _rule_public(row)


def create_rule(
    db: Any,
    *,
    name: str,
    event_type: str,
    conditions: dict[str, Any],
    action_type: str,
    action_config: dict[str, Any],
    enabled: bool = True,
) -> dict[str, Any]:
    if action_type not in ACTION_TYPES:
        raise ApiError(422, "invalid_action_type", f"action_type must be one of {list(ACTION_TYPES)}")
    if not event_type or not event_type.startswith(events_hub.EVENT_PREFIXES):
        raise ApiError(422, "invalid_event_type", "event_type must use a known namespace (payment.*, ...) or be '*'")
    if len(conditions) > 10:
        raise ApiError(422, "invalid_conditions", "conditions may contain at most 10 keys")
    now = iso_now()
    rule_id = new_id("ar")
    with db.transaction() as conn:
        conn.execute(
            """
            INSERT INTO automation_rules(
                id, name, event_type, conditions, action_type, action_config,
                enabled, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                rule_id,
                name,
                event_type,
                canonical_json(conditions),
                action_type,
                canonical_json(action_config),
                1 if enabled else 0,
                now,
                now,
            ),
        )
        events_hub.record_event(
            conn,
            "automation.rule.created",
            {"rule_id": rule_id, "name": name, "event_type": event_type, "action_type": action_type},
        )
    return get_rule(db, rule_id)


def update_rule(
    db: Any,
    rule_id: str,
    *,
    name: str | None = None,
    event_type: str | None = None,
    conditions: dict[str, Any] | None = None,
    action_type: str | None = None,
    action_config: dict[str, Any] | None = None,
    enabled: bool | None = None,
) -> dict[str, Any]:
    current = get_rule(db, rule_id)
    fields: dict[str, Any] = {}
    if name is not None:
        fields["name"] = name
    if event_type is not None:
        if not event_type.startswith(events_hub.EVENT_PREFIXES):
            raise ApiError(422, "invalid_event_type", "event_type must use a known namespace (payment.*, ...)")
        fields["event_type"] = event_type
    if conditions is not None:
        if len(conditions) > 10:
            raise ApiError(422, "invalid_conditions", "conditions may contain at most 10 keys")
        fields["conditions"] = canonical_json(conditions)
    if action_type is not None:
        if action_type not in ACTION_TYPES:
            raise ApiError(422, "invalid_action_type", f"action_type must be one of {list(ACTION_TYPES)}")
        fields["action_type"] = action_type
    if action_config is not None:
        fields["action_config"] = canonical_json(action_config)
    if enabled is not None:
        fields["enabled"] = 1 if enabled else 0
    with db.transaction() as conn:
        if fields:
            sets = ", ".join(f"{column} = ?" for column in fields)
            conn.execute(
                f"UPDATE automation_rules SET {sets}, updated_at = ? WHERE id = ?",
                (*fields.values(), iso_now(), rule_id),
            )
            if "enabled" in fields:
                events_hub.record_event(
                    conn,
                    "automation.rule.updated",
                    {
                        "rule_id": rule_id,
                        "enabled": bool(fields["enabled"]),
                        "change": "enabled" if set(fields) == {"enabled"} else "edited",
                    },
                )
    updated = get_rule(db, rule_id)
    return updated


def delete_rule(db: Any, rule_id: str) -> dict[str, Any]:
    get_rule(db, rule_id)
    with db.transaction() as conn:
        conn.execute("DELETE FROM automation_rules WHERE id = ?", (rule_id,))
        events_hub.record_event(conn, "automation.rule.deleted", {"rule_id": rule_id})
    return {"deleted": True, "id": rule_id}


def list_rules(db: Any, *, enabled_only: bool = False) -> dict[str, Any]:
    clause = "WHERE enabled = 1" if enabled_only else ""
    with db.connection() as conn:
        rows = conn.execute(
            f"SELECT * FROM automation_rules {clause} ORDER BY created_at, id"
        ).fetchall()
    return {"object": "list", "data": [_rule_public(row) for row in rows]}


def rule_matches_event(rule_event: str, event_type: str) -> bool:
    """Exact match or namespace wildcard (``payment.*`` matches payment.succeeded)."""
    if rule_event == event_type:
        return True
    return rule_event.endswith(".*") and event_type.startswith(rule_event[:-1])


def conditions_match(conditions: dict[str, Any], context: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    """Evaluate the declarative conditions against the flattened event context."""
    evaluated: dict[str, Any] = {}
    for key, expected in conditions.items():
        if key.endswith("_gte"):
            field, op = key[: -4], ">="
        elif key.endswith("_lte"):
            field, op = key[: -4], "<="
        elif key.endswith("_ne"):
            field, op = key[: -3], "!="
        else:
            field, op = key, "=="
        actual = context.get(field)
        evaluated[key] = {"expected": expected, "actual": actual, "op": op}
        if actual is None:
            return False, evaluated
        if isinstance(actual, str) and isinstance(expected, str):
            if actual.strip().lower() != expected.strip().lower():
                return False, evaluated
            continue
        try:
            if op == ">=" and not float(actual) >= float(expected):
                return False, evaluated
            if op == "<=" and not float(actual) <= float(expected):
                return False, evaluated
            if op == "!=" and (actual == expected or str(actual) == str(expected)):
                return False, evaluated
            if op == "==" and not (actual == expected or str(actual) == str(expected)):
                return False, evaluated
        except (TypeError, ValueError):
            return False, evaluated
    return True, evaluated


def format_message(template: str, context: dict[str, Any]) -> str:
    message = str(template)
    for key, value in list(context.items())[:40]:
        placeholder = "{" + key + "}"
        if placeholder in message:
            message = message.replace(placeholder, str(value))
    return message


# --------------------------------------------------------------------------- #
# Evaluation (queue consumer entry point)
# --------------------------------------------------------------------------- #
def evaluate_event(db: Any, settings: Settings, event_id: str) -> str:
    """Run all enabled rules matching the hub event. Idempotent per (rule, event)."""
    with db.connection() as conn:
        row = conn.execute("SELECT * FROM event_stream WHERE id = ?", (event_id,)).fetchone()
    if row is None:
        return "not_found"
    try:
        payload = json.loads(row["payload"])
    except (TypeError, ValueError):
        payload = {}
    # Hub payloads are stored flat ({"payment_id": ...}) but domain envelopes
    # may wrap them ({"data": {...}}) — support both shapes.
    if isinstance(payload, dict) and "data" in payload and isinstance(payload["data"], dict):
        payload = payload["data"]
    context = dict(payload)
    context.setdefault("event_type", row["type"])
    context["event_id"] = row["id"]

    with db.connection() as conn:
        rules = conn.execute("SELECT * FROM automation_rules WHERE enabled = 1").fetchall()
    matched = 0
    executed = 0
    for rule in rules:
        if not rule_matches_event(rule["event_type"], row["type"]):
            continue
        # At-least-once delivery: skip rules whose run was already audited for
        # this event (UNIQUE(rule_id, event_id)) so actions never re-execute.
        with db.connection() as conn:
            already = conn.execute(
                "SELECT id FROM automation_runs WHERE rule_id = ? AND event_id = ?",
                (rule["id"], row["id"]),
            ).fetchone()
        if already is not None:
            continue
        try:
            conditions = json.loads(rule["conditions"] or "{}")
        except (TypeError, ValueError):
            conditions = {}
        matched += 1
        conditions_ok, evaluated = conditions_match(conditions, context)
        if not conditions_ok:
            _record_run(db, rule, row, status="skipped", detail={"conditions": evaluated})
            continue
        try:
            rule_context = _rule_context(rule, context)
            detail = execute_action(db, settings, rule, rule_context)
            _record_run(db, rule, row, status="executed", detail=detail)
            executed += 1
        except Exception as exc:  # noqa: BLE001 - isolate per rule
            logger.exception("automation action failed", extra={"rule_id": rule["id"]})
            _record_run(db, rule, row, status="error", detail={"error": str(exc)[:400]})
    return f"{executed}/{matched}"


def _rule_context(rule: Any, context: dict[str, Any]) -> dict[str, Any]:
    merged = {f"rule_{k}": v for k, v in _rule_public(rule).items() if not isinstance(v, dict)}
    merged.update(context)
    merged.setdefault("rule_name", rule["name"])
    merged["rule_id"] = rule["id"]
    return merged


def _record_run(db: Any, rule: Any, event_row: Any, *, status: str, detail: dict[str, Any]) -> str:
    """Append the audit row (idempotent) and mirror it onto the hub."""
    run_id = new_id("run")
    now = iso_now()
    with db.transaction() as conn:
        existing = conn.execute(
            "SELECT id, status FROM automation_runs WHERE rule_id = ? AND event_id = ?",
            (rule["id"], event_row["id"]),
        ).fetchone()
        if existing is not None:
            return existing["id"]
        conn.execute(
            "INSERT INTO automation_runs(id, rule_id, event_type, event_id, status, detail, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                run_id,
                rule["id"],
                event_row["type"],
                event_row["id"],
                status,
                canonical_json(detail),
                now,
            ),
        )
        events_hub.record_event(
            conn,
            "automation.run",
            {
                "rule_id": rule["id"],
                "rule_name": rule["name"],
                "action_type": rule["action_type"],
                "status": status,
                "source_event_id": event_row["id"],
                "detail": detail,
            },
        )
    return run_id


# --------------------------------------------------------------------------- #
# Actions
# --------------------------------------------------------------------------- #
def execute_action(db: Any, settings: Settings, rule: Any, context: dict[str, Any]) -> dict[str, Any]:
    action = rule["action_type"]
    try:
        config = json.loads(rule["action_config"] or "{}")
    except (TypeError, ValueError):
        config = {}

    if action == "alerta_no_console":
        template = str(config.get("message") or "Alerta da regra {rule_name}")
        message = format_message(template, context)
        with db.transaction() as conn:
            event_id = events_hub.record_event(
                conn,
                "automation.alert",
                {
                    "rule_id": rule["id"],
                    "rule_name": rule["name"],
                    "message": message,
                    "source_event_id": context.get("event_id"),
                },
            )
        logger.info("automation alert", extra={"rule_id": rule["id"], "event_id": event_id})
        return {"message": message, "alert_event_id": event_id}

    if action == "tag_customer":
        return _action_tag_customer(db, settings, config, context)

    if action == "retry_webhook":
        return _action_retry_webhook(db, settings, config, context)

    raise ValueError(f"unsupported action type: {action}")


def _action_tag_customer(db: Any, settings: Settings, config: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    from .crm import add_tag, ensure_customer  # local import avoids cycles

    email = str(context.get("customer_email") or "").strip()
    tag = str(config.get("tag") or "").strip()
    if not email or not tag:
        return {"status": "skipped", "reason": "missing customer_email or tag in context"}
    with db.transaction() as conn:
        customer = ensure_customer(
            conn, email, name=context.get("customer_name"), fanout=settings.event_fanout
        )
    tag_row = add_tag(db, customer["id"], tag, fanout=settings.event_fanout)
    return {"status": "executed", "customer_id": customer["id"], "email": email, "tag": tag_row["tag"]}


def _action_retry_webhook(
    db: Any, settings: Settings, config: dict[str, Any], context: dict[str, Any]
) -> dict[str, Any]:
    """Reuse the v0.2 retry/DLQ path: DLQ rows go back to 'pending' (idempotent)."""
    delivery_id = context.get("delivery_id")
    payment_id = context.get("payment_id")
    if not delivery_id and not payment_id:
        return {"status": "skipped", "reason": "no delivery_id/payment_id in the event context"}
    clauses: list[str] = ["d.status = 'failed'"]
    params: list[Any] = []
    if delivery_id:
        clauses.append("d.id = ?")
        params.append(delivery_id)
    if payment_id:
        clauses.append("d.payment_id = ?")
        params.append(payment_id)
    with db.connection() as conn:
        candidates = conn.execute(
            "SELECT d.id FROM webhook_deliveries d WHERE "
            + " AND ".join(clauses)
            + " ORDER BY d.created_at DESC LIMIT 5",
            params,
        ).fetchall()
    if not candidates:
        return {"status": "skipped", "reason": "no failed delivery found for this event"}
    from .webhook_admin import requeue_delivery_row

    requeued: list[str] = []
    with db.transaction() as conn:
        for row in candidates:
            try:
                requeue_delivery_row(conn, row["id"])
                requeued.append(row["id"])
            except Exception as exc:  # noqa: BLE001
                logger.warning("automation retry_webhook skipped", extra={"error": str(exc)})
        if requeued:
            events_hub.record_event(
                conn,
                "webhook.requeued",
                {
                    "deliveries": requeued,
                    "rule_id": context.get("rule_id"),
                    "via": "automation",
                },
                fanout=settings.event_fanout,
            )
    return {"status": "executed", "requeued_deliveries": requeued}



def list_runs(
    db: Any,
    *,
    rule_id: str | None = None,
    status: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    clauses, params = [], []
    if rule_id:
        clauses.append("rule_id = ?")
        params.append(rule_id)
    if status:
        clauses.append("status = ?")
        params.append(status)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with db.connection() as conn:
        rows = conn.execute(
            "SELECT r.*, ru.name AS rule_name FROM automation_runs r "
            "LEFT JOIN automation_rules ru ON ru.id = r.rule_id "
            f"{where} ORDER BY r.created_at DESC, r.id DESC LIMIT ?",
            (*params, limit),
        ).fetchall()
    return {
        "object": "list",
        "data": [
            {
                "id": row["id"],
                "object": "automation_run",
                "rule_id": row["rule_id"],
                "rule_name": row["rule_name"],
                "event_type": row["event_type"],
                "event_id": row["event_id"],
                "status": row["status"],
                "detail": json.loads(row["detail"] or "{}"),
                "created_at": row["created_at"],
            }
            for row in rows
        ],
        "has_more": False,
    }
