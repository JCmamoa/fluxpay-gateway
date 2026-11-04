package com.paygateway.gateway;

import org.springframework.stereotype.Component;
import reactor.core.publisher.Mono;

import java.time.Duration;
import java.util.concurrent.ThreadLocalRandom;

@Component
public class ExternalGatewayClient {

    public Mono<Boolean> processPrimary(String transactionId) {
        return Mono.fromSupplier(() -> {
                    if (ThreadLocalRandom.current().nextInt(100) < 20) {
                        throw new RuntimeException("Gateway primário indisponível");
                    }
                    return true;
                })
                .delayElement(Duration.ofMillis(simulatedLatency()));
    }

    public Mono<Boolean> processFallback(String transactionId) {
        return Mono.just(true)
                .delayElement(Duration.ofMillis(ThreadLocalRandom.current().nextInt(50, 300)));
    }

    private long simulatedLatency() {
        if (ThreadLocalRandom.current().nextInt(100) < 15) {
            return ThreadLocalRandom.current().nextInt(2500, 4000);
        }
        return ThreadLocalRandom.current().nextInt(50, 400);
    }
}
