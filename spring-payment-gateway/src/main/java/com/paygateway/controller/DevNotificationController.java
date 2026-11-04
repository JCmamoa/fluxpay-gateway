package com.paygateway.controller;

import com.paygateway.notification.RecoveryNotification;
import com.paygateway.notification.SimulatedNotificationMailbox;
import org.springframework.context.annotation.Profile;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;

@RestController
@RequestMapping("/api/dev/notifications")
@Profile("dev")
public class DevNotificationController {

    private final SimulatedNotificationMailbox mailbox;

    public DevNotificationController(SimulatedNotificationMailbox mailbox) {
        this.mailbox = mailbox;
    }

    @GetMapping
    public List<RecoveryNotification> latest() {
        return mailbox.latest();
    }
}
