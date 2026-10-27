"""PIX EMV payload and card helper unit tests."""

from __future__ import annotations

from app.pix import build_pix_payload, crc16_ccitt, generate_txid, pix_qr_image_data_uri
from app.security import detect_card_brand, luhn_valid, normalize_card_number, simulated_card_outcome


def test_pix_payload_structure_and_crc():
    txid = generate_txid()
    assert len(txid) == 26 and txid.isalnum()
    payload = build_pix_payload(
        key="demo@fluxpay.local",
        merchant_name="Loja Demo",
        city="SAO PAULO",
        amount_cents=12990,
        txid=txid,
        description="Pedido 123",
    )
    assert payload.startswith("000201")
    assert "BR.GOV.BCB.PIX" in payload
    assert "demo@fluxpay.local" in payload
    assert "5405129.90" not in payload  # TLV length is computed, not hardcoded
    assert "129.90" in payload
    assert txid in payload
    assert payload.endswith(crc16_ccitt(payload[:-4]))
    assert len(payload) < 512


def test_qr_svg_is_generated():
    payload = build_pix_payload(
        key="demo@fluxpay.local",
        merchant_name="Loja",
        city="SAO PAULO",
        amount_cents=100,
        txid=generate_txid(),
    )
    uri = pix_qr_image_data_uri(payload)
    assert uri is not None and uri.startswith("data:image/svg+xml;base64,")


def test_luhn_and_brand_detection():
    assert luhn_valid("4242424242424242")
    assert luhn_valid("4000000000000002")
    assert luhn_valid("4000000000009995")
    assert not luhn_valid("4242424242424241")
    assert detect_card_brand("4242424242424242") == "visa"
    assert detect_card_brand("5555555555554444") == "mastercard"
    assert detect_card_brand("378282246310005") == "amex"
    assert normalize_card_number("4242 4242-4242 4242") == "4242424242424242"


def test_card_simulator_outcomes():
    assert simulated_card_outcome("4242") == (True, None)
    assert simulated_card_outcome("0002") == (False, "card_declined")
    assert simulated_card_outcome("9995") == (False, "insufficient_funds")
