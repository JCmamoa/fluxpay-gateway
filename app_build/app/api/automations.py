"""Automation endpoints (RF-15): rule CRUD, toggles and run audit."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query

from ..schemas import AutomationRuleCreate, AutomationRulePatch
from ..services import automations as automations_service
from .deps import get_db, require_merchant

router = APIRouter(prefix="/v1/automations", tags=["Automations"])


@router.get("/rules", summary="List automation rules")
def list_rules(
    enabled_only: Annotated[bool, Query()] = False,
    db: Any = Depends(get_db),
    merchant: dict[str, Any] = Depends(require_merchant),
) -> dict[str, Any]:
    return automations_service.list_rules(db, enabled_only=enabled_only)


@router.post("/rules", status_code=201, summary="Create an automation rule")
def create_rule(
    payload: AutomationRuleCreate,
    db: Any = Depends(get_db),
    merchant: dict[str, Any] = Depends(require_merchant),
) -> dict[str, Any]:
    return automations_service.create_rule(
        db,
        name=payload.name,
        event_type=payload.event_type,
        conditions=payload.conditions,
        action_type=payload.action_type,
        action_config=payload.action_config,
        enabled=payload.enabled,
    )


@router.get("/rules/{rule_id}", summary="Retrieve an automation rule")
def get_rule(
    rule_id: str,
    db: Any = Depends(get_db),
    merchant: dict[str, Any] = Depends(require_merchant),
) -> dict[str, Any]:
    return automations_service.get_rule(db, rule_id)


@router.patch("/rules/{rule_id}", summary="Update / toggle an automation rule")
def update_rule(
    rule_id: str,
    payload: AutomationRulePatch,
    db: Any = Depends(get_db),
    merchant: dict[str, Any] = Depends(require_merchant),
) -> dict[str, Any]:
    return automations_service.update_rule(
        db,
        rule_id,
        name=payload.name,
        event_type=payload.event_type,
        conditions=payload.conditions,
        action_type=payload.action_type,
        action_config=payload.action_config,
        enabled=payload.enabled,
    )


@router.delete("/rules/{rule_id}", summary="Delete an automation rule")
def delete_rule(
    rule_id: str,
    db: Any = Depends(get_db),
    merchant: dict[str, Any] = Depends(require_merchant),
) -> dict[str, Any]:
    return automations_service.delete_rule(db, rule_id)


@router.get("/runs", summary="Automation execution audit")
def list_runs(
    rule_id: Annotated[str | None, Query(max_length=64)] = None,
    status: Annotated[Literal["executed", "skipped", "error", "duplicate"] | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    db: Any = Depends(get_db),
    merchant: dict[str, Any] = Depends(require_merchant),
) -> dict[str, Any]:
    return automations_service.list_runs(db, rule_id=rule_id, status=status, limit=limit)
