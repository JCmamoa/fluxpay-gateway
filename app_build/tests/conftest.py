"""Shared pytest fixtures: temporary SQLite DB, TestClient and a live uvicorn server."""

from __future__ import annotations

import socket
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn
from fastapi.testclient import TestClient

from app.config import Settings
from app.db import Database
from app.main import create_app
from app.services.seed import seed_merchant_and_endpoint

API_KEY = "sk_test_fluxpay_automated_2026"
SECOND_API_KEY = "sk_test_other_merchant_2026"


def build_settings(tmp_path: Path, **overrides) -> Settings:
    values = dict(
        app_env="test",
        demo_mode=True,
        demo_expose_api_key=True,
        database_path=tmp_path / "gateway-test.db",
        merchant_id="mch_test",
        merchant_name="Loja de Testes",
        api_key=API_KEY,
        webhook_secret="whsec_test_secret_123456",
        webhook_url="http://127.0.0.1:9/demo/webhook-receiver?endpoint=whe_demo",
        worker_enabled=False,
        webhook_retry_schedule=(0.0, 0.0, 0.0),
        webhook_max_attempts=3,
        webhook_jitter_ratio=0.0,
        webhook_timeout_seconds=2.0,
        card_authorization_delay_ms=0,
        pix_expires_minutes=30,
        log_level="ERROR",
    )
    values.update(overrides)
    return Settings(**values)


@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    return build_settings(tmp_path)


@pytest.fixture()
def db(settings: Settings) -> Database:
    database = Database(settings.database_path, settings.db_busy_timeout_ms)
    database.init_schema()
    seed_merchant_and_endpoint(database, settings)
    return database


@pytest.fixture()
def app(settings: Settings):
    return create_app(settings)


@pytest.fixture()
def client(app) -> TestClient:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {API_KEY}"}


@pytest.fixture()
def live_server(settings: Settings):
    """Real HTTP server for concurrency tests (TestClient is request-at-a-time)."""
    app = create_app(settings)
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=0,
        log_level="error",
        log_config=None,
        access_log=False,
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="test-uvicorn", daemon=True)
    thread.start()

    deadline = time.time() + 20
    while not server.started and time.time() < deadline:
        if not thread.is_alive():
            raise RuntimeError("uvicorn test server exited before starting")
        time.sleep(0.05)
    if not server.started:
        raise RuntimeError("uvicorn test server did not start in time")

    sockets = getattr(server.servers[0], "sockets", [])
    assert sockets, "uvicorn did not expose a listening socket"
    port = sockets[0].getsockname()[1]
    base_url = f"http://127.0.0.1:{port}"

    deadline = time.time() + 15
    while time.time() < deadline:
        try:
            response = httpx.get(f"{base_url}/health", timeout=1.0)
            if response.status_code == 200:
                break
        except httpx.HTTPError:
            pass
        time.sleep(0.05)
    else:
        raise RuntimeError("health endpoint did not become ready")

    try:
        yield base_url
    finally:
        server.should_exit = True
        thread.join(timeout=15)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]
