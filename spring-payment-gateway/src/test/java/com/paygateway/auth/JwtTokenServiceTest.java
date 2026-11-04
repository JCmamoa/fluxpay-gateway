package com.paygateway.auth;

import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.security.oauth2.jwt.ReactiveJwtDecoder;
import reactor.test.StepVerifier;

import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;

@SpringBootTest(properties = {
        "app.auth.username=test-user",
        "app.auth.password=test-password",
        "jwt.secret=test-secret-with-at-least-thirty-two-bytes"
})
class JwtTokenServiceTest {

    @Autowired
    private JwtTokenService tokenService;

    @Autowired
    private ReactiveJwtDecoder decoder;

    @Test
    void tokenGeradoDeveSerAceitoPeloDecoder() {
        String token = tokenService.generateToken("tester", List.of("payments", "metrics"));

        StepVerifier.create(decoder.decode(token))
                .assertNext(jwt -> {
                    assertThat(jwt.getSubject()).isEqualTo("tester");
                    assertThat(jwt.getClaimAsString("scope")).isEqualTo("payments metrics");
                })
                .verifyComplete();
    }
}
