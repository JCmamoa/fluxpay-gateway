"""Cryptographic and card-simulation primitives."""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import time


def hash_api_key(api_key: str) -> str:
    """API keys are high-entropy secrets; a fast digest is appropriate."""
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()


def generate_api_key(prefix: str = "sk_demo") -> str:
    return f"{prefix}_{secrets.token_urlsafe(24)}"


def sign_webhook(secret: str, timestamp: int, raw_body: bytes | str) -> str:
    body = raw_body.encode("utf-8") if isinstance(raw_body, str) else raw_body
    signed = str(timestamp).encode("ascii") + b"." + body
    return hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()


def parse_signature_header(header: str) -> tuple[int, list[str]]:
    timestamp: int | None = None
    signatures: list[str] = []
    for part in header.split(","):
        piece = part.strip()
        if not piece or "=" not in piece:
            continue
        key, value = piece.split("=", 1)
        if key.strip() == "t":
            try:
                timestamp = int(value.strip())
            except ValueError as exc:
                raise ValueError("Invalid signature timestamp") from exc
        elif key.strip() == "v1":
            signatures.append(value.strip())
    if timestamp is None or not signatures:
        raise ValueError("Malformed signature header")
    return timestamp, signatures


def verify_webhook_signature(
    secret: str,
    header: str | None,
    raw_body: bytes | str,
    tolerance_seconds: int = 300,
    now: int | None = None,
) -> tuple[bool, str | None]:
    """Verify ``t=<ts>,v1=<hmac>`` with a replay window."""
    if not header:
        return False, "missing_signature"
    try:
        timestamp, signatures = parse_signature_header(header)
    except ValueError as exc:
        return False, str(exc)
    current = int(time.time()) if now is None else now
    if abs(current - timestamp) > tolerance_seconds:
        return False, "timestamp_outside_tolerance"
    expected = sign_webhook(secret, timestamp, raw_body)
    if not any(hmac.compare_digest(expected, candidate) for candidate in signatures):
        return False, "invalid_signature"
    return True, None


def normalize_card_number(value: str) -> str:
    return re.sub(r"[\s-]+", "", value.strip())


def luhn_valid(number: str) -> bool:
    digits = [int(char) for char in number if char.isdigit()]
    if not digits:
        return False
    checksum = 0
    parity = len(digits) % 2
    for index, digit in enumerate(digits):
        if index % 2 == parity:
            digit *= 2
            if digit > 9:
                digit -= 9
        checksum += digit
    return checksum % 10 == 0


def detect_card_brand(number: str) -> str:
    if number.startswith("4"):
        return "visa"
    if re.match(r"^(5[1-5]|2[2-7])", number):
        return "mastercard"
    if re.match(r"^3[47]", number):
        return "amex"
    if re.match(r"^(4011|4312|4389|5041|5066|5090|6277|6362|6363|650|651|655)", number):
        return "elo"
    return "other"


# Sandbox outcomes, derived from the stored last four digits so the PAN never
# has to reach the worker.
DECLINE_LAST4 = {"0002": "card_declined", "9995": "insufficient_funds"}


def simulated_card_outcome(last4: str) -> tuple[bool, str | None]:
    reason = DECLINE_LAST4.get(last4)
    return (reason is None, reason)
