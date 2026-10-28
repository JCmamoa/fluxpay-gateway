"""Analytics endpoints (RF-13 / RF-18): summary, timeseries and daily rollups."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query

from ..services import analytics as analytics_service
from .deps import get_db, require_merchant

router = APIRouter(prefix="/v1/analytics", tags=["Analytics"])


@router.get("/summary", summary="Dashboard snapshot (today/7d/30d, queue, DLQ)")
def summary(
    db: Any = Depends(get_db),
    merchant: dict[str, Any] = Depends(require_merchant),
) -> dict[str, Any]:
    return analytics_service.summary(db)


@router.get("/timeseries", summary="Series for the dashboard charts")
def timeseries(
    metric: Annotated[str, Query()] = "volume",
    granularity: Annotated[str, Query()] = "day",
    from_: Annotated[str | None, Query(alias="from", max_length=29)] = None,
    to: Annotated[str | None, Query(max_length=29)] = None,
    db: Any = Depends(get_db),
    merchant: dict[str, Any] = Depends(require_merchant),
) -> dict[str, Any]:
    return analytics_service.timeseries(
        db, metric=metric, granularity=granularity, from_day=from_, to_day=to
    )


@router.get("/daily", summary="Daily rollups (analytics_daily)")
def daily(
    from_: Annotated[str | None, Query(alias="from", max_length=10)] = None,
    to: Annotated[str | None, Query(max_length=10)] = None,
    db: Any = Depends(get_db),
    merchant: dict[str, Any] = Depends(require_merchant),
) -> dict[str, Any]:
    return analytics_service.daily(db, from_day=from_, to_day=to)
