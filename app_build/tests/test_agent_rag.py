"""Agent IA + RAG tests (CA-15 / CA-16 / CA-17 / RF-16 / RF-17 / RF-19):
grounded answers with sources, no-LLM deterministic fallback, two-step
sensitive actions, RAG incremental indexing and PII redaction.
"""

from __future__ import annotations

import time

import pytest

from app.services import rag as rag_service
from app.services import worker
from app.services.agent import register_pending
from app.services.seed import seed_merchant_and_endpoint

from conftest import build_settings
from helpers import card_payload, idem_key, pix_payload


@pytest.fixture()
def settings(tmp_path):
    """No LLM configured (deterministic engine); generous rate limit."""
    return build_settings(tmp_path, agent_rate_limit_per_minute=1000)


def _pix_paid(client, auth_headers, db, settings, amount=12990):
    response = client.post(
        "/v1/payments",
        json=pix_payload(amount=amount),
        headers={**auth_headers, "Idempotency-Key": idem_key()},
    )
    assert response.status_code == 202, response.text
    payment = response.json()
    paid = client.post(f"/v1/payments/{payment['id']}/simulate-payment", headers=auth_headers)
    assert paid.status_code == 202, paid.text
    worker.run_once(db, settings)
    return payment


# --------------------------------------------------------------------------- #
# CA-15: grounded metric answer with source, validated against the DB
# --------------------------------------------------------------------------- #
def test_agent_answers_pix_metrics_with_source(client, auth_headers, db, settings):
    payment = _pix_paid(client, auth_headers, db, settings)

    reply = client.post(
        "/v1/agent/chat", json={"question": "Quantos PIX aprovados hoje?"}, headers=auth_headers
    ).json()

    with db.connection() as conn:
        truth = conn.execute(
            "SELECT COUNT(*) AS c, COALESCE(SUM(amount_cents), 0) AS v FROM payments "
            "WHERE method = 'pix' AND status = 'succeeded' AND id = ?",
            (payment["id"],),
        ).fetchone()
    assert truth["c"] == 1

    assert reply["engine"] == "local"
    assert reply["tools_used"]
    assert str(truth["c"]) in reply["answer"]
    assert "pix" in reply["answer"].lower()
    # Source of truth is cited.
    citations = {source["citation"] for source in reply["sources"]}
    assert "db:payments" in citations
    assert reply["sources"]
    assert reply["latency_ms"] >= 0


# --------------------------------------------------------------------------- #
# CA-16: deterministic fallback without LLM; never invents evidence
# --------------------------------------------------------------------------- #
def test_no_llm_fallback_is_deterministic_and_fast(client, auth_headers, settings):
    assert not settings.llm_configured
    started = time.perf_counter()
    reply = client.post(
        "/v1/agent/chat",
        json={"question": "Quantas cobranças falharam hoje?"},
        headers=auth_headers,
    ).json()
    wall = time.perf_counter() - started

    assert reply["engine"] == "local"
    assert reply["latency_ms"] <= 1500
    assert wall < 1.5
    assert "0" in reply["answer"]  # truthful zero, not invented


def test_agent_says_not_enough_data(client, auth_headers):
    reply = client.post(
        "/v1/agent/chat", json={"question": "bom dia, como está o clima?"}, headers=auth_headers
    ).json()
    assert "Sem dados suficientes" in reply["answer"]
    assert reply["engine"] == "local"


def test_agent_answers_payment_lookup(client, auth_headers, db, settings):
    response = client.post(
        "/v1/payments",
        json=card_payload(),
        headers={**auth_headers, "Idempotency-Key": idem_key()},
    )
    payment = response.json()
    worker.run_once(db, settings)

    reply = client.post(
        "/v1/agent/chat", json={"question": f"qual o status do {payment['id']}?"}, headers=auth_headers
    ).json()
    assert payment["id"] in reply["answer"]
    assert "succeeded" in reply["answer"]

    missing = client.post(
        "/v1/agent/chat",
        json={"question": "e do pay_00000000deadbeef?"},
        headers=auth_headers,
    ).json()
    assert "Não encontrei o pagamento" in missing["answer"]


# --------------------------------------------------------------------------- #
# CA-17: sensitive actions are two-step confirmed
# --------------------------------------------------------------------------- #
def test_criar_nota_requires_confirmation_and_only_executes_after(
    client, auth_headers, db, settings
):
    seed_merchant_and_endpoint(db, settings)
    with db.transaction() as conn:
        from app.services.crm import ensure_customer

        ensure_customer(conn, "bruno@exemplo.com", "Bruno Costa")

    with db.connection() as conn:
        before = conn.execute("SELECT COUNT(*) FROM customer_notes").fetchone()[0]
    assert before == 0

    reply = client.post(
        "/v1/agent/chat",
        json={
            "question": (
                "adicione uma nota para bruno@exemplo.com "
                "\"cliente pediu segunda via da fatura\""
            )
        },
        headers=auth_headers,
    ).json()
    assert reply["confirmation_required"] is True
    assert reply["action"]["type"] == "criar_nota"
    token = reply["action"]["token"]
    assert reply["action"]["expires_at"]

    # Nothing changed before confirmation (CA-17).
    with db.connection() as conn:
        after = conn.execute("SELECT COUNT(*) FROM customer_notes").fetchone()[0]
    assert after == 0

    confirmed = client.post("/v1/agent/confirm", json={"token": token}, headers=auth_headers)
    assert confirmed.status_code == 200, confirmed.text
    body = confirmed.json()
    assert body["status"] == "executed"
    assert body["result"]["note"]["body"] == "cliente pediu segunda via da fatura"

    with db.connection() as conn:
        notes = conn.execute(
            "SELECT body FROM customer_notes"
        ).fetchall()
    assert [n["body"] for n in notes] == ["cliente pediu segunda via da fatura"]

    # Single use token.
    replay = client.post("/v1/agent/confirm", json={"token": token}, headers=auth_headers)
    assert replay.status_code == 409
    assert replay.json()["error"]["code"] == "confirmation_already_used"


def test_retry_webhook_action_needs_confirmation(client, auth_headers, db, settings):
    import httpx
    from app.services import webhooks

    response = client.post(
        "/v1/payments",
        json=card_payload(),
        headers={**auth_headers, "Idempotency-Key": idem_key()},
    )
    payment = response.json()
    worker.run_once(db, settings)

    def succeeding(request: httpx.Request) -> httpx.Response:
        return httpx.Response(204)

    with httpx.Client(transport=httpx.MockTransport(succeeding)) as http_client:
        assert webhooks.dispatch_once(db, settings, client=http_client) == 1

    reply = client.post(
        "/v1/agent/chat",
        json={"question": f"reprocessar webhook do pagamento {payment['id']}"},
        headers=auth_headers,
    ).json()
    assert reply["confirmation_required"] is True
    assert reply["action"]["type"] == "retry_webhook"
    token = reply["action"]["token"]
    assert "nada foi alterado" in reply["answer"].lower()

    with db.connection() as conn:
        delivery = dict(
            conn.execute(
                "SELECT * FROM webhook_deliveries WHERE payment_id = ?", (payment["id"],)
            ).fetchone()
        )
    assert delivery["status"] == "delivered"  # untouched pre-confirmation

    confirmed = client.post("/v1/agent/confirm", json={"token": token}, headers=auth_headers)
    assert confirmed.status_code in (200, 409)  # no failed delivery → graceful


def test_confirmation_expires_and_never_executes(client, auth_headers, db, settings):
    seed_merchant_and_endpoint(db, settings)
    with db.transaction() as conn:
        from app.services.crm import ensure_customer

        customer = ensure_customer(conn, "expira@exemplo.com")
    pending = register_pending(
        db,
        settings,
        "criar_nota",
        {"email": "expira@exemplo.com", "body": "nota expirada"},
        description="teste",
    )

    with db.transaction() as conn:
        conn.execute(
            "UPDATE agent_pending_actions SET expires_at = '2020-01-01T00:00:00.000000Z' "
            "WHERE id = ?",
            (pending["action_id"],),
        )

    response = client.post(
        "/v1/agent/confirm", json={"token": pending["token"]}, headers=auth_headers
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "confirmation_expired"
    with db.connection() as conn:
        notes = conn.execute("SELECT COUNT(*) FROM customer_notes").fetchone()[0]
    assert notes == 0


# --------------------------------------------------------------------------- #
# RAG (RF-17): BM25, incremental content_hash indexing, thin public surface
# --------------------------------------------------------------------------- #
def test_rag_reindex_incremental_and_search(client, auth_headers, db, settings):
    _pix_paid(client, auth_headers, db, settings)
    with db.transaction() as conn:
        from app.services.crm import ensure_customer

        ensure_customer(conn, "rag@exemplo.com", "Rag Teste")

    first = rag_service.reindex_all(db, settings)
    assert first["indexed"] > 0
    assert first["unchanged"] == 0

    # Nothing changed → content-hash incremental sweep indexes nothing again.
    second = rag_service.reindex_all(db, settings)
    assert second["indexed"] == 0
    assert second["unchanged"] == first["indexed"]

    results = rag_service.search(db, "PIX aprovado", limit=3)
    assert results
    top = results[0]
    assert {"title", "snippet", "citation", "source_type", "source_ref"} <= set(top)
    assert top["source_type"] in {"payment", "event_feed", "customer", "project_doc"}

    api = client.get("/v1/rag/search", params={"q": "pix"}, headers=auth_headers).json()
    assert api["object"] == "list" and api["data"]

    stats = client.post("/v1/rag/reindex", headers=auth_headers).json()
    assert stats["indexed"] == 0
    assert stats["unchanged"] > 0


def test_rag_sources_feed_agent_answers(client, auth_headers, db, settings):
    _pix_paid(client, auth_headers, db, settings)
    rag_service.reindex_all(db, settings)

    reply = client.post(
        "/v1/agent/chat",
        json={"question": "como funcionam os webhooks do gateway?"},
        headers=auth_headers,
    ).json()
    rag_sources = [s for s in reply["sources"] if s["source_type"] != "database"]
    assert rag_sources, "the answer must cite indexed documents (RAG)"


# --------------------------------------------------------------------------- #
# PII redaction (RNF-privacy)
# --------------------------------------------------------------------------- #
def test_agent_logs_are_pii_redacted(tmp_path, client_factory=None):
    from fastapi.testclient import TestClient
    from app.main import create_app
    from app.db import Database

    settings = build_settings(tmp_path, pii_redact_email=True, agent_rate_limit_per_minute=1000)
    db = Database(settings.database_path, settings.db_busy_timeout_ms)
    db.init_schema()
    seed_merchant_and_endpoint(db, settings)
    client = TestClient(create_app(settings))
    with client:
        with db.transaction() as conn:
            from app.services.crm import ensure_customer

            ensure_customer(conn, "sigilo@exemplo.com", "Cliente Sigiloso")
        headers = {"Authorization": f"Bearer {(client.app.state.settings.api_key)}"}

        reply = client.post(
            "/v1/agent/chat",
            json={"question": "informações do cliente sigilo@exemplo.com"},
            headers=headers,
        ).json()
        assert "s***@exemplo.com" in reply["answer"]
        assert "sigilo@" not in reply["answer"]

        with db.connection() as conn:
            rows = conn.execute("SELECT question, answer FROM agent_logs").fetchall()
        assert rows
        for row in rows:
            assert "sigilo@" not in row["question"] and "sigilo@" not in row["answer"]
            assert "s***@exemplo.com" in row["answer"]
