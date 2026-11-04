CREATE TABLE IF NOT EXISTS transactions (
    id             BIGINT AUTO_INCREMENT PRIMARY KEY,
    transaction_id VARCHAR(64)   NOT NULL,
    payer_id       VARCHAR(64)   NOT NULL,
    payee_id        VARCHAR(64)   NOT NULL,
    amount          DECIMAL(19,2) NOT NULL,
    currency        VARCHAR(3)    NOT NULL,
    status          VARCHAR(20)   NOT NULL,
    gateway_used    VARCHAR(20),
    latency_ms      BIGINT,
    created_at      TIMESTAMP
);
