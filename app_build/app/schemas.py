"""Pydantic request models and API validation rules."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .security import detect_card_brand, luhn_valid, normalize_card_number

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Customer(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    email: str = Field(min_length=3, max_length=254)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        if not EMAIL_RE.match(value):
            raise ValueError("must be a valid email address")
        return value.lower()


class Card(StrictModel):
    number: str
    holder: str = Field(min_length=2, max_length=80)
    exp_month: int = Field(ge=1, le=12)
    exp_year: int = Field(ge=2000, le=2100)
    cvv: str = Field(min_length=3, max_length=4, pattern=r"^\d{3,4}$")

    @field_validator("number", mode="before")
    @classmethod
    def normalize_number(cls, value: Any) -> str:
        digits = normalize_card_number(str(value))
        if not digits.isdigit():
            raise ValueError("must contain only digits, spaces or hyphens")
        return digits

    @field_validator("number")
    @classmethod
    def validate_number(cls, value: str) -> str:
        if not 12 <= len(value) <= 19:
            raise ValueError("must have between 12 and 19 digits")
        if not luhn_valid(value):
            raise ValueError("failed the Luhn checksum")
        return value

    @field_validator("holder")
    @classmethod
    def normalize_holder(cls, value: str) -> str:
        return " ".join(value.split())

    @model_validator(mode="after")
    def validate_expiry(self) -> "Card":
        now = datetime.now(timezone.utc)
        if (self.exp_year, self.exp_month) < (now.year, now.month):
            raise ValueError("card is expired")
        return self

    @property
    def brand(self) -> str:
        return detect_card_brand(self.number)

    @property
    def last4(self) -> str:
        return self.number[-4:]


class PaymentCreate(StrictModel):
    amount: int = Field(gt=0, le=100_000_000, description="Amount in cents (BRL)")
    currency: Literal["BRL"] = "BRL"
    method: Literal["pix", "credit_card", "debit_card"]
    description: str | None = Field(default=None, max_length=500)
    customer: Customer | None = None
    card: Card | None = None
    metadata: dict[str, Any] | None = None

    @field_validator("description")
    @classmethod
    def normalize_description(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = " ".join(value.split())
        return cleaned or None

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is None:
            return None
        if len(value) > 30:
            raise ValueError("metadata may contain at most 30 keys")
        for key in value:
            if not isinstance(key, str) or len(key) > 64:
                raise ValueError("metadata keys must be strings of at most 64 characters")
        try:
            encoded = json.dumps(value, ensure_ascii=False).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise ValueError("metadata must be JSON serializable") from exc
        if len(encoded) > 4096:
            raise ValueError("metadata must be at most 4096 bytes when serialized")
        return value

    @model_validator(mode="after")
    def validate_card_presence(self) -> "PaymentCreate":
        if self.method == "pix" and self.card is not None:
            raise ValueError("card must not be sent for pix payments")
        if self.method in {"credit_card", "debit_card"} and self.card is None:
            raise ValueError("card is required for credit_card and debit_card payments")
        return self


class SimulatePaymentRequest(StrictModel):
    result: Literal["paid"] = "paid"


class WebhookEndpointPatch(StrictModel):
    url: str | None = Field(default=None, min_length=8, max_length=2048)
    active: bool | None = None

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value.startswith(("http://", "https://")):
            raise ValueError("url must use http or https")
        return value


class DemoFailureRequest(StrictModel):
    count: int = Field(ge=0, le=20)
