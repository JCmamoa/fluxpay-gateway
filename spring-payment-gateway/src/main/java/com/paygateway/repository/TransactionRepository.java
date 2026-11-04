package com.paygateway.repository;

import com.paygateway.domain.Transaction;
import org.springframework.data.repository.reactive.ReactiveCrudRepository;
import reactor.core.publisher.Flux;
import reactor.core.publisher.Mono;

public interface TransactionRepository extends ReactiveCrudRepository<Transaction, Long> {
    Flux<Transaction> findByStatus(String status);
    Mono<Transaction> findByIdempotencyKey(String idempotencyKey);
}
