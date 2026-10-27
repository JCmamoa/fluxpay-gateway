"""Webhook endpoint and delivery inspection endpoints."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query

from ..schemas import WebhookEndpointPatch
from ..services import webhook_admin
from .deps import get_db, require_merchant

router = APIRouter(prefix="/v1", tags=["Webhooks"])


@router.get("/webhook-endpoints", summary="List webhook endpoints")
def list_endpoints(
    merchant: dict[str, Any] = Depends(require_merchant),
    db: Any = Depends(get_db),
) -> dict[str, Any]:
    return {
        "object": "list",
        "data": webhook_admin.list_endpoints(db, merchant["id"]),
        "has_more": False,
    }


@router.patch("/webhook-endpoints/{endpoint_id}", summary="Update a webhook endpoint")
def update_endpoint(
    endpoint_id: str,
    payload: WebhookEndpointPatch,
    merchant: dict[str, Any] = Depends(require_merchant),
    db: Any = Depends(get_db),
) -> dict[str, Any]:
    return webhook_admin.update_endpoint(
        db, merchant["id"], endpoint_id, url=payload.url, active=payload.active
    )


@router.get("/webhook-deliveries", summary="List webhook deliveries (including the DLQ)")
def list_deliveries(
    status: Annotated[Literal["pending", "delivered", "failed"] | None, Query()] = None,
    payment_id: Annotated[str | None, Query(max_length=64)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    merchant: dict[str, Any] = Depends(require_merchant),
    db: Any = Depends(get_db),
) -> dict[str, Any]:
    return webhook_admin.list_deliveries(
        db, merchant["id"], status=status, payment_id=payment_id, limit=limit
    )


@router.get("/webhook-deliveries/{delivery_id}", summary="Delivery detail + attempt log")
def get_delivery(
    delivery_id: str,
    merchant: dict[str, Any] = Depends(require_merchant),
    db: Any = Depends(get_db),
) -> dict[str, Any]:
    return webhook_admin.get_delivery(db, merchant["id"], delivery_id)


@router.post(
    "/webhook-deliveries/{delivery_id}/retry",
    status_code=202,
    summary="Manually requeue a delivery (DLQ replay)",
)
def retry_delivery(
    delivery_id: str,
    merchant: dict[str, Any] = Depends(require_merchant),
    db: Any = Depends(get_db),
) -> dict[str, Any]:
    return webhook_admin.retry_delivery(db, merchant["id"], delivery_id)
