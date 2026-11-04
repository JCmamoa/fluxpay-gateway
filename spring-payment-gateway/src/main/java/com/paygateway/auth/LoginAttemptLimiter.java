package com.paygateway.auth;

import org.springframework.stereotype.Component;

import java.time.Duration;
import java.util.LinkedHashMap;
import java.util.Locale;
import java.util.Map;

@Component
public class LoginAttemptLimiter {

    private static final int MAX_ATTEMPTS = 5;
    private static final int MAX_TRACKED_USERNAMES = 10_000;
    private static final long WINDOW_MILLIS = Duration.ofMinutes(15).toMillis();

    private final Map<String, AttemptWindow> attempts = new LinkedHashMap<>(16, 0.75f, true) {
        @Override
        protected boolean removeEldestEntry(Map.Entry<String, AttemptWindow> eldest) {
            return size() > MAX_TRACKED_USERNAMES;
        }
    };

    public synchronized Duration tryAcquire(String username) {
        String key = username.toLowerCase(Locale.ROOT);
        long now = System.currentTimeMillis();
        AttemptWindow window = attempts.computeIfAbsent(key, ignored -> new AttemptWindow(now));

        if (now >= window.resetAtMillis) {
            window.attempts = 0;
            window.resetAtMillis = now + WINDOW_MILLIS;
        }
        if (window.attempts >= MAX_ATTEMPTS) {
            return Duration.ofMillis(window.resetAtMillis - now);
        }

        window.attempts++;
        return Duration.ZERO;
    }

    public synchronized void recordSuccess(String username) {
        attempts.remove(username.toLowerCase(Locale.ROOT));
    }

    private static final class AttemptWindow {
        private int attempts;
        private long resetAtMillis;

        private AttemptWindow(long now) {
            this.resetAtMillis = now + WINDOW_MILLIS;
        }
    }
}
