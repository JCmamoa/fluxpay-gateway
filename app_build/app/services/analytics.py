"""Analytics layer (RF-18): dashboard snapshot, series, daily rollups, exports.

``analytics_daily`` is an idempotent per-(day, method) upsert recomputed from
``payments`` (CA-19): the rollup job reprocesses a sliding window and any
consistent re-run yields identical rows.
"""

from __future__ import annotations

import csv
import io
import logging
from datetime import date, timedelta
from typing import Any, Iterator

from ..config import Settings
from ..errors import ApiError
from ..utils import iso_now, parse_iso, utc_now

logger = logging.getLogger("app.analytics")

DAY_FORMAT = "%Y-%m-%d"
METHODS = ("pix", "credit_card", "debit_card")
TIMESERIES_METRICS = ("volume", "approvals", "attempts", "failures", "approval_rate")
GRANULARITIES = ("hour", "day")


# --------------------------------------------------------------------------- #
# Dashboard summary (RF-13)
# --------------------------------------------------------------------------- #
def summary(db: Any) -> dict[str, Any]:
    now_day = utc_now().strftime(DAY_FORMAT)
    week_start = (utc_now() - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    month_start = (utc_now() - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    today_start = f"{now_day}T00:00:00.000000Z"
    with db.connection() as conn:
        today = conn.execute(
            """
            SELECT
              COUNT(*) AS payments,
              COALESCE(SUM(CASE WHEN status = 'succeeded' THEN 1 ELSE 0 END), 0) AS succeeded,
              COALESCE(SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END), 0) AS failed,
              COALESCE(SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END), 0) AS pending,
              COALESCE(SUM(CASE WHEN status = 'succeeded' THEN amount_cents ELSE 0 END), 0)
                AS revenue_cents
            FROM payments WHERE created_at >= ?
            """,
            (today_start,),
        ).fetchone()
        window = conn.execute(
            """
            SELECT
              COUNT(*) AS payments,
              COALESCE(SUM(CASE WHEN status = 'succeeded' THEN amount_cents ELSE 0 END), 0)
                AS revenue_cents
            FROM payments WHERE created_at >= ?
            """,
            (week_start,),
        ).fetchone()
        window30 = conn.execute(
            """
            SELECT
              COUNT(*) AS payments,
              COALESCE(SUM(CASE WHEN status = 'succeeded' THEN amount_cents ELSE 0 END), 0)
                AS revenue_cents
            FROM payments WHERE created_at >= ?
            """,
            (month_start,),
        ).fetchone()
        mix = conn.execute(
            """
            SELECT method,
              COUNT(*) AS total,
              COALESCE(SUM(CASE WHEN status = 'succeeded' THEN amount_cents ELSE 0 END), 0)
                AS revenue_cents
            FROM payments WHERE created_at >= ?
            GROUP BY method
            """,
            (today_start,),
        ).fetchall()
        queue_counts = {
            row["status"]: row["total"]
            for row in conn.execute(
                "SELECT status, COUNT(*) AS total FROM queue_messages GROUP BY status"
            ).fetchall()
        }
        deliveries = {
            row["status"]: row["total"]
            for row in conn.execute(
                "SELECT status, COUNT(*) AS total FROM webhook_deliveries GROUP BY status"
            ).fetchall()
        }
        failure_reasons = [
            {"reason": row[0] or "unknown", "count": row[1]}
            for row in conn.execute(
                """
                SELECT failure_reason, COUNT(*) FROM payments
                WHERE status = 'failed' AND created_at >= ?
                GROUP BY failure_reason ORDER BY COUNT(*) DESC, failure_reason LIMIT 5
                """,
                (week_start,),
            ).fetchall()
        ]
        top_customers = [
            {
                "email": row[0],
                "succeeded_count": row[1],
                "total_cents": row[2],
            }
            for row in conn.execute(
                """
                SELECT LOWER(customer_email) AS email,
                  COUNT(*) AS succeeded_count,
                  SUM(amount_cents) AS total_cents
                FROM payments
                WHERE status = 'succeeded' AND customer_email IS NOT NULL AND created_at >= ?
                GROUP BY LOWER(customer_email)
                ORDER BY total_cents DESC, email
                LIMIT 5
                """,
                (month_start,),
            ).fetchall()
        ]

    attempts_today = int(today["payments"])
    succeeded_today = int(today["succeeded"])
    queue_base = {"pending": 0, "processing": 0, "done": 0, "failed": 0}
    queue_base.update(queue_counts)
    delivery_base = {"pending": 0, "delivered": 0, "failed": 0}
    delivery_base.update(deliveries)

    mix_map = {row["method"]: row for row in mix}
    methods_mix = {}
    for method in METHODS:
        row = mix_map.get(method)
        methods_mix[method] = {
            "count": row["total"] if row else 0,
            "revenue_cents": row["revenue_cents"] if row else 0,
        }

    return {
        "object": "analytics_summary",
        "generated_at": iso_now(),
        "today": {
            "payments": attempts_today,
            "approvals": succeeded_today,
            "failures": int(today["failed"]),
            "pending": int(today["pending"]),
            "approval_rate": _rate(succeeded_today, attempts_today),
            "revenue_cents": int(today["revenue_cents"]),
            "method_mix": methods_mix,
        },
        "last_7_days": {
            "payments": int(window["payments"]),
            "revenue_cents": int(window["revenue_cents"]),
        },
        "last_30_days": {
            "payments": int(window30["payments"]),
            "revenue_cents": int(window30["revenue_cents"]),
        },
        "queue": queue_base,
        "webhooks": {
            **delivery_base,
            "dlq": delivery_base["failed"],
        },
        "top_failure_reasons": failure_reasons,
        "top_customers": top_customers,
    }


def _rate(succeeded: int, attempts: int) -> float:
    if not attempts:
        return 0.0
    return round(succeeded / attempts, 4)


# --------------------------------------------------------------------------- #
# Timeseries (payments grouped by hour/day)
# --------------------------------------------------------------------------- #
def timeseries(
    db: Any,
    *,
    metric: str,
    granularity: str,
    from_day: str | None = None,
    to_day: str | None = None,
) -> dict[str, Any]:
    if metric not in TIMESERIES_METRICS:
        raise ApiError(422, "invalid_metric", f"metric must be one of {list(TIMESERIES_METRICS)}")
    if granularity not in GRANULARITIES:
        raise ApiError(422, "invalid_granularity", f"granularity must be one of {list(GRANULARITIES)}")
    if granularity == "day":
        default_from = utc_now() - timedelta(days=30)
        fmt_expr = "%Y-%m-%d"
    else:
        default_from = utc_now() - timedelta(hours=48)
        fmt_expr = "%Y-%m-%dT%H"

    if from_day:
        try:
            start = parse_iso(
                from_day if "T" in from_day else f"{from_day}T00:00:00.000000Z"
            )
        except ValueError as exc:
            raise ApiError(422, "invalid_from", "from must be an ISO date (YYYY-MM-DD)") from exc
    else:
        start = default_from
    if to_day:
        try:
            end = parse_iso(to_day if "T" in to_day else f"{to_day}T23:59:59.999999Z")
        except ValueError as exc:
            raise ApiError(422, "invalid_to", "to must be an ISO date (YYYY-MM-DD)") from exc
    else:
        end = utc_now()

    with db.connection() as conn:
        rows = conn.execute(
            """
            SELECT strftime(?, created_at) AS bucket,
              COUNT(*) AS attempts,
              COALESCE(SUM(CASE WHEN status = 'succeeded' THEN 1 ELSE 0 END), 0) AS succeeded,
              COALESCE(SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END), 0) AS failed,
              COALESCE(SUM(CASE WHEN status = 'succeeded' THEN amount_cents ELSE 0 END), 0)
                AS volume_cents
            FROM payments
            WHERE created_at >= ? AND created_at <= ?
            GROUP BY bucket ORDER BY bucket
            """,
            (fmt_expr, _iso(start), _iso(end)),
        ).fetchall()

    series = [
        {
            "bucket": row["bucket"],
            "attempts": int(row["attempts"]),
            "succeeded": int(row["succeeded"]),
            "approvals": int(row["succeeded"]),
            "failed": int(row["failed"]),
            "failures": int(row["failed"]),
            "volume": int(row["volume_cents"]),
            "volume_cents": int(row["volume_cents"]),
            "approval_rate": _rate(row["succeeded"], row["attempts"]),
        }
        for row in rows
    ]
    return {
        "object": "timeseries",
        "metric": metric,
        "granularity": granularity,
        "from": _iso(start).split("T")[0],
        "to": utc_now().strftime(DAY_FORMAT),
        "points": [{"bucket": point["bucket"], "value": point[metric]} for point in series],
        "series": series,
    }


def _iso(moment: Any) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%S.%fZ")  # type: ignore[union-attr]


# --------------------------------------------------------------------------- #
# Daily rollups (idempotent upsert)
# --------------------------------------------------------------------------- #
def recompute_daily(
    db: Any, settings: Settings, *, window_days: int | None = None
) -> int:
    days = window_days if window_days is not None else settings.analytics_rollup_days
    cutoff = (utc_now() - timedelta(days=days)).strftime(DAY_FORMAT)
    now = iso_now()
    with db.transaction() as conn:
        rows = conn.execute(
            """
            SELECT day, method, attempts, succeeded, failed, volume_cents FROM (
              SELECT strftime('%Y-%m-%d', created_at) AS day, method,
                COUNT(*) AS attempts,
                COALESCE(SUM(CASE WHEN status = 'succeeded' THEN 1 ELSE 0 END), 0) AS succeeded,
                COALESCE(SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END), 0) AS failed,
                COALESCE(SUM(CASE WHEN status = 'succeeded' THEN amount_cents ELSE 0 END), 0)
                  AS volume_cents
              FROM payments
              WHERE strftime('%Y-%m-%d', created_at) >= ?
              GROUP BY day, method
            )
            """,
            (cutoff,),
        ).fetchall()
        upserted = 0
        for row in rows:
            rate = _rate(row["succeeded"], row["attempts"])
            conn.execute(
                """
                INSERT INTO analytics_daily(
                    day, method, attempts, succeeded, failed, volume_cents,
                    approval_rate, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(day, method) DO UPDATE SET
                    attempts = excluded.attempts,
                    succeeded = excluded.succeeded,
                    failed = excluded.failed,
                    volume_cents = excluded.volume_cents,
                    approval_rate = excluded.approval_rate,
                    updated_at = excluded.updated_at
                """,
                (
                    row["day"],
                    row["method"],
                    row["attempts"],
                    row["succeeded"],
                    row["failed"],
                    row["volume_cents"],
                    rate,
                    now,
                ),
            )
            upserted += 1
    logger.info("analytics rollup recomputed", extra={"rows": upserted, "cutoff": cutoff})
    return upserted


def daily(db: Any, *, from_day: str | None = None, to_day: str | None = None) -> dict[str, Any]:
    clauses, params = [], []
    if from_day:
        _validate_day(from_day, "from")
        clauses.append("day >= ?")
        params.append(from_day)
    if to_day:
        _validate_day(to_day, "to")
        clauses.append("day <= ?")
        params.append(to_day)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with db.connection() as conn:
        rows = conn.execute(
            f"SELECT * FROM analytics_daily {where} ORDER BY day DESC, method",
            params,
        ).fetchall()
    return {
        "object": "list",
        "data": [
            {
                "day": row["day"],
                "method": row["method"],
                "attempts": row["attempts"],
                "succeeded": row["succeeded"],
                "failed": row["failed"],
                "volume_cents": row["volume_cents"],
                "approval_rate": row["approval_rate"],
            }
            for row in rows
        ],
        "has_more": False,
    }


def _validate_day(value: str, name: str) -> None:
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise ApiError(422, f"invalid_{name}", f"{name} must be an ISO date (YYYY-MM-DD)") from exc


# --------------------------------------------------------------------------- #
# CSV exports (streaming; deterministic)
# --------------------------------------------------------------------------- #
PAYMENT_CSV_FIELDS = [
    "id",
    "created_at",
    "updated_at",
    "status",
    "method",
    "amount_cents",
    "currency",
    "description",
    "customer_name",
    "customer_email",
    "authorization_code",
    "failure_reason",
]
CUSTOMER_CSV_FIELDS = [
    "id",
    "email",
    "name",
    "lifecycle_status",
    "first_seen_at",
    "last_seen_at",
    "payments_count",
    "succeeded_count",
    "total_succeeded_cents",
    "tags",
    "created_at",
]


def _csv_cell(value: Any) -> Any:
    """Neutralize spreadsheet formula injection (OWASP CSVA).

    Text starting with ``=``, ``+``, ``-``, ``@``, TAB or CR could be interpreted
    as a formula/command by Excel/LibreOffice/Sheets when opened. A leading
    apostrophe forces the literal-text treatment; integers (money in cents)
    and the static header pass through untouched.
    """
    if isinstance(value, str) and value.startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + value
    return value


def _write_row(values: list[Any]) -> str:
    buffer = io.StringIO()
    csv.writer(buffer, lineterminator="\n", quoting=csv.QUOTE_MINIMAL).writerow(
        [_csv_cell(value) for value in values]
    )
    return buffer.getvalue()


def iter_export(db: Any, kind: str, *, batch_size: int = 500) -> Iterator[str]:
    """Streaming CSV generator; money stays in integer cents (CA-18)."""
    if kind not in ("payments", "customers"):
        raise ApiError(404, "export_not_found", "Export kind must be payments or customers")
    fields = PAYMENT_CSV_FIELDS if kind == "payments" else CUSTOMER_CSV_FIELDS
    yield _write_row(fields)

    if kind == "payments":
        last_created, last_id = "", ""
        while True:
            with db.connection() as conn:
                rows = conn.execute(
                    "SELECT id, created_at, updated_at, status, method, amount_cents, currency,"
                    " description, customer_name, customer_email, authorization_code,"
                    " failure_reason FROM payments"
                    " WHERE (created_at > ? OR (created_at = ? AND id > ?))"
                    " ORDER BY created_at, id LIMIT ?",
                    (last_created, last_created, last_id, batch_size),
                ).fetchall()
            if not rows:
                return
            for row in rows:
                last_created, last_id = row["created_at"], row["id"]
                yield _write_row([row[field] for field in fields])
    else:
        with db.connection() as conn:
            rows = conn.execute(
                """
                SELECT c.id, c.email, c.name, c.lifecycle_status, c.first_seen_at,
                       c.last_seen_at, c.created_at,
                       (SELECT COUNT(*) FROM payments p
                         WHERE LOWER(p.customer_email) = LOWER(c.email)) AS payments_count,
                       (SELECT COUNT(*) FROM payments p
                         WHERE LOWER(p.customer_email) = LOWER(c.email)
                           AND p.status = 'succeeded') AS succeeded_count,
                       (SELECT COALESCE(SUM(p.amount_cents), 0) FROM payments p
                         WHERE LOWER(p.customer_email) = LOWER(c.email)
                           AND p.status = 'succeeded') AS total_succeeded_cents,
                       (SELECT GROUP_CONCAT(t.tag, '|') FROM customer_tags t
                         WHERE t.customer_id = c.id) AS tags
                FROM customers c ORDER BY c.created_at, c.id
                """
            ).fetchall()
        for row in rows:
            yield _write_row(
                [
                    row["id"],
                    row["email"],
                    row["name"],
                    row["lifecycle_status"],
                    row["first_seen_at"],
                    row["last_seen_at"],
                    row["payments_count"],
                    row["succeeded_count"],
                    row["total_succeeded_cents"],
                    row["tags"] or "",
                    row["created_at"],
                ]
            )


def export_filename(kind: str) -> str:
    stamp = utc_now().strftime("%Y%m%d")
    return f"fluxpay-{kind}-{stamp}.csv"
