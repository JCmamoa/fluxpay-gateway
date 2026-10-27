"""Demo-only endpoints: console bootstrap, self-hosted webhook receiver, fault injection."""

from __future__ import annotations

import json
import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse

from ..config import Settings
from ..errors import ApiError
from ..schemas import DemoFailureRequest
from ..security import verify_webhook_signature
from ..services.seed import get_demo_setting, set_demo_setting
from ..utils import iso_now, mask_secret, new_id
from .deps import get_db, get_settings, require_merchant

logger = logging.getLogger("app.demo")

router = APIRouter(tags=["Demo"])

FAIL_NEXT_KEY = "fail_next_webhooks"


def _ensure_demo(settings: Settings) -> None:
    if not settings.demo_mode:
        raise ApiError(404, "not_found", "Not found")


@router.get("/demo/config", summary="Console bootstrap (no secrets unless explicitly enabled)")
def demo_config(
    settings: Settings = Depends(get_settings),
    db: Any = Depends(get_db),
) -> dict[str, Any]:
    _ensure_demo(settings)
    with db.connection() as conn:
        endpoint = conn.execute(
            "SELECT id, url, active FROM webhook_endpoints WHERE merchant_id = ? ORDER BY created_at LIMIT 1",
            (settings.merchant_id,),
        ).fetchone()
        fail_next = int(get_demo_setting(db, FAIL_NEXT_KEY, "0"))
    payload: dict[str, Any] = {
        "demo_mode": settings.demo_mode,
        "version": "1.0.0",
        "merchant": {"id": settings.merchant_id, "name": settings.merchant_name},
        "api_key_hint": mask_secret(settings.api_key, 4),
        "webhook_endpoint": dict(endpoint) if endpoint else None,
        "pix": {"expires_minutes": settings.pix_expires_minutes, "key": settings.pix_key},
        "webhooks": {
            "max_attempts": settings.webhook_max_attempts,
            "retry_schedule_seconds": list(settings.webhook_retry_schedule),
            "timeout_seconds": settings.webhook_timeout_seconds,
            "signature_tolerance_seconds": settings.webhook_signature_tolerance_seconds,
        },
        "fail_next_webhooks": fail_next,
    }
    if settings.demo_expose_api_key:
        # Convenience for the local demo console; disabled automatically in production.
        payload["demo_api_key"] = settings.api_key
    return payload


@router.post(
    "/demo/webhook-receiver",
    summary="Simulated merchant webhook receiver (validates HMAC and de-duplicates)",
)
async def webhook_receiver(
    request: Request,
    endpoint: Annotated[str, Query(max_length=64)] = "whe_demo",
    settings: Settings = Depends(get_settings),
    db: Any = Depends(get_db),
) -> JSONResponse:
    _ensure_demo(settings)
    raw = await request.body()
    signature_header = request.headers.get("x-signature")
    event_id = request.headers.get("x-event-id") or "evt_unknown"

    with db.connection() as conn:
        endpoint_row = conn.execute(
            "SELECT id, secret FROM webhook_endpoints WHERE id = ?", (endpoint,)
        ).fetchone()
    if endpoint_row is None:
        return JSONResponse(
            status_code=404,
            content={"error": {"code": "webhook_endpoint_not_found", "message": "Unknown endpoint"}},
        )

    # Fault injection used by the console to demonstrate retry/backoff/DLQ.
    with db.transaction() as conn:
        row = conn.execute("SELECT value FROM demo_settings WHERE key = ?", (FAIL_NEXT_KEY,)).fetchone()
        remaining = int(row["value"]) if row else 0
        if remaining > 0:
            conn.execute(
                "UPDATE demo_settings SET value = ?, updated_at = ? WHERE key = ?",
                (str(remaining - 1), iso_now(), FAIL_NEXT_KEY),
            )
    if remaining > 0:
        logger.warning("simulated receiver failure", extra={"event_id": event_id})
        return JSONResponse(
            status_code=503,
            content={"error": {"code": "simulated_receiver_unavailable", "message": "Injected failure"}},
        )

    valid, reason = verify_webhook_signature(
        endpoint_row["secret"],
        signature_header,
        raw,
        tolerance_seconds=settings.webhook_signature_tolerance_seconds,
    )
    received_at = iso_now()

    with db.transaction() as conn:
        duplicate = conn.execute(
            "SELECT id FROM demo_webhook_receipts "
            "WHERE endpoint_id = ? AND event_id = ? AND signature_valid = 1",
            (endpoint, event_id),
        ).fetchone()
        if duplicate is None:
            conn.execute(
                """
                INSERT INTO demo_webhook_receipts(
                    endpoint_id, event_id, signature_valid, body, received_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (endpoint, event_id, 1 if valid else 0, raw.decode("utf-8", "replace"), received_at),
            )

    if not valid:
        logger.warning(
            "webhook signature rejected", extra={"event_id": event_id}
        )
        return JSONResponse(
            status_code=401,
            content={
                "error": {
                    "code": "invalid_signature",
                    "message": f"Webhook signature verification failed: {reason}",
                }
            },
        )

    return JSONResponse(
        status_code=200,
        content={
            "received": True,
            "duplicate": duplicate is not None,
            "event_id": event_id,
            "received_at": received_at,
        },
    )


@router.get(
    "/demo/webhook-receiver/receipts",
    summary="Inspect what the simulated merchant received",
)
def webhook_receipts(
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    merchant: dict[str, Any] = Depends(require_merchant),
    db: Any = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    _ensure_demo(settings)
    with db.connection() as conn:
        rows = conn.execute(
            """
            SELECT r.* FROM demo_webhook_receipts r
            JOIN webhook_endpoints e ON e.id = r.endpoint_id
            WHERE e.merchant_id = ?
            ORDER BY r.received_at DESC, r.id DESC LIMIT ?
            """,
            (merchant["id"], limit),
        ).fetchall()
    data = []
    for row in rows:
        try:
            body = json.loads(row["body"])
        except (TypeError, ValueError):
            body = None
        data.append(
            {
                "id": row["id"],
                "endpoint_id": row["endpoint_id"],
                "event_id": row["event_id"],
                "signature_valid": bool(row["signature_valid"]),
                "event": body,
                "received_at": row["received_at"],
            }
        )
    return {"object": "list", "data": data, "has_more": False}


@router.post(
    "/demo/webhook-failures",
    summary="Make the next N webhook deliveries fail (retry/backoff demo)",
)
def set_webhook_failures(
    payload: DemoFailureRequest,
    _merchant: dict[str, Any] = Depends(require_merchant),
    db: Any = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    _ensure_demo(settings)
    set_demo_setting(db, FAIL_NEXT_KEY, str(payload.count))
    return {"fail_next_webhooks": payload.count}


@router.post("/demo/reset-failures", summary="Clear injected webhook failures")
def reset_webhook_failures(
    _merchant: dict[str, Any] = Depends(require_merchant),
    db: Any = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    _ensure_demo(settings)
    set_demo_setting(db, FAIL_NEXT_KEY, "0")
    return {"fail_next_webhooks": 0}


@router.get("/demo/echo", summary="Generate a signed sample event for signature tests")
def demo_echo(
    settings: Settings = Depends(get_settings),
) -> dict[str, str]:
    """Not part of the gateway API; handy when wiring a merchant verifier."""
    _ensure_demo(settings)
    return {
        "hint": "Webhook signatures are HMAC-SHA256 over '<unix_ts>.<raw_body>'.",
        "sample_event_id": new_id("evt"),
        "secret_hint": mask_secret(settings.webhook_secret, 4),
    }
