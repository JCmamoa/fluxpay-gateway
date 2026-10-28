"""Worker: claims durable queue messages and routes them to their handlers.

Consumers (ARQ-09): the payment worker is the original consumer (v0.2) and
extended in v0.3 to deliver at-least-once processing for automations, RAG
indexing, segment recomputation and analytics rollups.
"""

from __future__ import annotations

import json
import logging
import threading
from typing import Any

from ..config import Settings
from . import queue
from .payments import expire_pix_payments, process_payment

logger = logging.getLogger("app.worker")


def handle_message(db: Any, settings: Settings, message: dict[str, Any]) -> str:
    message_type = message["type"]
    try:
        payload = json.loads(message["payload"] or "{}")
    except (ValueError, TypeError) as exc:
        raise ValueError("Malformed queue message payload") from exc

    if message_type == queue.PAYMENT_PROCESS:
        payment_id = payload.get("payment_id")
        if not payment_id:
            raise ValueError("Malformed payment message payload")
        return process_payment(db, settings, payment_id)

    if message_type == queue.AUTOMATION_EVALUATE:
        from .automations import evaluate_event

        return f"automation:{evaluate_event(db, settings, payload['event_id'])}"

    if message_type == queue.RECOMPUTE_SEGMENTS:
        from .crm import recompute_segments

        changed = recompute_segments(db)
        return f"segments:{changed}"

    if message_type == queue.RAG_INDEX:
        from .rag import index_source

        return f"rag:{index_source(db, settings, payload['source_type'], payload['source_ref'])}"

    raise ValueError(f"Unsupported message type: {message_type}")


def run_once(db: Any, settings: Settings, *, limit: int | None = None) -> dict[str, int]:
    """Process one batch. Also expires stale PIX payments (cheap sweep)."""
    messages = queue.claim_messages(db, settings, limit=limit)
    processed = 0
    failed = 0
    for message in messages:
        try:
            result = handle_message(db, settings, message)
            queue.mark_done(db, message["id"])
            processed += 1
            logger.info(
                "queue message processed",
                extra={"queue": message["type"], "payment_id": json.loads(message["payload"]).get("payment_id")},
            )
            if result in {"not_found", "ignored"}:
                logger.debug("payment job was a no-op", extra={"payment_id": None})
        except Exception as exc:  # noqa: BLE001 - isolate each message
            failed += 1
            outcome = queue.mark_failed(db, settings, message["id"], str(exc))
            logger.exception(
                "queue message failed (%s)", outcome, extra={"queue": message["type"]}
            )

    expired = expire_pix_payments(db, settings)
    return {
        "claimed": len(messages),
        "processed": processed,
        "failed": failed,
        "expired": expired,
    }


def run_forever(db: Any, settings: Settings, stop_event: threading.Event) -> None:
    logger.info("payment worker started", extra={"queue": "payment.process"})
    while not stop_event.is_set():
        try:
            run_once(db, settings)
        except Exception:  # noqa: BLE001 - keep the loop alive
            logger.exception("payment worker iteration failed")
        stop_event.wait(settings.worker_poll_seconds)
    logger.info("payment worker stopped")
