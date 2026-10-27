"""Health, identity and operational metrics endpoints."""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from .. import __version__
from ..services import metrics as metrics_service
from ..services import queue as queue_service
from ..services.webhook_admin import delivery_counts
from ..utils import iso_now
from .deps import get_db, require_merchant

router = APIRouter(tags=["System"])


@router.get("/health", summary="Liveness/readiness probe")
def health(request: Request) -> JSONResponse:
    db = request.app.state.db
    database_ok = db.healthy()
    body: dict[str, Any] = {
        "status": "ok" if database_ok else "error",
        "version": __version__,
        "database": "ok" if database_ok else "unavailable",
        "time": iso_now(),
    }
    if database_ok:
        counts = queue_service.queue_stats(db)
        body["queue"] = {
            "pending": counts["pending"],
            "processing": counts["processing"],
            "failed": counts["failed"],
        }
        with db.connection() as conn:
            deliveries = conn.execute(
                "SELECT status, COUNT(*) AS total FROM webhook_deliveries GROUP BY status"
            ).fetchall()
        body["webhooks"] = {row["status"]: row["total"] for row in deliveries}
    return JSONResponse(status_code=200 if database_ok else 503, content=body)


@router.get("/v1/me", summary="Authenticated merchant identity")
def me(merchant: dict[str, Any] = Depends(require_merchant)) -> dict[str, Any]:
    return {"object": "merchant", **merchant}


@router.get("/v1/metrics", summary="Basic operational metrics")
def metrics(
    request: Request,
    merchant: dict[str, Any] = Depends(require_merchant),
    db: Any = Depends(get_db),
) -> dict[str, Any]:
    snapshot = metrics_service.snapshot(
        db, started_at=request.app.state.started_at, now=time.time()
    )
    snapshot["webhooks"]["merchant_deliveries_by_status"] = delivery_counts(db, merchant["id"])
    return snapshot
