package com.paygateway.service;

import com.paygateway.domain.Transaction;
import com.paygateway.dto.PaymentRequest;
import com.paygateway.dto.PaymentResponse;
import com.paygateway.gateway.ExternalGatewayClient;
import com.paygateway.metrics.PaymentMetricsRecorder;
import com.paygateway.repository.TransactionRepository;
import io.github.resilience4j.bulkhead.Bulkhead;
import io.github.resilience4j.bulkhead.BulkheadRegistry;
import io.github.resilience4j.circuitbreaker.CircuitBreaker;
import io.github.resilience4j.circuitbreaker.CircuitBreakerRegistry;
import io.github.resilience4j.reactor.bulkhead.operator.BulkheadOperator;
import io.github.resilience4j.reactor.circuitbreaker.operator.CircuitBreakerOperator;
import io.github.resilience4j.reactor.retry.RetryOperator;
import io.github.resilience4j.reactor.timelimiter.TimeLimiterOperator;
import io.github.resilience4j.retry.Retry;
import io.github.resilience4j.retry.RetryRegistry;
import io.github.resilience4j.timelimiter.TimeLimiter;
import io.github.resilience4j.timelimiter.TimeLimiterRegistry;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import reactor.core.publisher.Mono;

import java.time.Duration;
import java.time.Instant;
import java.util.UUID;

@Service
public class PaymentService {

    private static final Logger log = LoggerFactory.getLogger(PaymentService.class);
    private static final Duration PRIMARY_BUDGET = Duration.ofSeconds(4);

    private final ExternalGatewayClient gatewayClient;
    private final TransactionRepository transactionRepository;
    private final PaymentMetricsRecorder metrics;
    private final CircuitBreaker circuitBreaker;
    private final Retry retry;
    private final Bulkhead bulkhead;
    private final TimeLimiter timeLimiter;

    public PaymentService(ExternalGatewayClient gatewayClient,
                          TransactionRepository transactionRepository,
                          PaymentMetricsRecorder metrics,
                          CircuitBreakerRegistry cbRegistry,
                          RetryRegistry retryRegistry,
                          BulkheadRegistry bulkheadRegistry,
                          TimeLimiterRegistry timeLimiterRegistry) {
        this.gatewayClient = gatewayClient;
        this.transactionRepository = transactionRepository;
        this.metrics = metrics;
        this.circuitBreaker = cbRegistry.circuitBreaker("externalGateway");
        this.retry = retryRegistry.retry("externalGateway");
        this.bulkhead = bulkheadRegistry.bulkhead("externalGateway");
        this.timeLimiter = timeLimiterRegistry.timeLimiter("externalGateway");
    }

    public Mono<PaymentResponse> processPayment(PaymentRequest request) {
        String transactionId = "TXN-" + UUID.randomUUID();
        Instant start = Instant.now();

        return callPrimaryGateway(transactionId)
                .timeout(PRIMARY_BUDGET)
                .onErrorResume(error -> fallbackGateway(transactionId))
                .flatMap(gatewayUsed -> {
                    long latency = Duration.between(start, Instant.now()).toMillis();
                    Transaction tx = new Transaction(
                            transactionId, request.payerId(), request.payeeId(),
                            request.amount(), request.currency(), "APPROVED",
                            gatewayUsed, latency, Instant.now());

                    return transactionRepository.save(tx)
                            .map(saved -> {
                                metrics.recordApproved(latency);
                                if ("FALLBACK".equals(gatewayUsed)) {
                                    metrics.recordFallbackUsed();
                                }
                                return new PaymentResponse(
                                        transactionId, "APPROVED", gatewayUsed, latency,
                                        Instant.now(), "Transação processada com sucesso");
                            });
                })
                .onErrorResume(error -> registrarFalha(request, transactionId, start, error));
    }

    private Mono<String> callPrimaryGateway(String transactionId) {
        return gatewayClient.processPrimary(transactionId)
                .flatMap(approved -> approved
                        ? Mono.just("PRIMARY")
                        : Mono.error(new IllegalStateException("Gateway primário recusou a transação")))
                .transformDeferred(TimeLimiterOperator.of(timeLimiter))
                .transformDeferred(RetryOperator.of(retry))
                .transformDeferred(CircuitBreakerOperator.of(circuitBreaker))
                .transformDeferred(BulkheadOperator.of(bulkhead));
    }

    private Mono<String> fallbackGateway(String transactionId) {
        return gatewayClient.processFallback(transactionId)
                .flatMap(approved -> approved
                        ? Mono.just("FALLBACK")
                        : Mono.error(new IllegalStateException("Gateway de fallback recusou a transação")));
    }

    private Mono<PaymentResponse> registrarFalha(PaymentRequest request, String transactionId,
                                                  Instant start, Throwable error) {
        log.error("Failed to process transaction {}", transactionId, error);
        metrics.recordFailed();
        long latency = Duration.between(start, Instant.now()).toMillis();

        Transaction tx = new Transaction(
                transactionId, request.payerId(), request.payeeId(),
                request.amount(), request.currency(), "FAILED",
                "NONE", latency, Instant.now());

        PaymentResponse response = new PaymentResponse(
                transactionId, "FAILED", "NONE", latency, Instant.now(),
                "Falha ao processar a transação");

        return transactionRepository.save(tx).thenReturn(response);
    }
}
