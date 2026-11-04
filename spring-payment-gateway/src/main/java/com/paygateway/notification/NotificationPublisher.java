package com.paygateway.notification;

import reactor.core.publisher.Mono;

public interface NotificationPublisher {
    Mono<Void> publish(RecoveryNotification notification);
}
