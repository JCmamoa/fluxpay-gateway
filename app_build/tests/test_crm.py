"""CRM acceptance tests (CA-13): auto-created customers, e-mail unification
(case-insensitive, no duplicates), 360° view, notes/tags and declarative
segments.
"""

from __future__ import annotations

import pytest

from app.services import crm as crm_service
from app.services import worker

from helpers import card_payload, idem_key, pix_payload


def _create_payment(client, auth_headers, payload):
    response = client.post(
        "/v1/payments", json=payload, headers={**auth_headers, "Idempotency-Key": idem_key()}
    )
    assert response.status_code == 202, response.text
    return response.json()


def test_customer_created_on_first_payment_and_dedupes_case_insensitive(
    client, auth_headers, db, settings
):
    """CA-13: the customer row appears on the first payment carrying an e-mail
    and the CRM unifies subsequent payments by normalized e-mail (unique)."""
    first = _create_payment(
        client, auth_headers, card_payload(customer={"name": "Ana Silva", "email": "Ana@Exemplo.com"})
    )
    second = _create_payment(
        client,
        auth_headers,
        card_payload(amount=5000, customer={"name": "Ana Silva", "email": "ANA@EXEMPLO.COM"}),
    )
    worker.run_once(db, settings)

    customers = client.get("/v1/customers", headers=auth_headers).json()["data"]
    matches = [c for c in customers if c["email"] == "ana@exemplo.com"]
    assert len(matches) == 1, customers

    view = client.get(f"/v1/customers/{matches[0]['id']}", headers=auth_headers).json()
    assert view["email"] == "ana@exemplo.com"
    assert view["payments_count"] == 2
    assert view["total_succeeded_cents"] == first["amount"] + second["amount"]
    assert len(view["payments"]) == 2
    assert {p["id"] for p in view["payments"]} == {first["id"], second["id"]}
    # The payment lifecycle is mirrored to the customer timeline.
    timeline_types = {event["type"] for event in view["events"]}
    assert "payment.pending" in timeline_types


def test_payment_without_email_leaves_crm_untouched(client, auth_headers):
    response = client.post(
        "/v1/payments",
        json={
            "amount": 1000,
            "currency": "BRL",
            "method": "credit_card",
            "description": "Sem cliente",
            "card": {
                "number": "4242424242424242",
                "holder": "S NOME",
                "exp_month": 12,
                "exp_year": 2030,
                "cvv": "123",
            },
        },
        headers={**auth_headers, "Idempotency-Key": idem_key()},
    )
    assert response.status_code == 202, response.text

    customers = client.get("/v1/customers", headers=auth_headers).json()
    assert customers["total"] == 0 and customers["data"] == []

    missing = client.get("/v1/customers/cus_doesnotexist0001", headers=auth_headers)
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "customer_not_found"


def test_notes_and_tags_in_360_view(client, auth_headers, db, settings):
    _create_payment(client, auth_headers, pix_payload())
    worker.run_once(db, settings)
    customer = client.get("/v1/customers", headers=auth_headers).json()["data"][0]

    note = client.post(
        f"/v1/customers/{customer['id']}/notes",
        json={"body": "Cliente pediu fatura em duplicata.", "author": "suporte"},
        headers=auth_headers,
    )
    assert note.status_code == 201, note.text
    tag = client.post(
        f"/v1/customers/{customer['id']}/tags",
        json={"tag": "VIP"},
        headers=auth_headers,
    )
    assert tag.status_code == 201, tag.text

    # Tags are unique per customer (INSERT OR IGNORE, case-insensitive).
    client.post(f"/v1/customers/{customer['id']}/tags", json={"tag": "vip"}, headers=auth_headers)

    view = client.get(f"/v1/customers/{customer['id']}", headers=auth_headers).json()
    assert [n["body"] for n in view["notes"]] == ["Cliente pediu fatura em duplicata."]
    assert view["notes"][0]["author"] == "suporte"
    assert [t["tag"] for t in view["tags"]] == ["vip"]

    # Notes/tags trail: customer_events (timeline) + event hub broadcast.
    with db.connection() as conn:
        timeline = {row["type"] for row in conn.execute(
            "SELECT type FROM customer_events WHERE customer_id = ?", (customer["id"],)
        ).fetchall()}
        hub_types = {row["type"] for row in conn.execute("SELECT type FROM event_stream").fetchall()}
    assert {"note.created", "tag.added"} <= timeline
    assert {"crm.note.created", "crm.tag.added"} <= hub_types


def test_notes_and_tags_are_append_only(db, settings):
    """Append-only triggers (audit integrity): UPDATE/DELETE are rejected."""
    from app.services.seed import seed_merchant_and_endpoint

    seed_merchant_and_endpoint(db, settings)
    with db.transaction() as conn:
        customer = crm_service.ensure_customer(conn, "append-only@exemplo.com")
    crm_service.add_note(db, customer["id"], "nota original")

    with pytest.raises(Exception):
        with db.transaction() as conn:
            row = conn.execute(
                "SELECT id FROM customer_events LIMIT 1"
            ).fetchone()
            conn.execute("UPDATE customer_events SET type = 'adulterado' WHERE id = ?", (row["id"],))
    with pytest.raises(Exception):
        with db.transaction() as conn:
            row = conn.execute("SELECT id FROM customer_events LIMIT 1").fetchone()
            conn.execute("DELETE FROM customer_events WHERE id = ?", (row["id"],))

    with db.connection() as conn:
        body = conn.execute(
            "SELECT body FROM customer_notes WHERE customer_id = ?", (customer["id"],)
        ).fetchone()[0]
    assert body == "nota original"


def test_declarative_segment_computed_and_recomputed(client, auth_headers, db, settings):
    """Segments are declarative; members are computed from live stats and
    refreshed by the segment-recompute job."""
    _create_payment(client, auth_headers, card_payload(amount=99000))
    worker.run_once(db, settings)

    created = client.post(
        "/v1/segments",
        json={
            "name": "cartao alto valor",
            "definition": {"method": "credit_card", "min_amount_cents": 50000},
        },
        headers=auth_headers,
    )
    assert created.status_code == 201, created.text
    segment = created.json()
    assert segment["members_count"] == 1
    assert segment["members"][0]["email"] == "bruno@exemplo.com"

    # A brand new high-ticket card customer joins the segment on recompute.
    _create_payment(
        client,
        auth_headers,
        card_payload(amount=60000, customer={"name": "Carol Dias", "email": "carol@exemplo.com"}),
    )
    worker.run_once(db, settings)
    changed = crm_service.recompute_segments(db)
    assert changed >= 1  # only the new member is (re)added; idempotent for the existing one

    listed = client.get("/v1/segments", headers=auth_headers).json()
    high_ticket = next(s for s in listed["data"] if s["name"] == "cartao alto valor")
    assert high_ticket["members_count"] == 2

    detail = crm_service.get_segment(db, high_ticket["id"])
    assert {m["email"] for m in detail["members"]} == {
        "bruno@exemplo.com",
        "carol@exemplo.com",
    }

    filtered = client.get(
        "/v1/customers", params={"segment": "cartao alto valor"}, headers=auth_headers
    ).json()
    assert filtered["total"] == 2


def test_customer_list_filters(client, auth_headers, db, settings):
    _create_payment(client, auth_headers, card_payload())  # bruno, credit_card
    _create_payment(client, auth_headers, pix_payload())  # ana, pix
    worker.run_once(db, settings)

    by_method = client.get(
        "/v1/customers", params={"method": "credit_card"}, headers=auth_headers
    ).json()["data"]
    assert [c["email"] for c in by_method] == ["bruno@exemplo.com"]

    tagged = client.get("/v1/customers", params={"tag": "vip"}, headers=auth_headers).json()
    assert tagged["total"] == 0

    search = client.get("/v1/customers", params={"q": "ana"}, headers=auth_headers).json()
    assert search["total"] == 1


def test_unknown_customer_returns_404(client, auth_headers):
    missing = client.get("/v1/customers/cus_doesnotexist0001", headers=auth_headers)
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "customer_not_found"

