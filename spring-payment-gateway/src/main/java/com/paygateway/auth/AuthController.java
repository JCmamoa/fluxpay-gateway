package com.paygateway.auth;

import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.Size;
import com.paygateway.service.TokenRevocationService;
import org.springframework.http.HttpHeaders;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import reactor.core.publisher.Mono;

import java.util.Map;

@RestController
@RequestMapping("/auth")
public class AuthController {

    private final CredentialService credentialService;
    private final JwtTokenService tokenService;
    private final TokenRevocationService tokenRevocationService;
    private final LoginAttemptLimiter loginAttemptLimiter;

    public AuthController(CredentialService credentialService, JwtTokenService tokenService,
                          TokenRevocationService tokenRevocationService,
                          LoginAttemptLimiter loginAttemptLimiter) {
        this.credentialService = credentialService;
        this.tokenService = tokenService;
        this.tokenRevocationService = tokenRevocationService;
        this.loginAttemptLimiter = loginAttemptLimiter;
    }

    public record LoginRequest(
            @NotBlank @Size(min = 3, max = 64) @Pattern(regexp = "[A-Za-z0-9._-]+") String username,
            @NotBlank @Size(max = 72) String password) {}

    @PostMapping("/login")
    public Mono<ResponseEntity<Map<String, String>>> login(@Valid @RequestBody LoginRequest request) {
        java.time.Duration retryAfter = loginAttemptLimiter.tryAcquire(request.username());
        if (!retryAfter.isZero()) {
            long retrySeconds = Math.max(1, retryAfter.toSeconds());
            return Mono.just(ResponseEntity.status(HttpStatus.TOO_MANY_REQUESTS)
                    .header(HttpHeaders.RETRY_AFTER, Long.toString(retrySeconds))
                    .body(Map.of("error", "Muitas tentativas", "message", "Tente novamente mais tarde")));
        }

        return credentialService.authenticate(request.username(), request.password())
                .map(user -> {
                    loginAttemptLimiter.recordSuccess(request.username());
                    String token = tokenService.generateToken(
                            user.getUsername(), user.getScopeList(), user.getTokenVersion());
                    return ResponseEntity.ok(Map.of(
                    "access_token", token,
                    "token_type", "Bearer",
                    "expires_in", "900"));
                })
                .defaultIfEmpty(ResponseEntity.status(HttpStatus.UNAUTHORIZED)
                        .body(Map.of("error", "Credenciais inválidas")));
    }

    @PostMapping("/logout")
    public Mono<ResponseEntity<Void>> logout(@AuthenticationPrincipal Jwt jwt) {
        return tokenRevocationService.revoke(jwt)
                .thenReturn(ResponseEntity.noContent().build());
    }
}
