"""Structured JSON logging with request/payment/event correlation IDs."""

from __future__ import annotations

import contextvars
import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any

request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("request_id", default=None)
payment_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("payment_id", default=None)
event_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("event_id", default=None)

_EXTRA_FIELDS = (
    "request_id",
    "payment_id",
    "event_id",
    "merchant_id",
    "method",
    "path",
    "status_code",
    "duration_ms",
    "delivery_id",
    "attempt",
    "queue",
    "http_status",
)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": request_id_var.get(),
            "payment_id": payment_id_var.get(),
            "event_id": event_id_var.get(),
        }
        for field in _EXTRA_FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps({k: v for k, v in payload.items() if v is not None}, ensure_ascii=False)


class _ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:  # noqa: D102 - logging API
        if not getattr(record, "request_id", None):
            record.request_id = request_id_var.get()
        if not getattr(record, "payment_id", None):
            record.payment_id = payment_id_var.get()
        if not getattr(record, "event_id", None):
            record.event_id = event_id_var.get()
        return True


def configure_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    root.handlers.clear()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(_ContextFilter())
    root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    # Uvicorn's default handlers would emit unstructured lines; we log requests ourselves.
    for noisy in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(noisy)
        logger.handlers.clear()
        logger.propagate = True
