CREATE CATALOG IF NOT EXISTS payment_analytics;

CREATE SCHEMA IF NOT EXISTS payment_analytics.bronze;
CREATE SCHEMA IF NOT EXISTS payment_analytics.silver;
CREATE SCHEMA IF NOT EXISTS payment_analytics.gold;

CREATE VOLUME IF NOT EXISTS payment_analytics.gold.executive_reports;
