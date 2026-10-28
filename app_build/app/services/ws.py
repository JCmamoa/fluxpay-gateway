"""WebSocket broadcaster for ``WS /ws/events`` (RF-13 / RF-14 / ARQ-10).

Design notes
------------
* **WS is a notification channel; REST is the source of truth.**  Clients
  reconcile with ``GET /v1/events?after=<last_event_id>`` on reconnect and the
  snapshot endpoints for domain state.
* A single asyncio task polls the ``event_stream`` tail and fans out to all
  connected sockets; SQLite commits from the worker/dispatcher threads become
  visible on the next poll (default 0.15 s), comfortably inside the 500 ms
  application-level budget (RNF-10).
* Handshake authentication: ``?token=<api_key>`` validated against the stored
  key hash before the socket is accepted (RNF-13).
* Heartbeat: a server ``server.ping`` message every ``ws_heartbeat_seconds``
  (20 s default) so proxies and clients can detect a dead connection.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from fastapi import WebSocket

from ..config import Settings
from ..security import hash_api_key
from . import events as events_service

logger = logging.getLogger("app.ws")

CLOSE_INVALID_TOKEN = 4401
CLOSE_BAD_QUERY = 4400


class ConnectionRegistry:
    """Track live sockets with their individual watermarks (event ``seq``)."""

    def __init__(self) -> None:
        self._clients: dict[WebSocket, int] = {}
        self._lock = asyncio.Lock()

    async def add(self, websocket: WebSocket, last_seq: int) -> None:
        async with self._lock:
            self._clients[websocket] = last_seq

    async def remove(self, websocket: WebSocket) -> None:
        async with self._lock:
            self._clients.pop(websocket, None)

    async def snapshot(self) -> dict[WebSocket, int]:
        async with self._lock:
            return dict(self._clients)


    async def update(self, websocket: WebSocket, last_seq: int) -> None:
        """Advance a client watermark after its backlog was delivered."""
        async with self._lock:
            current = self._clients.get(websocket)
            if current is not None and last_seq > current:
                self._clients[websocket] = last_seq


class Broadcaster:
    """Fans out new ``event_stream`` rows + heartbeats to subscribed sockets."""

    def __init__(self, db: Any, settings: Settings, registry: ConnectionRegistry) -> None:
        self.db = db
        self.settings = settings
        self.registry = registry
        self._cursor = 0
        self._last_send = time.monotonic()
        self._task: asyncio.Task | None = None

    def seed_cursor(self) -> None:
        try:
            self._cursor = events_service.max_seq(self.db)
        except Exception:  # noqa: BLE001 - startup must not crash on db hiccups  # pragma: no cover
            logger.exception("broadcaster cursor seed failed")

    def start(self) -> None:
        self.seed_cursor()
        loop = asyncio.get_running_loop()
        self._task = loop.create_task(self.run())
        logger.info("ws broadcaster started")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None
        logger.info("ws broadcaster stopped")

    async def run(self) -> None:
        poll = max(0.02, self.settings.ws_poll_seconds)
        heartbeat = max(1.0, self.settings.ws_heartbeat_seconds)
        while True:
            try:
                await asyncio.sleep(poll)
                clients = await self.registry.snapshot()
                if not clients:
                    continue
                # Read from the oldest client watermark so a client that
                # reconnected with an old ``after`` id still catches up.
                oldest = min(self._cursor, min(clients.values()))
                fresh, max_seq = events_service.events_since(self.db, oldest)
                if fresh:
                    self._cursor = max(self._cursor, max_seq)
                dead: list[WebSocket] = []
                for wsc, watermark in clients.items():
                    pending = [event for event in fresh if event["seq"] > watermark]
                    try:
                        for event in pending:
                            await wsc.send_json(_ws_message(event))
                            self._last_send = time.monotonic()
                        if not fresh and time.monotonic() - self._last_send > heartbeat:
                            await wsc.send_json(_heartbeat_message())
                            self._last_send = time.monotonic()
                        if pending:
                            # Advance the client watermark so already-sent rows
                            # are never re-fetched on the next poll.
                            await self.registry.update(wsc, pending[-1]["seq"])
                    except Exception:  # noqa: BLE001 - any send failure drops the client
                        dead.append(wsc)
                for wsc in dead:
                    await self.registry.remove(wsc)
                    try:
                        await wsc.close()
                    except Exception:  # noqa: BLE001
                        pass
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - keep the broadcaster alive
                logger.exception("broadcaster iteration failed")

    async def broadcast(self, event: dict[str, Any]) -> int:
        """Direct push (used when the loop task already saw the row unnecessary)."""
        clients = await self.registry.snapshot()
        sent = 0
        for wsc, watermark in clients.items():
            if event["seq"] <= watermark:
                continue
            try:
                await wsc.send_json(_ws_message(event))
                self._last_send = time.monotonic()
                await self.registry.update(wsc, event["seq"])
                sent += 1
            except Exception:  # noqa: BLE001
                await self.registry.remove(wsc)
        return sent


def _ws_message(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": event["id"],
        "type": event["type"],
        "created_at": event["created_at"],
        "data": event["data"],
    }


def _heartbeat_message() -> dict[str, Any]:
    from ..utils import iso_now

    return {"id": None, "type": "server.ping", "created_at": iso_now(), "data": {}}


async def websocket_auth(websocket: WebSocket, db: Any) -> str | None:
    """Validate ``?token=`` against the stored merchant key hashes (handshake)."""
    token = websocket.query_params.get("token") or ""
    if not token:
        return None
    key_hash = hash_api_key(token)
    try:
        with db.connection() as conn:
            row = conn.execute(
                "SELECT id FROM merchants WHERE api_key_hash = ?", (key_hash,)
            ).fetchone()
    except Exception:  # noqa: BLE001
        return None
    return row["id"] if row else None
