package com.paygateway.gateway;

import reactor.core.publisher.Mono;

public interface PaymentGateway {
    Mono<Boolean> processPrimary(String transactionId);
    Mono<Boolean> processFallback(String transactionId);
}
