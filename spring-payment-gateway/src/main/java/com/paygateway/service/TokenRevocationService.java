package com.paygateway.service;

import com.paygateway.domain.RevokedToken;
import com.paygateway.repository.RevokedTokenRepository;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.stereotype.Service;
import reactor.core.publisher.Mono;

import java.time.Instant;

@Service
public class TokenRevocationService {

    private final RevokedTokenRepository repository;

    public TokenRevocationService(RevokedTokenRepository repository) {
        this.repository = repository;
    }

    public Mono<Void> revoke(Jwt jwt) {
        String tokenId = jwt.getId();
        Instant expiresAt = jwt.getExpiresAt();
        if (tokenId == null || expiresAt == null) {
            return Mono.error(new IllegalArgumentException("Token sem identificador ou validade"));
        }

        return repository.deleteByExpiresAtBefore(Instant.now())
                .then(repository.insert(tokenId, expiresAt))
                .then();
    }
}
