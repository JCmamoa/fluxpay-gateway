package com.paygateway.auth;

import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import reactor.core.publisher.Mono;
import reactor.core.scheduler.Schedulers;

import java.util.List;
import java.util.Map;

@RestController
@RequestMapping("/auth")
public class AuthController {

    private final CredentialService credentialService;
    private final JwtTokenService tokenService;

    public AuthController(CredentialService credentialService, JwtTokenService tokenService) {
        this.credentialService = credentialService;
        this.tokenService = tokenService;
    }

    public record LoginRequest(@NotBlank String username, @NotBlank String password) {}

    @PostMapping("/login")
    public Mono<ResponseEntity<Map<String, String>>> login(@Valid @RequestBody LoginRequest request) {
        return Mono.fromCallable(() -> {
            if (!credentialService.authenticate(request.username(), request.password())) {
                return ResponseEntity.<Map<String, String>>status(HttpStatus.UNAUTHORIZED)
                        .body(Map.of("error", "Credenciais inválidas"));
            }

            String token = tokenService.generateToken(request.username(), List.of("payments", "metrics"));
            return ResponseEntity.ok(Map.of(
                    "access_token", token,
                    "token_type", "Bearer",
                    "expires_in", "3600"));
        }).subscribeOn(Schedulers.boundedElastic());
    }
}
