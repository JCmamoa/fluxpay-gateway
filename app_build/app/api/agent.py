"""Agent IA endpoints (RF-16 / RF-19): chat with RAG sources + 2-step confirm."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from ..config import Settings
from ..schemas import AgentChatRequest, AgentConfirmRequest
from ..services import agent as agent_service
from .deps import get_db, get_settings, require_merchant

router = APIRouter(prefix="/v1/agent", tags=["Agent"])


@router.post("/chat", summary="Ask the operational assistant (grounded, audit-logged)")
def chat(
    payload: AgentChatRequest,
    db: Any = Depends(get_db),
    settings: Settings = Depends(get_settings),
    merchant: dict[str, Any] = Depends(require_merchant),
) -> dict[str, Any]:
    return agent_service.chat(db, settings, payload.question, merchant_id=merchant["id"])


@router.post("/confirm", summary="Confirm a pending sensitive action (two-step execution)")
def confirm(
    payload: AgentConfirmRequest,
    db: Any = Depends(get_db),
    settings: Settings = Depends(get_settings),
    merchant: dict[str, Any] = Depends(require_merchant),
) -> dict[str, Any]:
    return agent_service.confirm(db, settings, payload.token)
