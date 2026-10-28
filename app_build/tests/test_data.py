"""Data & analytics tests (CA-18 / CA-19): deterministic CSV exports with
integer cents and idempotent daily rollups consistent with ``payments``.
"""

from __future__ import annotations

import csv
import io

from app.services.analytics import PAYMENT_CSV_FIELDS, recompute_daily
from app.services import worker

from helpers import card_payload, idem_key, pix_payload


def _card(client, auth_headers, amount=25990, **customer):
    payload = card_payload(amount=amount)
    if customer:
        payload["customer"] = {"name": customer.get("name", "X"), "email": customer["email"]}
    response = client.post(
        "/v1/payments", json=payload, headers={**auth_headers, "Idempotency-Key": idem_key()}
    )
    return response.json()


def _declined_card(client, auth_headers):
    response = client.post(
        "/v1/payments",
        json=card_payload(number="4000000000000002"),
        headers={**auth_headers, "Idempotency-Key": idem_key()},
    )
    return response.json()


def test_daily_rollup_idempotent_and_consistent(client, auth_headers, db, settings):
    _card(client, auth_headers, 25990, email="ana@exemplo.com")
    _card(client, auth_headers, 5000, email="bruno@exemplo.com")
    _declined_card(client, auth_headers)
    worker.run_once(db, settings)
    worker.run_once(db, settings)  # drain leftover queue messages

    first = recompute_daily(db, settings, window_days=7)
    assert first >= 1  # at least credit_card rows

    second = recompute_daily(db, settings, window_days=7)
    with db.connection() as conn:
        rows = conn.execute(
            "SELECT * FROM analytics_daily ORDER BY day DESC, method"
        ).fetchall()
    assert second == first, "re-running the rollup must upsert, never duplicate"

    truth = {
        ("credit_card", "succeeded"): 2,
        ("credit_card", "failed"): 1,
    }
    counts = {(row["method"], "succeeded"): row["succeeded"] for row in rows}
    counts_failed = {(row["method"], "failed"): row["failed"] for row in rows}
    assert counts[("credit_card", "succeeded")] == truth[("credit_card", "succeeded")]
    assert counts_failed[("credit_card", "failed")] == truth[("credit_card", "failed")]

    # Consistency: the rollup totals equal a fresh aggregation on payments.
    with db.connection() as conn:
        group = conn.execute(
            """
            SELECT strftime('%Y-%m-%d', created_at) AS day, method, COUNT(*) AS attempts,
                   SUM(CASE WHEN status = 'succeeded' THEN 1 ELSE 0 END) AS succeeded,
                   SUM(CASE WHEN status = 'succeeded' THEN amount_cents ELSE 0 END) AS volume_cents
            FROM payments GROUP BY day, method
            """
        ).fetchall()
    by_key = {(row["day"], row["method"]): row for row in group}
    for row in rows:
        source = by_key[(row["day"], row["method"])]
        assert row["attempts"] == source["attempts"]
        assert row["succeeded"] == source["succeeded"]
        assert row["volume_cents"] == source["volume_cents"]

    api = client.get("/v1/analytics/daily", headers=auth_headers).json()
    assert len(api["data"]) == len(rows)


def test_csv_export_payments_header_cents_and_deterministic_order(
    client, auth_headers, db, settings
):
    _card(client, auth_headers, 12345, email="c1@exemplo.com")
    _card(client, auth_headers, 999, email="c2@exemplo.com")
    _declined_card(client, auth_headers)
    worker.run_once(db, settings)

    first = client.get("/v1/data/exports/payments", headers=auth_headers)
    assert first.status_code == 200
    assert first.headers["content-type"].startswith("text/csv")
    assert "attachment" in first.headers["content-disposition"]

    rows = list(csv.reader(io.StringIO(first.text)))
    assert rows[0] == PAYMENT_CSV_FIELDS
    assert rows[0].index("amount_cents") >= 0
    data_rows = rows[1:]
    assert len(data_rows) == 3
    # Money stays in integer cents — never transformed to float.
    assert "12345" in [row[rows[0].index("amount_cents")] for row in data_rows]
    assert "999" in [row[rows[0].index("amount_cents")] for row in data_rows]
    assert all("." not in row[rows[0].index("amount_cents")] for row in data_rows)
    # Deterministic order: (created_at, id) ascending.
    stamps = [(row[rows[0].index("created_at")], row[0]) for row in data_rows]
    assert stamps == sorted(stamps)

    again = client.get("/v1/data/exports/payments", headers=auth_headers).text
    assert again == first.text, "exports must be deterministic"


def test_csv_export_customers(client, auth_headers, db, settings):
    _card(client, auth_headers, 25990, email="exp@exemplo.com")
    worker.run_once(db, settings)

    response = client.get("/v1/data/exports/customers", headers=auth_headers)
    assert response.status_code == 200
    rows = list(csv.reader(io.StringIO(response.text)))
    assert rows[0][0] == "id" and "email" in rows[0] and "total_succeeded_cents" in rows[0]
    data = rows[1:]
    assert len(data) == 1
    assert data[0][rows[0].index("email")] == "exp@exemplo.com"
    # Cents preserved as integer: 25990 cents = R$ 259,90 — placed intact.
    assert data[0][rows[0].index("total_succeeded_cents")] == "25990"


def test_unknown_export_kind_404(client, auth_headers):
    response = client.get("/v1/data/exports/payments_csv_v2", headers=auth_headers)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "export_not_found"


def test_csv_export_neutralizes_formula_injection(client, auth_headers, db, settings):
    """CSV cells that start with = + - @ are escaped with a leading apostrophe
    so a hostile payment description/customer cannot become a spreadsheet
    formula when the export is opened (OWASP CSV injection)."""
    hostile = '=HYPERLINK("http://evil.example","clique")'
    response = client.post(
        "/v1/payments",
        json=card_payload(
            amount=3411,
            description=hostile,
            customer={"name": "@MINHA(2+2)+cmd|calc", "email": "formula@exemplo.com"},
        ),
        headers={**auth_headers, "Idempotency-Key": idem_key()},
    )
    assert response.status_code == 202, response.text

    export = client.get("/v1/data/exports/payments", headers=auth_headers)
    rows = list(csv.reader(io.StringIO(export.text)))
    header = rows[0]
    desc_col = header.index("description")
    name_col = header.index("customer_name")
    amount_col = header.index("amount_cents")
    data_rows = rows[1:]
    assert any(row[desc_col] == f"'{hostile}" for row in data_rows), export.text
    assert any(row[name_col] == "'@MINHA(2+2)+cmd|calc" for row in data_rows), export.text
    # Money stays numeric — never prefixed.
    assert all(row[amount_col] in {"3411"} for row in data_rows if row[amount_col]), export.text
    # Header remains untouched.
    assert header == PAYMENT_CSV_FIELDS


def test_summary_and_timeseries_endpoints(client, auth_headers, db, settings):
    _card(client, auth_headers, 25990, email="dash@exemplo.com")
    worker.run_once(db, settings)

    summary = client.get("/v1/analytics/summary", headers=auth_headers).json()
    assert summary["object"] == "analytics_summary"
    today = summary["today"]
    assert {"payments", "approvals", "approval_rate", "revenue_cents", "method_mix"} <= set(today)
    assert today["payments"] == 1
    assert today["approvals"] == 1
    assert today["revenue_cents"] == 25990
    assert summary["last_7_days"]["payments"] == 1
    assert summary["last_30_days"]["payments"] == 1
    assert "queue" in summary and "webhooks" in summary

    series = client.get(
        "/v1/analytics/timeseries", params={"metric": "volume", "granularity": "day"}
    , headers=auth_headers).json()
    assert series["object"] == "timeseries"
    assert series["points"][-1]["value"] == 25990
    assert series["series"][-1]["succeeded"] == 1
