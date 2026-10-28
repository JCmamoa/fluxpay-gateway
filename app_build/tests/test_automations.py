"""Automation engine tests (CA-14 / RF-15): seeded rule, full pipeline
webhook failure → hub event → durable queue → run audit + live alert, the
three action types, idempotent runs and rule CRUD.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from app.db import Database
from app.main import create_app
from app.services import queue as queue_service
from app.services import webhooks, worker
from app.services.seed import seed_merchant_and_endpoint

from conftest import API_KEY, build_settings
from helpers import card_payload, idem_key


@pytest.fixture()
def fan_db(tmp_path):
    """Full outbox pipeline: hub events DO enqueue their consumers here."""
    settings = build_settings(tmp_path, event_fanout=True)
    database = Database(settings.database_path, settings.db_busy_timeout_ms)
    database.init_schema()
    seed_merchant_and_endpoint(database, settings)
    return database


@pytest.fixture()
def fan_settings(tmp_path):
    return build_settings(tmp_path, event_fanout=True)


@pytest.fixture()
def fan_client(fan_db, fan_settings):
    client = TestClient(create_app(fan_settings))
    with client:
        client.state_db = fan_db
        yield client


@pytest.fixture()
def fan_headers():
    return {"Authorization": f"Bearer {API_KEY}"}


def _drain(db, settings):
    """Keep processing until the queue is empty (bounded iterations)."""
    for _ in range(12):
        outcome = worker.run_once(db, settings)
        if outcome["claimed"] == 0:
            break


def _succeeded_card(client, db, settings):
    response = client.post(
        "/v1/payments",
        json=card_payload(),
        headers={"Authorization": f"Bearer {API_KEY}", "Idempotency-Key": idem_key()},
    )
    assert response.status_code == 202, response.text
    payment = response.json()
    worker.run_once(db, settings)  # process the payment
    _drain(db, settings)
    return payment


def _fail_to_dlq(db, settings, payment):
    def failing(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    with httpx.Client(transport=httpx.MockTransport(failing)) as http_client:
        for _ in range(3):
            webhooks.dispatch_once(db, settings, client=http_client)
    _drain(db, settings)


def test_seed_rule_installed(fan_client, fan_headers):
    rules = fan_client.get("/v1/automations/rules", headers=fan_headers).json()["data"]
    ids = [rule["id"] for rule in rules]
    assert "ar_seed_webhook_alert" in ids
    seed = next(rule for rule in rules if rule["id"] == "ar_seed_webhook_alert")
    assert seed["event_type"] == "webhook.failed"
    assert seed["action_type"] == "alerta_no_console"
    assert seed["enabled"] is True
    assert "payment_id" in seed["action_config"]["message"]


def test_webhook_dlq_fires_seed_rule_with_audit_and_live_alert(
    fan_db, fan_settings, fan_client, fan_headers
):
    """CA-14: DLQ → webhook.failed → automation.evaluate → run recorded and
    alert broadcast to the console."""
    payment = _succeeded_card(fan_client, fan_db, fan_settings)
    _fail_to_dlq(fan_db, fan_settings, payment)

    rules = fan_client.get("/v1/automations/rules", headers=fan_headers).json()["data"]
    runs = fan_client.get(
        "/v1/automations/runs", params={"rule_id": "ar_seed_webhook_alert"}, headers=fan_headers
    ).json()["data"]
    assert runs, "seed rule must have audited at least one run"
    assert all(run["status"] == "executed" for run in runs)

    with fan_db.connection() as conn:
        alert_events = conn.execute(
            "SELECT payload FROM event_stream WHERE type = 'automation.alert' ORDER BY rowid DESC"
        ).fetchall()
    assert alert_events
    payload = alert_events[0][0]
    assert payment["id"] in payload
    assert "Webhook falhou" in payload

    # The alert is visible through the REST hub snapshot (console source of truth)
    hub = fan_client.get(
        "/v1/events", params={"type": "automation.alert,automation.run"}, headers=fan_headers
    ).json()
    types = {event["type"] for event in hub["data"]}
    assert {"automation.alert", "automation.run"} <= types


def _create_rule(client, headers, **overrides):
    body = {
        "name": "pagamento aprovado -> tag vip",
        "event_type": "payment.succeeded",
        "conditions": {},
        "action_type": "tag_customer",
        "action_config": {"tag": "vip"},
    }
    body.update(overrides)
    response = client.post("/v1/automations/rules", json=body, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def test_tag_customer_action_executed_via_pipeline(
    fan_db, fan_settings, fan_client, fan_headers
):
    rule = _create_rule(fan_client, fan_headers)
    _succeeded_card(fan_client, fan_db, fan_settings)

    customers = fan_client.get("/v1/customers", params={"tag": "vip"}, headers=fan_headers).json()
    assert customers["total"] == 1
    assert customers["data"][0]["email"] == "bruno@exemplo.com"

    runs = fan_client.get("/v1/automations/runs", params={"rule_id": rule["id"]}, headers=fan_headers).json()["data"]
    assert runs and runs[0]["status"] == "executed"
    assert runs[0]["detail"]["status"] == "executed"


def test_conditions_gate_skips_and_records_skip(fan_db, fan_settings, fan_client, fan_headers):
    rule = _create_rule(
        fan_client,
        fan_headers,
        name="somente cartoes pequenos",
        conditions={"amount_cents_lte": 1000},
    )
    _succeeded_card(fan_client, fan_db, fan_settings)  # 25990 cents → skipped

    runs = fan_client.get("/v1/automations/runs", params={"rule_id": rule["id"]}, headers=fan_headers).json()["data"]
    assert runs
    assert all(run["status"] == "skipped" for run in runs)


def test_retry_webhook_action_requeues_dlq(fan_db, fan_settings, fan_client, fan_headers):
    _create_rule(
        fan_client,
        fan_headers,
        name="dlq -> reprocessar webhook",
        event_type="webhook.failed",
        action_type="retry_webhook",
        action_config={},
    )
    payment = _succeeded_card(fan_client, fan_db, fan_settings)
    _fail_to_dlq(fan_db, fan_settings, payment)

    with fan_db.connection() as conn:
        delivery = dict(
            conn.execute(
                "SELECT * FROM webhook_deliveries WHERE payment_id = ? AND status = 'pending'",
                (payment["id"],),
            ).fetchone()
        )

    # Re-run the failing endpoint again to move the requeued delivery to the
    # DLQ again, and pick the requeue trail from the event hub.
    with fan_db.connection() as conn:
        requeued = conn.execute(
            "SELECT payload FROM event_stream WHERE type = 'webhook.requeued' ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
    assert requeued is not None
    assert delivery is not None
    store = fan_client.get(
        "/v1/events", params={"type": "webhook.requeued"}, headers=fan_headers
    ).json()["data"]
    assert any("automation" in event["data"].get("via", "") for event in store)


def test_runs_are_idempotent_per_rule_and_event(fan_db, fan_settings, fan_client, fan_headers):
    from app.services import automations as automations_service
    from app.services import events as events_hub

    _create_rule(fan_client, fan_headers)

    with fan_db.transaction() as conn:
        eid = events_hub.record_event(
            conn,
            "payment.succeeded",
            {
                "payment_id": "pay_dbg_manual_evt",
                "status": "succeeded",
                "method": "credit_card",
                "amount_cents": 1000,
                "customer_email": "bruno@exemplo.com",
            },
        )

    first = automations_service.evaluate_event(fan_db, fan_settings, eid)
    assert first == "1/1"
    second = automations_service.evaluate_event(fan_db, fan_settings, eid)
    assert second == "0/0", "the second evaluation must be a no-op (idempotent)"

    with fan_db.connection() as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM automation_runs WHERE event_id = ?", (eid,)
        ).fetchone()[0]
    assert count == 1


def test_rule_crud_and_validation(fan_client, fan_headers):
    rule = _create_rule(fan_client, fan_headers, name="regra original")

    patched = fan_client.patch(
        f"/v1/automations/rules/{rule['id']}", json={"enabled": False}, headers=fan_headers
    ).json()
    assert patched["enabled"] is False

    detail = fan_client.get(f"/v1/automations/rules/{rule['id']}", headers=fan_headers).json()
    assert detail["enabled"] is False

    deleted = fan_client.delete(f"/v1/automations/rules/{rule['id']}", headers=fan_headers)
    assert deleted.status_code == 200
    assert fan_client.get(f"/v1/automations/rules/{rule['id']}", headers=fan_headers).status_code == 404

    invalid = fan_client.post(
        "/v1/automations/rules",
        json={
            "name": "acao invalida",
            "event_type": "payment.succeeded",
            "action_type": "apagar_banco",
            "action_config": {},
        },
        headers=fan_headers,
    )
    assert invalid.status_code == 422

    bad_event = fan_client.post(
        "/v1/automations/rules",
        json={
            "name": "evento invalido",
            "event_type": "banco.drop",
            "action_type": "alerta_no_console",
            "action_config": {},
        },
        headers=fan_headers,
    )
    assert bad_event.status_code == 422


def test_unsupported_event_evaluation_is_noop(fan_db, fan_settings):
    result = automations_evaluate(fan_db, fan_settings, "evt_missing_0001")
    assert result == "not_found"


def automations_evaluate(db, settings, event_id):
    from app.services.automations import evaluate_event

    return evaluate_event(db, settings, event_id)
