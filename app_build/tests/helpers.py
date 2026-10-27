"""Small request builders shared by tests."""

from __future__ import annotations

import uuid

VALID_CARD = "4242424242424242"
DECLINED_CARD = "4000000000000002"
INSUFFICIENT_CARD = "4000000000009995"


def pix_payload(amount: int = 12990, **overrides) -> dict:
    payload = {
        "amount": amount,
        "currency": "BRL",
        "method": "pix",
        "description": "Pedido #123",
        "customer": {"name": "Ana Silva", "email": "ana@exemplo.com"},
        "metadata": {"order_id": "ord_123"},
    }
    payload.update(overrides)
    return payload


def card_payload(number: str = VALID_CARD, method: str = "credit_card", **overrides) -> dict:
    payload = {
        "amount": 25990,
        "currency": "BRL",
        "method": method,
        "description": "Assinatura anual",
        "customer": {"name": "Bruno Costa", "email": "bruno@exemplo.com"},
        "card": {
            "number": number,
            "holder": "BRUNO COSTA",
            "exp_month": 12,
            "exp_year": 2030,
            "cvv": "123",
        },
    }
    payload.update(overrides)
    return payload


def idem_key() -> str:
    return f"test_{uuid.uuid4().hex}"
