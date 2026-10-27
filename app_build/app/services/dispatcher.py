"""Webhook dispatcher loop (independently restartable from the worker)."""

from __future__ import annotations

import logging
import threading

from ..config import Settings
from .webhooks import dispatch_once

logger = logging.getLogger("app.dispatcher")


def run_forever(db: object, settings: Settings, stop_event: threading.Event) -> None:
    logger.info("webhook dispatcher started", extra={"queue": "webhook.deliveries"})
    while not stop_event.is_set():
        try:
            attempted = dispatch_once(db, settings)  # type: ignore[arg-type]
            if attempted:
                logger.debug("webhook batch dispatched", extra={"queue": attempted})
        except Exception:  # noqa: BLE001 - keep the loop alive
            logger.exception("webhook dispatcher iteration failed")
        stop_event.wait(settings.dispatcher_poll_seconds)
    logger.info("webhook dispatcher stopped")
