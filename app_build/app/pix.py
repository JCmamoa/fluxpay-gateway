"""Simulated PIX (EMV QR / copia-e-cola) helpers.

The payload follows the Banco Central EMV® QRCPS-MPM structure closely enough
to be parsed by PIX tooling, but no real PSP is contacted.  ``txid`` and the
BR Code are generated synchronously during payment creation, as required.
"""

from __future__ import annotations

import base64
import secrets
import unicodedata

try:  # optional rendering of a scannable preview in the demo console
    import segno
except ImportError:  # pragma: no cover - only when optional dependency is absent
    segno = None  # type: ignore[assignment]


def _ascii(value: str, max_length: int) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    stripped = normalized.encode("ascii", "ignore").decode("ascii")
    cleaned = " ".join(stripped.split())
    return cleaned[:max_length]


def _tlv(tag: str, value: str) -> str:
    if len(value) > 99:
        raise ValueError(f"EMV field {tag} is too long")
    return f"{tag}{len(value):02d}{value}"


def crc16_ccitt(payload: str) -> str:
    crc = 0xFFFF
    for byte in payload.encode("utf-8"):
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return f"{crc:04X}"


def generate_txid() -> str:
    # PIX allows 26-35 alphanumeric characters. 26 keeps the BR Code compact.
    return secrets.token_hex(13).upper()


def build_pix_payload(
    *,
    key: str,
    merchant_name: str,
    city: str,
    amount_cents: int,
    txid: str,
    description: str | None = None,
) -> str:
    merchant_account = _tlv("00", "BR.GOV.BCB.PIX") + _tlv("01", key)
    if description:
        merchant_account += _tlv("02", _ascii(description, 72))

    amount = f"{amount_cents // 100}.{amount_cents % 100:02d}"
    payload = (
        _tlv("00", "01")
        + _tlv("26", merchant_account)
        + _tlv("52", "0000")
        + _tlv("53", "986")
        + _tlv("54", amount)
        + _tlv("58", "BR")
        + _tlv("59", _ascii(merchant_name, 25) or "LOJA DEMO")
        + _tlv("60", _ascii(city, 15) or "SAO PAULO")
        + _tlv("62", _tlv("05", txid))
    )
    payload += "6304"
    return payload + crc16_ccitt(payload)


def pix_qr_image_data_uri(payload: str) -> str | None:
    """Return an SVG data URI for the console preview, if segno is available."""
    if segno is None:
        return None
    qr = segno.make(payload, error="m")
    svg = qr.svg_inline(scale=6, border=2, dark="#0b1220", light="#ffffff")
    encoded = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    return f"data:image/svg+xml;base64,{encoded}"
