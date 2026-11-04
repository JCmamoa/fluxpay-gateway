package com.paygateway.notification;

import org.springframework.context.annotation.Profile;
import org.springframework.stereotype.Component;
import reactor.core.publisher.Mono;

@Component
@Profile("test")
public class InMemoryNotificationPublisher implements NotificationPublisher {

    private final SimulatedNotificationMailbox mailbox;

    public InMemoryNotificationPublisher(SimulatedNotificationMailbox mailbox) {
        this.mailbox = mailbox;
    }

    @Override
    public Mono<Void> publish(RecoveryNotification notification) {
        return Mono.fromRunnable(() -> mailbox.receive(notification)).then();
    }
}
