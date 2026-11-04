package com.paygateway.config;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.security.config.Customizer;
import org.springframework.security.config.annotation.web.reactive.EnableWebFluxSecurity;
import org.springframework.security.config.web.server.ServerHttpSecurity;
import org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.security.oauth2.jose.jws.MacAlgorithm;
import org.springframework.security.oauth2.jwt.BadJwtException;
import org.springframework.security.oauth2.jwt.NimbusReactiveJwtDecoder;
import org.springframework.security.oauth2.jwt.ReactiveJwtDecoder;
import org.springframework.security.web.server.SecurityWebFilterChain;
import com.paygateway.repository.RevokedTokenRepository;
import com.paygateway.repository.AppUserRepository;
import org.springframework.web.cors.CorsConfiguration;
import org.springframework.web.cors.reactive.CorsConfigurationSource;
import org.springframework.web.cors.reactive.UrlBasedCorsConfigurationSource;

import javax.crypto.spec.SecretKeySpec;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.List;

@Configuration
@EnableWebFluxSecurity
public class SecurityConfig {

    @Bean
    public SecurityWebFilterChain springSecurityFilterChain(
            ServerHttpSecurity http, ReactiveJwtDecoder jwtDecoder) {
        return http
                .csrf(ServerHttpSecurity.CsrfSpec::disable)
                .cors(Customizer.withDefaults())
                .authorizeExchange(exchange -> exchange
                        .pathMatchers("/auth/login", "/auth/password-recovery/**",
                                "/api/support/assistant", "/assistant.html", "/assistant.js",
                                "/assistant.css", "/actuator/health").permitAll()
                        .pathMatchers("/api/dev/**").hasAuthority("SCOPE_users:write")
                        .pathMatchers("/actuator/**").hasAuthority("SCOPE_metrics")
                        .pathMatchers("/api/payments/**").hasAuthority("SCOPE_payments")
                        .pathMatchers("/api/metrics/**").hasAuthority("SCOPE_metrics")
                        .pathMatchers("/api/users/**").hasAuthority("SCOPE_users:write")
                        .anyExchange().authenticated())
                .oauth2ResourceServer(oauth2 -> oauth2.jwt(jwt -> jwt.jwtDecoder(jwtDecoder)))
                .build();
    }

    @Bean
    public ReactiveJwtDecoder jwtDecoder(
            @Value("${jwt.secret}") String secret,
            RevokedTokenRepository revokedTokens,
            AppUserRepository users) {
        if (secret == null || secret.getBytes(StandardCharsets.UTF_8).length < 32) {
            throw new IllegalStateException("JWT_SECRET must contain at least 32 bytes");
        }
        SecretKeySpec key = new SecretKeySpec(secret.getBytes(StandardCharsets.UTF_8), "HmacSHA256");
        ReactiveJwtDecoder delegate = NimbusReactiveJwtDecoder.withSecretKey(key)
                .macAlgorithm(MacAlgorithm.HS256)
                .build();
        return token -> delegate.decode(token).flatMap(jwt -> {
            if (jwt.getId() == null) {
                return reactor.core.publisher.Mono.error(new BadJwtException("JWT is missing its token ID"));
            }
            Number tokenVersion = jwt.getClaim("token_version");
            return revokedTokens.existsById(jwt.getId())
                    .flatMap(revoked -> revoked
                            ? reactor.core.publisher.Mono.error(new BadJwtException("JWT has been revoked"))
                            : users.findById(jwt.getSubject())
                                    .filter(user -> user.isEnabled()
                                            && tokenVersion != null
                                            && tokenVersion.intValue() == user.getTokenVersion())
                                    .switchIfEmpty(reactor.core.publisher.Mono.error(
                                            new BadJwtException("JWT subject is not an active user")))
                                    .thenReturn(jwt));
        });
    }

    @Bean
    public PasswordEncoder passwordEncoder() {
        return new BCryptPasswordEncoder();
    }

    @Bean
    public CorsConfigurationSource corsConfigurationSource(
            @Value("${app.cors.allowed-origins:http://localhost:3000,http://localhost:5173}")
            List<String> allowedOrigins) {
        CorsConfiguration config = new CorsConfiguration();
        config.setAllowedOrigins(allowedOrigins);
        config.setAllowedMethods(List.of("GET", "POST", "PUT", "PATCH", "OPTIONS"));
        config.setAllowedHeaders(List.of(
                "Authorization", "Content-Type", "Accept", "Idempotency-Key"));
        config.setExposedHeaders(List.of("Retry-After"));
        config.setAllowCredentials(false);
        config.setMaxAge(Duration.ofHours(1));

        UrlBasedCorsConfigurationSource source = new UrlBasedCorsConfigurationSource();
        source.registerCorsConfiguration("/auth/**", config);
        source.registerCorsConfiguration("/api/**", config);
        source.registerCorsConfiguration("/actuator/**", config);
        return source;
    }
}
