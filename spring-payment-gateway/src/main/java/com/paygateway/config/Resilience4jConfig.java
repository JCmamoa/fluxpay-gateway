package com.paygateway.config;

import io.github.resilience4j.circuitbreaker.CircuitBreakerRegistry;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.ApplicationRunner;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

@Configuration
public class Resilience4jConfig {

    private static final Logger log = LoggerFactory.getLogger(Resilience4jConfig.class);

    @Bean
    public ApplicationRunner circuitBreakerLogger(CircuitBreakerRegistry registry) {
        return args -> registry.circuitBreaker("externalGateway")
                .getEventPublisher()
                .onStateTransition(event -> log.warn("Circuit breaker '{}': {}",
                        event.getCircuitBreakerName(), event.getStateTransition()));
    }
}
