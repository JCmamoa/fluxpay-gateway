"""Seeding and operational helpers for the demo merchant."""

from __future__ import annotations

import logging
from typing import Any

from ..config import Settings
from ..security import hash_api_key
from ..utils import iso_now, mask_secret

logger = logging.getLogger("app.seed")

DEMO_WEBHOOK_ENDPOINT_ID = "whe_demo"


def seed_merchant_and_endpoint(db: Any, settings: Settings) -> dict[str, Any]:
    """Idempotently ensure the configured merchant + webhook endpoint exist."""
    now = iso_now()
    api_key_hash = hash_api_key(settings.api_key)

    with db.transaction() as conn:
        merchant = conn.execute(
            "SELECT * FROM merchants WHERE id = ?", (settings.merchant_id,)
        ).fetchone()
        if merchant is None:
            # Guard against a rotated key already belonging to another merchant.
            conflict = conn.execute(
                "SELECT id FROM merchants WHERE api_key_hash = ?", (api_key_hash,)
            ).fetchone()
            if conflict is not None:
                conn.execute(
                    "UPDATE merchants SET name = ? WHERE id = ?",
                    (settings.merchant_name, conflict["id"]),
                )
                merchant_id = conflict["id"]
            else:
                conn.execute(
                    "INSERT INTO merchants(id, name, api_key_hash, created_at) VALUES (?, ?, ?, ?)",
                    (settings.merchant_id, settings.merchant_name, api_key_hash, now),
                )
                merchant_id = settings.merchant_id
        else:
            merchant_id = merchant["id"]
            conn.execute(
                "UPDATE merchants SET name = ?, api_key_hash = ? WHERE id = ?",
                (settings.merchant_name, api_key_hash, merchant_id),
            )

        endpoint = conn.execute(
            "SELECT * FROM webhook_endpoints WHERE id = ?", (DEMO_WEBHOOK_ENDPOINT_ID,)
        ).fetchone()
        if endpoint is None:
            conn.execute(
                """
                INSERT INTO webhook_endpoints(id, merchant_id, url, secret, active, created_at)
                VALUES (?, ?, ?, ?, 1, ?)
                """,
                (
                    DEMO_WEBHOOK_ENDPOINT_ID,
                    merchant_id,
                    settings.webhook_url,
                    settings.webhook_secret,
                    now,
                ),
            )
        else:
            conn.execute(
                """
                UPDATE webhook_endpoints
                SET merchant_id = ?, url = ?, secret = ?, active = 1
                WHERE id = ?
                """,
                (merchant_id, settings.webhook_url, settings.webhook_secret, DEMO_WEBHOOK_ENDPOINT_ID),
            )

        merchant_row = conn.execute(
            "SELECT id, name, created_at FROM merchants WHERE id = ?", (merchant_id,)
        ).fetchone()

    logger.info(
        "demo seed ensured",
        extra={"merchant_id": merchant_id},
    )
    logger.info("demo API key: %s", mask_secret(settings.api_key, visible=4))
    return dict(merchant_row)


def get_demo_setting(db: Any, key: str, default: str = "0") -> str:
    with db.connection() as conn:
        row = conn.execute("SELECT value FROM demo_settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_demo_setting(db: Any, key: str, value: str) -> None:
    now = iso_now()
    with db.transaction() as conn:
        conn.execute(
            """
            INSERT INTO demo_settings(key, value, updated_at) VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """,
            (key, value, now),
        )
