"""SQLite persistence layer.

Design notes
------------
* WAL mode keeps readers unblocked while the worker writes.
* Every write transaction uses ``BEGIN IMMEDIATE`` so the idempotency check,
  payment insert and outbox enqueue are strictly serialized.  This is what
  makes concurrent ``Idempotency-Key`` requests produce exactly one payment,
  without relying on application-level locks.
* ``payment_events`` and ``webhook_attempts`` are append-only, enforced by
  triggers (defence in depth for the audit trail).
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

SCHEMA_VERSION = 1

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS merchants (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    api_key_hash TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS payments (
    id TEXT PRIMARY KEY,
    merchant_id TEXT NOT NULL REFERENCES merchants(id),
    amount_cents INTEGER NOT NULL CHECK (amount_cents > 0),
    currency TEXT NOT NULL DEFAULT 'BRL' CHECK (currency = 'BRL'),
    method TEXT NOT NULL CHECK (method IN ('pix', 'credit_card', 'debit_card')),
    status TEXT NOT NULL CHECK (status IN ('pending', 'processing', 'succeeded', 'failed')),
    description TEXT,
    customer_name TEXT,
    customer_email TEXT,
    metadata TEXT NOT NULL DEFAULT '{}',
    pix_txid TEXT,
    pix_qr_code TEXT,
    pix_expires_at TEXT,
    card_brand TEXT,
    card_last4 TEXT,
    authorization_code TEXT,
    failure_reason TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_payments_merchant_created
    ON payments(merchant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_payments_status_expiry
    ON payments(status, pix_expires_at);

CREATE TABLE IF NOT EXISTS payment_events (
    id TEXT PRIMARY KEY,
    payment_id TEXT NOT NULL REFERENCES payments(id),
    type TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_payment_events_payment
    ON payment_events(payment_id, created_at);

CREATE TRIGGER IF NOT EXISTS payment_events_no_update
BEFORE UPDATE ON payment_events
BEGIN
    SELECT RAISE(ABORT, 'payment_events is append-only');
END;

CREATE TRIGGER IF NOT EXISTS payment_events_no_delete
BEFORE DELETE ON payment_events
BEGIN
    SELECT RAISE(ABORT, 'payment_events is append-only');
END;

CREATE TABLE IF NOT EXISTS idempotency_keys (
    merchant_id TEXT NOT NULL REFERENCES merchants(id),
    key TEXT NOT NULL,
    request_fingerprint TEXT NOT NULL,
    response_status INTEGER NOT NULL,
    response_body TEXT NOT NULL,
    payment_id TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (merchant_id, key)
);

CREATE TABLE IF NOT EXISTS webhook_endpoints (
    id TEXT PRIMARY KEY,
    merchant_id TEXT NOT NULL REFERENCES merchants(id),
    url TEXT NOT NULL,
    secret TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_webhook_endpoints_merchant
    ON webhook_endpoints(merchant_id, active);

CREATE TABLE IF NOT EXISTS webhook_deliveries (
    id TEXT PRIMARY KEY,
    endpoint_id TEXT NOT NULL REFERENCES webhook_endpoints(id),
    payment_id TEXT NOT NULL REFERENCES payments(id),
    event_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'delivered', 'failed')),
    attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TEXT,
    last_response_code INTEGER,
    last_error TEXT,
    locked_until TEXT,
    delivered_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (event_id, endpoint_id)
);

CREATE INDEX IF NOT EXISTS idx_webhook_deliveries_due
    ON webhook_deliveries(status, next_attempt_at);
CREATE INDEX IF NOT EXISTS idx_webhook_deliveries_payment
    ON webhook_deliveries(payment_id, created_at);

CREATE TABLE IF NOT EXISTS webhook_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    delivery_id TEXT NOT NULL REFERENCES webhook_deliveries(id),
    attempt_no INTEGER NOT NULL,
    response_code INTEGER,
    error TEXT,
    duration_ms INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_webhook_attempts_delivery
    ON webhook_attempts(delivery_id, id);

CREATE TRIGGER IF NOT EXISTS webhook_attempts_no_update
BEFORE UPDATE ON webhook_attempts
BEGIN
    SELECT RAISE(ABORT, 'webhook_attempts is append-only');
END;

CREATE TRIGGER IF NOT EXISTS webhook_attempts_no_delete
BEFORE DELETE ON webhook_attempts
BEGIN
    SELECT RAISE(ABORT, 'webhook_attempts is append-only');
END;

CREATE TABLE IF NOT EXISTS queue_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL,
    payload TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'processing', 'done', 'failed')),
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 5,
    available_at TEXT NOT NULL,
    locked_until TEXT,
    last_error TEXT,
    dedupe_key TEXT UNIQUE,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_queue_due
    ON queue_messages(status, available_at);

CREATE TABLE IF NOT EXISTS demo_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS demo_webhook_receipts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    endpoint_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    signature_valid INTEGER NOT NULL,
    body TEXT NOT NULL,
    received_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_demo_receipts_received
    ON demo_webhook_receipts(received_at DESC);
"""


class Database:
    """Thin SQLite wrapper with per-operation connections (thread safe)."""

    def __init__(self, path: str | Path, busy_timeout_ms: int = 15000) -> None:
        self.path = Path(path)
        self.busy_timeout_ms = busy_timeout_ms

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(
            str(self.path),
            timeout=self.busy_timeout_ms / 1000,
            isolation_level=None,  # explicit transaction control
            check_same_thread=False,
        )
        conn.row_factory = sqlite3.Row
        conn.execute(f"PRAGMA busy_timeout = {int(self.busy_timeout_ms)}")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA synchronous = NORMAL")
        return conn

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        conn = self.connect()
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def transaction(self, immediate: bool = True) -> Iterator[sqlite3.Connection]:
        conn = self.connect()
        try:
            conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            yield conn
            conn.execute("COMMIT")
        except BaseException:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        finally:
            conn.close()

    def init_schema(self) -> None:
        conn = self.connect()
        try:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA wal_autocheckpoint = 1000")
            conn.executescript(SCHEMA_SQL)
            conn.execute(
                "INSERT INTO schema_meta(key, value) VALUES('schema_version', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (str(SCHEMA_VERSION),),
            )
        finally:
            conn.close()

    def healthy(self) -> bool:
        try:
            with self.connection() as conn:
                return conn.execute("SELECT 1").fetchone()[0] == 1
        except sqlite3.Error:
            return False
