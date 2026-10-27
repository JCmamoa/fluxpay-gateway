"""Payment HTTP endpoints (spec section 10)."""

from __future__ import annotations

import json
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, Header, Query, Request, Response

from ..config import Settings
from ..errors import ApiError
from ..schemas import PaymentCreate, SimulatePaymentRequest
from ..services import payments as payment_service
from ..services.webhooks import public_payment
from .deps import get_db, get_settings, require_merchant

router = APIRouter(prefix="/v1", tags=["Payments"])


@router.post(
    "/payments",
    status_code=202,
    summary="Create a payment",
    responses={
        200: {"description": "Idempotent replay of the original response"},
        400: {"description": "Missing or invalid Idempotency-Key"},
        401: {"description": "Invalid API key"},
        409: {"description": "Idempotency-Key reused with a different payload"},
        422: {"description": "Validation error"},
    },
)
def create_payment(
    request: Request,
    payload: PaymentCreate,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    merchant: dict[str, Any] = Depends(require_merchant),
    settings: Settings = Depends(get_settings),
    db: Any = Depends(get_db),
) -> Response:
    key = idempotency_key or ""
    if not key.strip():
        raise ApiError(
            400,
            "missing_idempotency_key",
            "The Idempotency-Key header is required.",
        )
    if len(key) > 255:
        raise ApiError(
            400,
            "invalid_idempotency_key",
            "The Idempotency-Key header must be between 1 and 255 characters.",
        )

    status, body, replay = payment_service.create_payment(
        db, settings, merchant["id"], key, payload
    )
    headers: dict[str, str] = {}
    if replay:
        headers["Idempotent-Replay"] = "true"
    else:
        try:
            headers["Location"] = f"/v1/payments/{json.loads(body)['id']}"
        except (ValueError, KeyError):
            pass
    return Response(content=body, status_code=status, media_type="application/json", headers=headers)


@router.get("/payments", summary="List payments (cursor paginated)")
def list_payments(
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    starting_after: Annotated[str | None, Query(max_length=64)] = None,
    merchant: dict[str, Any] = Depends(require_merchant),
    db: Any = Depends(get_db),
) -> dict[str, Any]:
    return payment_service.list_payments(
        db, merchant["id"], limit=limit, starting_after=starting_after
    )


@router.get("/payments/{payment_id}", summary="Retrieve a payment")
def get_payment(
    payment_id: str,
    merchant: dict[str, Any] = Depends(require_merchant),
    db: Any = Depends(get_db),
) -> dict[str, Any]:
    row = payment_service.get_payment(db, merchant["id"], payment_id)
    return public_payment(row)


@router.get("/payments/{payment_id}/events", summary="Immutable payment event history")
def get_payment_events(
    payment_id: str,
    merchant: dict[str, Any] = Depends(require_merchant),
    db: Any = Depends(get_db),
) -> dict[str, Any]:
    events = payment_service.list_events(db, merchant["id"], payment_id)
    return {"object": "list", "data": events}


@router.post(
    "/payments/{payment_id}/simulate-payment",
    status_code=202,
    summary="Simulate a PIX payment confirmation (demo only)",
)
def simulate_payment(
    payment_id: str,
    request: Request,
    payload: Annotated[SimulatePaymentRequest | None, Body()] = None,
    merchant: dict[str, Any] = Depends(require_merchant),
    settings: Settings = Depends(get_settings),
    db: Any = Depends(get_db),
) -> dict[str, Any]:
    if not settings.demo_mode:
        raise ApiError(404, "not_found", "Simulation is only available in demo mode.")
    # The optional body only accepts {"result": "paid"}; validation is enough.
    _ = payload
    return payment_service.simulate_payment(db, settings, merchant["id"], payment_id)
