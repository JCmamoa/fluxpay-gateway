package com.paygateway.repository;

import com.paygateway.domain.RevokedToken;
import org.springframework.data.repository.reactive.ReactiveCrudRepository;
import org.springframework.data.r2dbc.repository.Modifying;
import org.springframework.data.r2dbc.repository.Query;
import reactor.core.publisher.Mono;

import java.time.Instant;

public interface RevokedTokenRepository extends ReactiveCrudRepository<RevokedToken, String> {
    Mono<Void> deleteByExpiresAtBefore(Instant instant);

    @Modifying
    @Query("INSERT INTO revoked_tokens (token_id, expires_at) VALUES (:tokenId, :expiresAt)")
    Mono<Integer> insert(String tokenId, Instant expiresAt);
}
