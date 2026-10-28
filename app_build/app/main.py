"""FastAPI application factory, lifespan and HTTP middleware."""

from __future__ import annotations

import asyncio
import logging
import re
import sqlite3
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import __version__
from .api import agent as agent_api
from .api import analytics as analytics_api
from .api import automations as automations_api
from .api import crm as crm_api
from .api import data as data_api
from .api import demo as demo_api
from .api import events_api
from .api import payments as payments_api
from .api import rag as rag_api
from .api import system as system_api
from .api import webhooks as webhooks_api
from .config import Settings
from .db import Database
from .errors import ApiError
from .logging_setup import configure_logging, request_id_var
from .services import dispatcher, jobs, queue, worker
from .services import ws as ws_service
from .services.automations import seed_example_rules
from .services.seed import seed_merchant_and_endpoint
from .utils import new_id

logger = logging.getLogger("app.http")

STATIC_DIR = Path(__file__).resolve().parent / "static"
REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


def _error_response(status_code: int, code: str, message: str, headers: dict[str, str] | None = None) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message}},
        headers=headers,
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    configure_logging(settings.log_level)

    db = Database(settings.database_path, settings.db_busy_timeout_ms)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        settings.validate()
        db.init_schema()
        seed_merchant_and_endpoint(db, settings)
        with db.transaction() as conn:
            seed_example_rules(conn)
        queue.recover_inflight(db)

        stop_event = threading.Event()
        threads: list[threading.Thread] = []
        if settings.worker_enabled:
            for target, name in (
                (worker.run_forever, "payment-worker"),
                (dispatcher.run_forever, "webhook-dispatcher"),
            ):
                thread = threading.Thread(
                    target=target,
                    args=(db, settings, stop_event),
                    name=name,
                    daemon=True,
                )
                thread.start()
                threads.append(thread)
        if settings.jobs_active:
            jobs_thread = threading.Thread(
                target=jobs.run_forever,
                args=(db, settings, stop_event),
                name="maintenance-jobs",
                daemon=True,
            )
            jobs_thread.start()
            threads.append(jobs_thread)
        app.state.stop_event = stop_event

        # Realtime WS broadcaster (asyncio task on the request loop).
        registry = ws_service.ConnectionRegistry()
        broadcaster = ws_service.Broadcaster(db, settings, registry)
        app.state.ws_registry = registry
        app.state.ws_broadcaster = broadcaster
        if settings.ws_enabled:
            broadcaster.start()

        logger.info(
            "gateway ready",
            extra={"path": str(settings.database_path)},
        )

        try:
            yield
        finally:
            if settings.ws_enabled:
                await broadcaster.stop()
            stop_event.set()
            for thread in threads:
                thread.join(timeout=10)
            logger.info("gateway stopped")

    app = FastAPI(
        title="FluxPay — Payment Gateway API",
        version=__version__,
        description=(
            "Simplified payment gateway with PIX, credit/debit card simulation, "
            "idempotent creation, durable queue and signed webhooks with retries/DLQ."
        ),
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url=None,
        openapi_url="/openapi.json",
    )
    app.state.settings = settings
    app.state.db = db
    app.state.started_at = time.time()

    # ------------------------------------------------------------------ #
    # Middleware
    # ------------------------------------------------------------------ #
    @app.middleware("http")
    async def request_context(request: Request, call_next) -> Response:  # type: ignore[no-untyped-def]
        incoming = request.headers.get("x-request-id", "")
        request_id = incoming if REQUEST_ID_RE.match(incoming or "") else new_id("req", 8)
        token = request_id_var.set(request_id)
        started = time.perf_counter()

        content_length = request.headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > settings.max_body_bytes:
                    response = _error_response(
                        413, "payload_too_large", "Request body is too large."
                    )
                    response.headers["X-Request-Id"] = request_id
                    request_id_var.reset(token)
                    return response
            except ValueError:
                pass

        try:
            response = await call_next(request)
        except Exception:
            logger.exception(
                "unhandled request error",
                extra={"path": request.url.path, "request_id": request_id},
            )
            request_id_var.reset(token)
            raise

        duration_ms = int((time.perf_counter() - started) * 1000)
        response.headers["X-Request-Id"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
        if not request.url.path.startswith(("/docs", "/openapi.json")):
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
                "script-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'self'"
            )
        if request.url.path.startswith(("/v1/", "/demo/", "/health")):
            response.headers["Cache-Control"] = "no-store"

        if request.url.path not in {"/health"} and not request.url.path.startswith("/static/"):
            logger.info(
                "request completed",
                extra={
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": response.status_code,
                    "duration_ms": duration_ms,
                },
            )
        request_id_var.reset(token)
        return response

    # ------------------------------------------------------------------ #
    # Error handlers — stable public error envelope
    # ------------------------------------------------------------------ #
    @app.exception_handler(ApiError)
    async def api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=exc.to_payload(),
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        details = [
            {
                "loc": [str(part) for part in error.get("loc", [])],
                "msg": error.get("msg", "invalid value"),
                "type": error.get("type", "value_error"),
            }
            for error in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "validation_error",
                    "message": "Request validation failed.",
                    "details": details,
                }
            },
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {404: "not_found", 405: "method_not_allowed"}.get(exc.status_code, "http_error")
        return _error_response(exc.status_code, code, str(exc.detail))

    @app.exception_handler(sqlite3.Error)
    async def database_error_handler(request: Request, exc: sqlite3.Error) -> JSONResponse:
        logger.exception("database error")
        return _error_response(500, "database_error", "A database error occurred.")

    @app.exception_handler(Exception)
    async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled application error")
        return _error_response(500, "internal_error", "An unexpected error occurred.")

    # ------------------------------------------------------------------ #
    # Routes
    # ------------------------------------------------------------------ #
    app.include_router(payments_api.router)
    app.include_router(webhooks_api.router)
    app.include_router(system_api.router)
    app.include_router(crm_api.router)
    app.include_router(automations_api.router)
    app.include_router(analytics_api.router)
    app.include_router(data_api.router)
    app.include_router(agent_api.router)
    app.include_router(rag_api.router)
    app.include_router(events_api.router)
    if settings.demo_mode:
        app.include_router(demo_api.router)

    if STATIC_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

        @app.get("/", include_in_schema=False)
        def console() -> FileResponse:
            return FileResponse(
                STATIC_DIR / "index.html",
                media_type="text/html",
                headers={"Cache-Control": "no-store"},
            )

        @app.get("/favicon.svg", include_in_schema=False)
        def favicon() -> FileResponse:
            return FileResponse(STATIC_DIR / "favicon.svg", media_type="image/svg+xml")

    return app


app = create_app()
