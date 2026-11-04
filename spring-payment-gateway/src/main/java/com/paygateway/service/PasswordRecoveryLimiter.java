package com.paygateway.service;

import org.springframework.stereotype.Component;

import java.time.Duration;
import java.util.LinkedHashMap;
import java.util.Locale;
import java.util.Map;

@Component
public class PasswordRecoveryLimiter {

    private static final int MAX_REQUESTS = 3;
    private static final int MAX_REQUESTS_PER_CLIENT = 10;
    private static final int MAX_TRACKED_USERNAMES = 10_000;
    private static final long WINDOW_MILLIS = Duration.ofMinutes(15).toMillis();

    private final Map<String, Window> usernameWindows = new LinkedHashMap<>(16, 0.75f, true) {
        @Override
        protected boolean removeEldestEntry(Map.Entry<String, Window> eldest) {
            return size() > MAX_TRACKED_USERNAMES;
        }
    };
    private final Map<String, Window> clientWindows = new LinkedHashMap<>(16, 0.75f, true) {
        @Override
        protected boolean removeEldestEntry(Map.Entry<String, Window> eldest) {
            return size() > MAX_TRACKED_USERNAMES;
        }
    };

    public synchronized boolean allow(String username, String clientAddress) {
        long now = System.currentTimeMillis();
        return consume(usernameWindows, username.toLowerCase(Locale.ROOT), MAX_REQUESTS, now)
                && consume(clientWindows, clientAddress, MAX_REQUESTS_PER_CLIENT, now);
    }

    private boolean consume(Map<String, Window> windows, String key, int limit, long now) {
        Window window = windows.computeIfAbsent(key, ignored -> new Window(now + WINDOW_MILLIS));
        if (now >= window.resetAt) {
            window.attempts = 0;
            window.resetAt = now + WINDOW_MILLIS;
        }
        if (window.attempts >= limit) {
            return false;
        }
        window.attempts++;
        return true;
    }

    private static final class Window {
        private int attempts;
        private long resetAt;

        private Window(long resetAt) {
            this.resetAt = resetAt;
        }
    }
}
