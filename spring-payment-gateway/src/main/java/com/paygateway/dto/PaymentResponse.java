package com.paygateway.dto;

import java.time.Instant;

public record PaymentResponse(
        String transactionId,
        String status,
        String gatewayUsed,
        Long latencyMs,
        Instant processedAt,
        String message
) {}
