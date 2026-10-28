"""Data export endpoints (RF-18): streaming CSV for payments and customers."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Response
from fastapi.responses import StreamingResponse

from ..errors import ApiError
from ..services import analytics as analytics_service
from .deps import get_db, require_merchant

router = APIRouter(prefix="/v1/data", tags=["Data"])


@router.get(
    "/exports/{kind}",
    summary="Streaming CSV export (payments | customers)",
    responses={
        404: {"description": "Unknown export kind (payments or customers)"},
    },
)
def export(
    kind: str,
    db: Any = Depends(get_db),
    merchant: dict[str, Any] = Depends(require_merchant),
) -> Response:
    if kind not in ("payments", "customers"):
        raise ApiError(404, "export_not_found", "Export kind must be payments or customers")
    filename = analytics_service.export_filename(kind)
    return StreamingResponse(
        analytics_service.iter_export(db, kind),
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )
