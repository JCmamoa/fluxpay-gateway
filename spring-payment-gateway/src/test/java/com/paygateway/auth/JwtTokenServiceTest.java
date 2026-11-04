package com.paygateway.auth;

import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.security.oauth2.jwt.ReactiveJwtDecoder;
import org.springframework.test.context.ActiveProfiles;
import reactor.test.StepVerifier;

import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;

@SpringBootTest(properties = {
        "jwt.secret=test-secret-with-at-least-thirty-two-bytes"
})
@ActiveProfiles("test")
class JwtTokenServiceTest {

    @Autowired
    private JwtTokenService tokenService;

    @Autowired
    private ReactiveJwtDecoder decoder;

    @Test
    void tokenGeradoDeveSerAceitoPeloDecoder() {
        String token = tokenService.generateToken("test-admin", List.of("payments", "metrics"));

        StepVerifier.create(decoder.decode(token))
                .assertNext(jwt -> {
                    assertThat(jwt.getSubject()).isEqualTo("test-admin");
                    assertThat(jwt.getClaimAsString("scope")).isEqualTo("payments metrics");
                })
                .verifyComplete();
    }
}
