"""Payment worker: claims durable queue messages and drives the state machine."""

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
    if message["type"] != queue.PAYMENT_PROCESS:
        raise ValueError(f"Unsupported message type: {message['type']}")
    try:
        payload = json.loads(message["payload"])
        payment_id = payload["payment_id"]
    except (ValueError, KeyError, TypeError) as exc:
        raise ValueError("Malformed payment message payload") from exc
    return process_payment(db, settings, payment_id)


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
