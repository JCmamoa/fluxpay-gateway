package com.paygateway.domain;

import org.springframework.data.annotation.Id;
import org.springframework.data.relational.core.mapping.Table;

import java.math.BigDecimal;
import java.time.Instant;

@Table("transactions")
public class Transaction {

    @Id
    private Long id;
    private String transactionId;
    private String payerId;
    private String payeeId;
    private BigDecimal amount;
    private String currency;
    private String status;
    private String gatewayUsed;
    private Long latencyMs;
    private Instant createdAt;

    public Transaction() {}

    public Transaction(String transactionId, String payerId, String payeeId, BigDecimal amount,
                       String currency, String status, String gatewayUsed, Long latencyMs, Instant createdAt) {
        this.transactionId = transactionId;
        this.payerId = payerId;
        this.payeeId = payeeId;
        this.amount = amount;
        this.currency = currency;
        this.status = status;
        this.gatewayUsed = gatewayUsed;
        this.latencyMs = latencyMs;
        this.createdAt = createdAt;
    }

    public Long getId() { return id; }
    public void setId(Long id) { this.id = id; }
    public String getTransactionId() { return transactionId; }
    public void setTransactionId(String transactionId) { this.transactionId = transactionId; }
    public String getPayerId() { return payerId; }
    public void setPayerId(String payerId) { this.payerId = payerId; }
    public String getPayeeId() { return payeeId; }
    public void setPayeeId(String payeeId) { this.payeeId = payeeId; }
    public BigDecimal getAmount() { return amount; }
    public void setAmount(BigDecimal amount) { this.amount = amount; }
    public String getCurrency() { return currency; }
    public void setCurrency(String currency) { this.currency = currency; }
    public String getStatus() { return status; }
    public void setStatus(String status) { this.status = status; }
    public String getGatewayUsed() { return gatewayUsed; }
    public void setGatewayUsed(String gatewayUsed) { this.gatewayUsed = gatewayUsed; }
    public Long getLatencyMs() { return latencyMs; }
    public void setLatencyMs(Long latencyMs) { this.latencyMs = latencyMs; }
    public Instant getCreatedAt() { return createdAt; }
    public void setCreatedAt(Instant createdAt) { this.createdAt = createdAt; }
}
