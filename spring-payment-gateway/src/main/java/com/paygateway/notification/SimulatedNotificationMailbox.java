package com.paygateway.notification;

import org.springframework.context.annotation.Profile;
import org.springframework.stereotype.Component;

import java.util.ArrayDeque;
import java.util.List;

@Component
@Profile({"dev", "test"})
public class SimulatedNotificationMailbox {

    private static final int CAPACITY = 100;
    private final ArrayDeque<RecoveryNotification> notifications = new ArrayDeque<>();

    public synchronized void receive(RecoveryNotification notification) {
        while (notifications.size() >= CAPACITY) {
            notifications.removeFirst();
        }
        notifications.addLast(notification);
    }

    public synchronized List<RecoveryNotification> latest() {
        return List.copyOf(notifications);
    }
}
