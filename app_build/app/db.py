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

SCHEMA_VERSION = 2

V0_3_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS event_stream (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    payload TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_event_stream_type
    ON event_stream(type, created_at);
CREATE INDEX IF NOT EXISTS idx_event_stream_created
    ON event_stream(created_at);

-- Append-only, except deletions issued by the retention job (EVENT_STREAM_RETENTION_DAYS).
CREATE TRIGGER IF NOT EXISTS event_stream_no_update
BEFORE UPDATE ON event_stream
BEGIN
    SELECT RAISE(ABORT, 'event_stream is append-only');
END;

CREATE TABLE IF NOT EXISTS customers (
    id TEXT PRIMARY KEY,
    email TEXT NOT NULL,
    name TEXT,
    phone TEXT,
    lifecycle_status TEXT NOT NULL DEFAULT 'new'
        CHECK (lifecycle_status IN ('new', 'active', 'dormant', 'blocked')),
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- Natural key is the e-mail, matched case-insensitively (CA-13).
CREATE UNIQUE INDEX IF NOT EXISTS idx_customers_email_ci ON customers(LOWER(email));
CREATE INDEX IF NOT EXISTS idx_customers_lifecycle ON customers(lifecycle_status);

CREATE TABLE IF NOT EXISTS customer_events (
    id TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL REFERENCES customers(id),
    type TEXT NOT NULL,
    payload TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_customer_events_customer
    ON customer_events(customer_id, created_at);

CREATE TRIGGER IF NOT EXISTS customer_events_no_update
BEFORE UPDATE ON customer_events
BEGIN
    SELECT RAISE(ABORT, 'customer_events is append-only');
END;

CREATE TRIGGER IF NOT EXISTS customer_events_no_delete
BEFORE DELETE ON customer_events
BEGIN
    SELECT RAISE(ABORT, 'customer_events is append-only');
END;

CREATE TABLE IF NOT EXISTS customer_notes (
    id TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL REFERENCES customers(id),
    author TEXT NOT NULL DEFAULT 'operator',
    body TEXT NOT NULL,
    pinned INTEGER NOT NULL DEFAULT 0 CHECK (pinned IN (0, 1)),
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_customer_notes_customer
    ON customer_notes(customer_id, created_at);

CREATE TABLE IF NOT EXISTS customer_tags (
    customer_id TEXT NOT NULL REFERENCES customers(id),
    tag TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (customer_id, tag)
);

CREATE TABLE IF NOT EXISTS segments (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    definition TEXT NOT NULL DEFAULT '{}',
    computed_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS segment_members (
    segment_id TEXT NOT NULL REFERENCES segments(id),
    customer_id TEXT NOT NULL REFERENCES customers(id),
    added_at TEXT NOT NULL,
    PRIMARY KEY (segment_id, customer_id)
);

CREATE INDEX IF NOT EXISTS idx_segment_members_customer ON segment_members(customer_id);

CREATE TABLE IF NOT EXISTS automation_rules (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    event_type TEXT NOT NULL,
    conditions TEXT NOT NULL DEFAULT '{}',
    action_type TEXT NOT NULL
        CHECK (action_type IN ('alerta_no_console', 'tag_customer', 'retry_webhook')),
    action_config TEXT NOT NULL DEFAULT '{}',
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_automation_rules_event ON automation_rules(event_type, enabled);

CREATE TABLE IF NOT EXISTS automation_runs (
    id TEXT PRIMARY KEY,
    rule_id TEXT,
    event_type TEXT NOT NULL,
    event_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'executed'
        CHECK (status IN ('executed', 'skipped', 'error', 'duplicate')),
    detail TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    UNIQUE (rule_id, event_id)
);

CREATE TRIGGER IF NOT EXISTS automation_runs_no_update
BEFORE UPDATE ON automation_runs
BEGIN
    SELECT RAISE(ABORT, 'automation_runs is append-only');
END;

CREATE TABLE IF NOT EXISTS rag_documents (
    id TEXT PRIMARY KEY,
    source_type TEXT NOT NULL,
    source_ref TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL DEFAULT '',
    content_hash TEXT NOT NULL,
    indexed_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_rag_documents_source
    ON rag_documents(source_type, source_ref);

CREATE TABLE IF NOT EXISTS rag_chunks (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES rag_documents(id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL,
    text TEXT NOT NULL,
    terms TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_rag_chunks_document ON rag_chunks(document_id, ordinal);

CREATE TABLE IF NOT EXISTS analytics_daily (
    day TEXT NOT NULL,
    method TEXT NOT NULL CHECK (method IN ('pix', 'credit_card', 'debit_card')),
    attempts INTEGER NOT NULL DEFAULT 0,
    succeeded INTEGER NOT NULL DEFAULT 0,
    failed INTEGER NOT NULL DEFAULT 0,
    volume_cents INTEGER NOT NULL DEFAULT 0,
    approval_rate REAL NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (day, method)
);

CREATE INDEX IF NOT EXISTS idx_analytics_daily_day ON analytics_daily(day);

CREATE TABLE IF NOT EXISTS agent_logs (
    id TEXT PRIMARY KEY,
    question TEXT NOT NULL,
    rag_sources TEXT NOT NULL DEFAULT '[]',
    action_taken TEXT,
    answer TEXT NOT NULL DEFAULT '',
    latency_ms INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TRIGGER IF NOT EXISTS agent_logs_no_update
BEFORE UPDATE ON agent_logs
BEGIN
    SELECT RAISE(ABORT, 'agent_logs is append-only');
END;

CREATE TABLE IF NOT EXISTS agent_pending_actions (
    id TEXT PRIMARY KEY,
    token TEXT NOT NULL UNIQUE,
    action_type TEXT NOT NULL CHECK (action_type IN ('criar_nota', 'retry_webhook')),
    payload TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'executed', 'expired', 'cancelled')),
    expires_at TEXT NOT NULL,
    executed_at TEXT,
    created_at TEXT NOT NULL
);
"""

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
            conn.executescript(V0_3_SCHEMA_SQL)
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
