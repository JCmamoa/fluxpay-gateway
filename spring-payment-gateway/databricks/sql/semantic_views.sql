CREATE OR REPLACE VIEW payment_analytics.gold.vw_executive_daily AS
SELECT
    payment_date,
    currency,
    total_transactions,
    resolved_transactions,
    approved_transactions,
    failed_transactions,
    pending_transactions,
    approval_rate_percent,
    approved_amount,
    average_latency_ms,
    report_generated_at
FROM payment_analytics.gold.executive_daily;

CREATE OR REPLACE VIEW payment_analytics.gold.vw_settlement_exceptions AS
SELECT
    transaction_id,
    payment_date,
    currency,
    payment_amount,
    settled_amount,
    settlement_difference,
    settlement_status,
    settlement_record_count,
    settlement_currencies,
    last_settled_at
FROM payment_analytics.gold.settlement_reconciliation
WHERE settlement_status <> 'RECONCILED';
