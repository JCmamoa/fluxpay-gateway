package com.paygateway.notification;

import org.springframework.amqp.rabbit.annotation.RabbitListener;
import org.springframework.context.annotation.Profile;
import org.springframework.stereotype.Component;

@Component
@Profile("dev")
public class SimulatedNotificationConsumer {

    private final SimulatedNotificationMailbox mailbox;

    public SimulatedNotificationConsumer(SimulatedNotificationMailbox mailbox) {
        this.mailbox = mailbox;
    }

    @RabbitListener(queues = "${app.messaging.recovery-queue}")
    public void receive(RecoveryNotification notification) {
        mailbox.receive(notification);
    }
}
