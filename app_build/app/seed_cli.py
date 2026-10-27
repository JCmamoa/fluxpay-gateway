"""CLI seed utility: ``python -m app.seed_cli [--print-key]``."""

from __future__ import annotations

import argparse
import json
from dataclasses import replace

from .config import Settings
from .db import Database
from .services.seed import seed_merchant_and_endpoint
from .utils import mask_secret


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed the demo merchant and webhook endpoint.")
    parser.add_argument("--merchant-id", default=None)
    parser.add_argument("--merchant-name", default=None)
    parser.add_argument("--api-key", default=None, help="Use a specific API key (stored hashed)")
    parser.add_argument("--webhook-url", default=None)
    parser.add_argument("--webhook-secret", default=None)
    parser.add_argument("--database", default=None)
    parser.add_argument("--print-key", action="store_true", help="Print the full configured API key")
    args = parser.parse_args()

    settings = Settings.from_env()
    overrides = {}
    if args.merchant_id:
        overrides["merchant_id"] = args.merchant_id
    if args.merchant_name:
        overrides["merchant_name"] = args.merchant_name
    if args.api_key:
        overrides["api_key"] = args.api_key
    if args.webhook_url:
        overrides["webhook_url"] = args.webhook_url
    if args.webhook_secret:
        overrides["webhook_secret"] = args.webhook_secret
    if args.database:
        from pathlib import Path

        overrides["database_path"] = Path(args.database)
    settings = replace(settings, **overrides)
    settings.validate()

    db = Database(settings.database_path, settings.db_busy_timeout_ms)
    db.init_schema()
    merchant = seed_merchant_and_endpoint(db, settings)

    print(
        json.dumps(
            {
                "merchant": merchant,
                "api_key": settings.api_key if args.print_key else mask_secret(settings.api_key, 4),
                "webhook_url": settings.webhook_url,
                "webhook_secret": settings.webhook_secret if args.print_key else mask_secret(settings.webhook_secret, 4),
                "database": str(settings.database_path),
            },
            indent=2,
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
