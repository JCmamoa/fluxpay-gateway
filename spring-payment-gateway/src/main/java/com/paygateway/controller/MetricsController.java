package com.paygateway.controller;

import com.paygateway.repository.TransactionRepository;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import reactor.core.publisher.Mono;

import java.math.BigDecimal;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.TreeMap;

@RestController
@RequestMapping("/api/metrics")
public class MetricsController {

    private final TransactionRepository repository;

    public MetricsController(TransactionRepository repository) {
        this.repository = repository;
    }

    @GetMapping("/summary")
    public Mono<Map<String, Object>> summary() {
        return repository.findAll()
                .collectList()
                .map(transactions -> {
                    long total = transactions.size();
                    long approved = transactions.stream()
                            .filter(transaction -> "APPROVED".equals(transaction.getStatus()))
                            .count();
                    long failed = transactions.stream()
                            .filter(transaction -> "FAILED".equals(transaction.getStatus()))
                            .count();
                    long fallback = transactions.stream()
                            .filter(transaction -> "FALLBACK".equals(transaction.getGatewayUsed()))
                            .count();
                    double averageLatency = transactions.stream()
                            .mapToLong(transaction -> transaction.getLatencyMs() == null
                                    ? 0L : transaction.getLatencyMs())
                            .average()
                            .orElse(0);
                    Map<String, BigDecimal> volumeByCurrency = new TreeMap<>();
                    transactions.stream()
                            .filter(transaction -> "APPROVED".equals(transaction.getStatus()))
                            .filter(transaction -> transaction.getCurrency() != null)
                            .forEach(transaction -> volumeByCurrency.merge(
                                    transaction.getCurrency(),
                                    transaction.getAmount() == null
                                            ? BigDecimal.ZERO : transaction.getAmount(),
                                    BigDecimal::add));

                    double successRate = total == 0 ? 0 : approved * 100.0 / total;
                    double fallbackRate = total == 0 ? 0 : fallback * 100.0 / total;

                    Map<String, Object> result = new LinkedHashMap<>();
                    result.put("totalTransactions", total);
                    result.put("approved", approved);
                    result.put("failed", failed);
                    result.put("fallbackUsed", fallback);
                    result.put("successRatePercent", Math.round(successRate * 100.0) / 100.0);
                    result.put("fallbackRatePercent", Math.round(fallbackRate * 100.0) / 100.0);
                    result.put("avgLatencyMs", Math.round(averageLatency));
                    result.put("volumeByCurrency", volumeByCurrency);
                    return result;
                });
    }
}
