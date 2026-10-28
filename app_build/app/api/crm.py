"""CRM endpoints (RF-12): customers, 360° view, notes, tags, segments."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query

from ..config import Settings
from ..schemas import CustomerNoteCreate, CustomerTagCreate, SegmentCreate
from ..services import crm as crm_service
from .deps import get_db, get_settings, require_merchant

router = APIRouter(prefix="/v1", tags=["CRM"])


@router.get("/customers", summary="List customers (CRM, filterable)")
def list_customers(
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0, le=10_000)] = 0,
    tag: Annotated[str | None, Query(max_length=40)] = None,
    segment: Annotated[str | None, Query(max_length=80)] = None,
    method: Annotated[str | None, Query(max_length=20)] = None,
    min_amount_cents: Annotated[int | None, Query(ge=0)] = None,
    q: Annotated[str | None, Query(max_length=120)] = None,
    db: Any = Depends(get_db),
    merchant: dict[str, Any] = Depends(require_merchant),
) -> dict[str, Any]:
    return crm_service.list_customers(
        db,
        limit=limit,
        offset=offset,
        tag=tag,
        segment=segment,
        method=method,
        min_amount_cents=min_amount_cents,
        query=q,
    )


@router.get("/customers/{customer_id}", summary="Customer 360° view")
def get_customer(
    customer_id: str,
    db: Any = Depends(get_db),
    merchant: dict[str, Any] = Depends(require_merchant),
) -> dict[str, Any]:
    return crm_service.customer_360(db, customer_id)


@router.post("/customers/{customer_id}/notes", status_code=201, summary="Add a customer note")
def add_note(
    customer_id: str,
    payload: CustomerNoteCreate,
    db: Any = Depends(get_db),
    merchant: dict[str, Any] = Depends(require_merchant),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    return crm_service.add_note(
        db, customer_id, payload.body, payload.author, fanout=settings.event_fanout
    )


@router.post("/customers/{customer_id}/tags", status_code=201, summary="Add a customer tag")
def add_tag(
    customer_id: str,
    payload: CustomerTagCreate,
    db: Any = Depends(get_db),
    merchant: dict[str, Any] = Depends(require_merchant),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    return crm_service.add_tag(db, customer_id, payload.tag, fanout=settings.event_fanout)


@router.get("/segments", summary="List declarative segments")
def list_segments(
    db: Any = Depends(get_db),
    merchant: dict[str, Any] = Depends(require_merchant),
) -> dict[str, Any]:
    return crm_service.list_segments(db)


@router.post("/segments", status_code=201, summary="Create a segment (computed immediately)")
def create_segment(
    payload: SegmentCreate,
    db: Any = Depends(get_db),
    merchant: dict[str, Any] = Depends(require_merchant),
) -> dict[str, Any]:
    return crm_service.create_segment(db, payload.name, payload.definition.model_dump(exclude_none=True))
