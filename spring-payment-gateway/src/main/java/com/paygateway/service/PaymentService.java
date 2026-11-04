package com.paygateway.service;

import com.paygateway.domain.Transaction;
import com.paygateway.dto.PaymentRequest;
import com.paygateway.dto.PaymentResponse;
import com.paygateway.gateway.PaymentGateway;
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

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.Duration;
import java.time.Instant;
import java.util.HexFormat;
import java.util.UUID;

@Service
public class PaymentService {

    private static final Logger log = LoggerFactory.getLogger(PaymentService.class);
    private static final Duration PRIMARY_BUDGET = Duration.ofSeconds(4);

    private final PaymentGateway gatewayClient;
    private final TransactionRepository transactionRepository;
    private final PaymentMetricsRecorder metrics;
    private final CircuitBreaker circuitBreaker;
    private final Retry retry;
    private final Bulkhead bulkhead;
    private final TimeLimiter timeLimiter;

    public PaymentService(PaymentGateway gatewayClient,
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

    public Mono<PaymentResponse> processPayment(
            PaymentRequest request, String username, String requestedKey) {
        String idempotencyKey = sha256(username + "\n" + requestedKey);
        String requestHash = requestHash(request);
        return transactionRepository.findByIdempotencyKey(idempotencyKey)
                .flatMap(existing -> existingResponse(existing, requestHash))
                .switchIfEmpty(Mono.defer(() -> reserveAndProcess(request, idempotencyKey, requestHash)));
    }

    private Mono<PaymentResponse> reserveAndProcess(
            PaymentRequest request, String idempotencyKey, String requestHash) {
        String transactionId = "TXN-" + UUID.randomUUID();
        Instant start = Instant.now();
        Transaction transaction = new Transaction(
                transactionId, idempotencyKey, requestHash, request.payerId(), request.payeeId(),
                request.amount(), request.currency(), "PENDING", "NONE", 0L, start);

        return transactionRepository.save(transaction)
                .flatMap(saved -> callGateways(transactionId)
                        .materialize()
                        .flatMap(outcome -> outcome.hasValue()
                                ? completeApproved(saved, outcome.get(), start)
                                : finalizeFailed(saved, start, outcome.getThrowable())))
                .onErrorResume(error -> transactionRepository.findByIdempotencyKey(idempotencyKey)
                        .flatMap(existing -> existingResponse(existing, requestHash))
                        .switchIfEmpty(Mono.error(error)));
    }

    private Mono<String> callGateways(String transactionId) {
        return callPrimaryGateway(transactionId)
                .timeout(PRIMARY_BUDGET)
                .onErrorResume(primaryError -> gatewayClient.processFallback(transactionId)
                        .flatMap(approved -> approved
                                ? Mono.just("FALLBACK")
                                : Mono.error(new IllegalStateException(
                                        "Gateway de fallback recusou a transação"))));
    }

    private Mono<PaymentResponse> completeApproved(
            Transaction transaction, String gatewayUsed, Instant start) {
        long latency = Duration.between(start, Instant.now()).toMillis();
        transaction.setStatus("APPROVED");
        transaction.setGatewayUsed(gatewayUsed);
        transaction.setLatencyMs(latency);

        return transactionRepository.save(transaction)
                .map(saved -> {
                    metrics.recordApproved(latency);
                    if ("FALLBACK".equals(gatewayUsed)) {
                        metrics.recordFallbackUsed();
                    }
                    return toResponse(saved, "Transação processada com sucesso");
                });
    }

    private Mono<PaymentResponse> finalizeFailed(Transaction transaction, Instant start, Throwable error) {
        log.error("Failed to process transaction {}", transaction.getTransactionId(), error);
        long latency = Duration.between(start, Instant.now()).toMillis();
        transaction.setStatus("FAILED");
        transaction.setGatewayUsed("NONE");
        transaction.setLatencyMs(latency);
        return transactionRepository.save(transaction)
                .doOnSuccess(saved -> metrics.recordFailed())
                .map(saved -> toResponse(saved, "Falha ao processar a transação"));
    }

    private Mono<PaymentResponse> existingResponse(Transaction transaction, String requestHash) {
        if (!transaction.getRequestHash().equals(requestHash)) {
            return Mono.error(new IdempotencyConflictException(
                    "Idempotency-Key já foi usado com dados diferentes"));
        }
        if ("PENDING".equals(transaction.getStatus())) {
            return Mono.error(new PaymentInProgressException(
                    "Já existe um pagamento em processamento para essa chave"));
        }
        return Mono.just(toResponse(transaction,
                "APPROVED".equals(transaction.getStatus())
                        ? "Transação processada com sucesso" : "Falha ao processar a transação"));
    }

    private PaymentResponse toResponse(Transaction transaction, String message) {
        return new PaymentResponse(
                transaction.getTransactionId(), transaction.getStatus(),
                transaction.getGatewayUsed(), transaction.getLatencyMs(),
                transaction.getCreatedAt(), message);
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

    private String requestHash(PaymentRequest request) {
        String canonical = String.join("\n", request.payerId(), request.payeeId(),
                request.amount().stripTrailingZeros().toPlainString(), request.currency());
        return sha256(canonical);
    }

    private String sha256(String value) {
        try {
            byte[] digest = MessageDigest.getInstance("SHA-256")
                    .digest(value.getBytes(StandardCharsets.UTF_8));
            return HexFormat.of().formatHex(digest);
        } catch (NoSuchAlgorithmException exception) {
            throw new IllegalStateException("SHA-256 is unavailable", exception);
        }
    }
}
