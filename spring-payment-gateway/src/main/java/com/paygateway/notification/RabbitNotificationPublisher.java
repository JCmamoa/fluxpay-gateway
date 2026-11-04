package com.paygateway.notification;

import org.springframework.amqp.rabbit.core.RabbitTemplate;
import org.springframework.amqp.rabbit.connection.CorrelationData;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Profile;
import org.springframework.stereotype.Component;
import reactor.core.publisher.Mono;
import reactor.core.scheduler.Schedulers;

import java.util.UUID;
import java.util.concurrent.TimeUnit;

@Component
@Profile("dev")
public class RabbitNotificationPublisher implements NotificationPublisher {

    private final RabbitTemplate rabbitTemplate;
    private final String queue;

    public RabbitNotificationPublisher(
            RabbitTemplate rabbitTemplate,
            @Value("${app.messaging.recovery-queue}") String queue) {
        this.rabbitTemplate = rabbitTemplate;
        this.queue = queue;
    }

    @Override
    public Mono<Void> publish(RecoveryNotification notification) {
        return Mono.fromCallable(() -> {
                    CorrelationData correlation = new CorrelationData(UUID.randomUUID().toString());
                    rabbitTemplate.convertAndSend("", queue, notification, correlation);
                    CorrelationData.Confirm confirm = correlation.getFuture().get(5, TimeUnit.SECONDS);
                    if (!confirm.isAck() || correlation.getReturned() != null) {
                        throw new IllegalStateException("RabbitMQ did not accept the recovery notification");
                    }
                    return true;
                })
                .subscribeOn(Schedulers.boundedElastic())
                .then();
    }
}
