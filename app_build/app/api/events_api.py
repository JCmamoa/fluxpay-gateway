"""Event hub REST snapshot + WS /ws/events realtime endpoint (RF-13/RF-14)."""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, WebSocket, WebSocketException, status

from ..errors import ApiError
from ..services import events as events_service
from ..services.ws import ConnectionRegistry, websocket_auth
from .deps import get_db

logger = logging.getLogger("app.api.events")

router = APIRouter(tags=["Events"])


@router.get("/v1/events", summary="Event hub snapshot (source of truth for reconciliation)")
def list_events(
    after: Annotated[str | None, Query(max_length=64)] = None,
    type: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    order: Annotated[str, Query()] = "desc",
    db: Any = Depends(get_db),
) -> dict[str, Any]:
    types = [type] if type else None
    if type and "," in type:
        types = [part for part in type.split(",") if part]
    return events_service.list_events(db, after=after, types=types, limit=limit, order=order)


@router.websocket("/ws/events")
async def ws_events(
    websocket: WebSocket,
    token: Annotated[str | None, Query(max_length=255)] = None,
) -> None:
    """Realtime broadcast of the event hub (handshake-authenticated).

    * ``?token=<api_key>`` is validated against stored key hashes;
    * reconnection replays everything after ``?after=<last_event_id>``;
    * server heartbeats every ``WS_HEARTBEAT_SECONDS``.
    """
    db = websocket.app.state.db  # type: ignore[attr-defined]
    registry: ConnectionRegistry = websocket.app.state.ws_registry  # type: ignore[attr-defined]
    merchant_id = await websocket_auth(websocket, db)
    if merchant_id is None:
        await websocket.close(code=4401, reason="invalid_token")
        return
    after = websocket.query_params.get("after") or None
    try:
        if after:
            watermark = _resolve_seq(db, after)
        else:
            # live tail: fresh connects subscribe from here (no backlog replay)
            watermark = events_service.max_seq(db)
    except ValueError as exc:
        await websocket.close(code=4400, reason=str(exc))
        return

    await websocket.accept()
    await registry.add(websocket, watermark)
    logger.info(
        "ws client connected",
        extra={"merchant_id": merchant_id, "after": after or "live"},
    )
    try:
        # Server only sends; this receive loop releases the socket for the
        # broadcaster task and detects client disconnects.
        while True:
            await websocket.receive_text()
    except Exception:  # noqa: BLE001 - WebSocketDisconnect and transport errors
        pass
    finally:
        await registry.remove(websocket)
        logger.info("ws client disconnected", extra={"merchant_id": merchant_id})


def _resolve_seq(db: Any, event_id: str) -> int:
    with db.connection() as conn:
        row = conn.execute(
            "SELECT rowid FROM event_stream WHERE id = ?", (event_id,)
        ).fetchone()
    if row is None:
        raise ValueError(f"unknown after event id: {event_id}")
    return int(row[0])
