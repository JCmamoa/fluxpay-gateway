"""RAG endpoints (RF-17): runtime reindex (data + project docs)."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query

from ..config import Settings
from ..services import rag as rag_service
from ..utils import iso_now
from .deps import get_db, get_settings, require_merchant

router = APIRouter(prefix="/v1/rag", tags=["RAG"])


@router.get("/search", summary="Lexical (BM25) search over the RAG index")
def search(
    q: Annotated[str, Query(min_length=2, max_length=300)],
    limit: Annotated[int, Query(ge=1, le=20)] = 5,
    db: Any = Depends(get_db),
    merchant: dict[str, Any] = Depends(require_merchant),
) -> dict[str, Any]:
    results = rag_service.search(db, q, limit=limit)
    return {"object": "list", "data": results}


@router.post("/reindex", summary="Force reindexation (payments, customers, events, docs)")
def reindex(
    force: Annotated[bool, Query()] = False,
    db: Any = Depends(get_db),
    settings: Settings = Depends(get_settings),
    merchant: dict[str, Any] = Depends(require_merchant),
) -> dict[str, Any]:
    if not settings.rag_enabled:
        return {"indexed": 0, "unchanged": 0, "disabled": True}
    stats = rag_service.reindex_all(db, settings, force=force)
    return {"object": "rag_reindex", "completed_at": iso_now(), **stats}
