package com.paygateway.repository;

import com.paygateway.domain.AppUser;
import org.springframework.data.r2dbc.repository.Modifying;
import org.springframework.data.r2dbc.repository.Query;
import org.springframework.data.repository.reactive.ReactiveCrudRepository;
import reactor.core.publisher.Mono;

public interface AppUserRepository extends ReactiveCrudRepository<AppUser, String> {
    @Modifying
    @Query("""
            INSERT INTO app_users (username, password_hash, scopes, enabled, token_version)
            VALUES (:username, :passwordHash, :scopes, :enabled, :tokenVersion)
            """)
    Mono<Integer> insert(String username, String passwordHash, String scopes,
                         boolean enabled, int tokenVersion);
}
