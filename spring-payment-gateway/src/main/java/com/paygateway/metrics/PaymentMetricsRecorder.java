package com.paygateway.metrics;

import io.micrometer.core.instrument.Counter;
import io.micrometer.core.instrument.MeterRegistry;
import io.micrometer.core.instrument.Timer;
import org.springframework.stereotype.Component;

import java.util.concurrent.TimeUnit;

@Component
public class PaymentMetricsRecorder {

    private final Counter approvedCounter;
    private final Counter failedCounter;
    private final Counter fallbackCounter;
    private final Timer latencyTimer;

    public PaymentMetricsRecorder(MeterRegistry registry) {
        this.approvedCounter = Counter.builder("payments.approved").register(registry);
        this.failedCounter = Counter.builder("payments.failed").register(registry);
        this.fallbackCounter = Counter.builder("payments.fallback_used").register(registry);
        this.latencyTimer = Timer.builder("payments.latency").register(registry);
    }

    public void recordApproved(long latencyMs) {
        approvedCounter.increment();
        latencyTimer.record(latencyMs, TimeUnit.MILLISECONDS);
    }

    public void recordFailed() {
        failedCounter.increment();
    }

    public void recordFallbackUsed() {
        fallbackCounter.increment();
    }
}
