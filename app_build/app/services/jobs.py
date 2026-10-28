"""Scheduled maintenance jobs (v0.3): segments, rollups, RAG sweep, retention.

Runs as an independent thread next to the payment worker/dispatcher
(ARQ-04). Every job is idempotent so overlapping executions are harmless.
The thread only exists when ``JOBS_ENABLED`` (default: follow ``WORKER_ENABLED``) —
tests keep it off for determinism and call the jobs directly.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from ..config import Settings
from . import analytics, crm, events, rag

logger = logging.getLogger("app.jobs")


def sweep_once(db: Any, settings: Settings) -> dict[str, int]:
    """One pass of every maintenance job (used by tests + the loop below)."""
    outcomes: dict[str, int] = {}
    try:
        outcomes["segments"] = crm.recompute_segments(db)
    except Exception:  # noqa: BLE001
        logger.exception("segments job failed")
    try:
        outcomes["analytics_rollup"] = analytics.recompute_daily(db, settings)
    except Exception:  # noqa: BLE001
        logger.exception("analytics rollup job failed")
    if settings.rag_enabled:
        try:
            stats = rag.reindex_all(db, settings)
            outcomes["rag_indexed"] = stats["indexed"]
            outcomes["rag_unchanged"] = stats["unchanged"]
        except Exception:  # noqa: BLE001
            logger.exception("rag sweep job failed")
    try:
        outcomes["retention_deleted"] = events.retention_delete(
            db, settings.event_stream_retention_days
        )
    except Exception:  # noqa: BLE001
        logger.exception("retention job failed")
    return outcomes


def run_forever(db: Any, settings: Settings, stop_event: threading.Event) -> None:
    logger.info("maintenance jobs started")
    next_run = {
        "segments": 0.0,
        "analytics": 0.0,
        "rag": 0.0,
        "retention": 0.0,
    }
    while not stop_event.is_set():
        now = time.monotonic()
        if now >= next_run["segments"]:
            next_run["segments"] = now + max(1.0, settings.jobs_segments_seconds)
            try:
                crm.recompute_segments(db)
            except Exception:  # noqa: BLE001
                logger.exception("segments job failed")
        if now >= next_run["analytics"]:
            next_run["analytics"] = now + max(1.0, settings.jobs_analytics_seconds)
            try:
                analytics.recompute_daily(db, settings)
            except Exception:  # noqa: BLE001
                logger.exception("analytics rollup job failed")
        if settings.rag_enabled and now >= next_run["rag"]:
            next_run["rag"] = now + max(1.0, settings.jobs_rag_seconds)
            try:
                rag.reindex_all(db, settings)
            except Exception:  # noqa: BLE001
                logger.exception("rag sweep job failed")
        if now >= next_run["retention"]:
            next_run["retention"] = now + max(1.0, settings.jobs_retention_seconds)
            try:
                events.retention_delete(db, settings.event_stream_retention_days)
            except Exception:  # noqa: BLE001
                logger.exception("retention job failed")
        stop_event.wait(1.0)
    logger.info("maintenance jobs stopped")
