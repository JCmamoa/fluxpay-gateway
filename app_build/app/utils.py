"""Small, dependency-free helpers shared across the application."""

from __future__ import annotations

import json
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

ISO_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    """Current UTC time as a lexicographically sortable ISO-8601 string."""
    return utc_now().strftime(ISO_FORMAT)


def iso_from(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime(ISO_FORMAT)


def iso_after(seconds: float, base: datetime | None = None) -> str:
    moment = (base or utc_now()) + timedelta(seconds=seconds)
    return iso_from(moment)


def iso_minutes_after(minutes: float, base: datetime | None = None) -> str:
    return iso_after(minutes * 60, base=base)


def parse_iso(value: str) -> datetime:
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def new_id(prefix: str, token_bytes: int = 12) -> str:
    return f"{prefix}_{secrets.token_hex(token_bytes)}"


def canonical_json(value: Any) -> str:
    """Canonical JSON used for fingerprints, stored bodies and idempotent replay."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def mask_secret(value: str, visible: int = 4) -> str:
    if not value:
        return ""
    if len(value) <= visible:
        return "*" * len(value)
    return "*" * (len(value) - visible) + value[-visible:]


def truncate(value: str | None, limit: int = 500) -> str | None:
    if value is None:
        return None
    return value[:limit]
