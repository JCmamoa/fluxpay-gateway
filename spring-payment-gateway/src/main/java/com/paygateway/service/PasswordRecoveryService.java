package com.paygateway.service;

import com.paygateway.domain.AppUser;
import com.paygateway.notification.NotificationPublisher;
import com.paygateway.notification.RecoveryNotification;
import com.paygateway.profile.PrivateUserDataStore;
import com.paygateway.profile.RecoveryChannel;
import com.paygateway.profile.RecoveryCodeRecord;
import com.paygateway.profile.UserProfile;
import com.paygateway.repository.AppUserRepository;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.stereotype.Service;
import reactor.core.publisher.Mono;
import reactor.core.scheduler.Schedulers;

import java.security.SecureRandom;
import java.time.Duration;
import java.time.Instant;
import java.util.Optional;

@Service
public class PasswordRecoveryService {

    private static final SecureRandom RANDOM = new SecureRandom();
    private static final String REQUEST_ACCEPTED =
            "Se a conta e o canal forem válidos, você receberá um código de recuperação.";

    private final PrivateUserDataStore privateStore;
    private final AppUserRepository users;
    private final UserProvisioningService userProvisioning;
    private final NotificationPublisher notifications;
    private final PasswordEncoder passwordEncoder;
    private final PasswordRecoveryLimiter limiter;
    private final Duration codeLifetime;

    public PasswordRecoveryService(
            PrivateUserDataStore privateStore,
            AppUserRepository users,
            UserProvisioningService userProvisioning,
            NotificationPublisher notifications,
            PasswordEncoder passwordEncoder,
            PasswordRecoveryLimiter limiter,
            @Value("${app.password-recovery.code-lifetime:PT10M}") Duration codeLifetime) {
        this.privateStore = privateStore;
        this.users = users;
        this.userProvisioning = userProvisioning;
        this.notifications = notifications;
        this.passwordEncoder = passwordEncoder;
        this.limiter = limiter;
        if (codeLifetime.isZero() || codeLifetime.isNegative()
                || codeLifetime.compareTo(Duration.ofMinutes(15)) > 0) {
            throw new IllegalStateException("Password recovery code lifetime must be between 1 and 15 minutes");
        }
        this.codeLifetime = codeLifetime;
    }

    public Mono<String> requestCode(String username, RecoveryChannel channel, String clientAddress) {
        if (!limiter.allow(username, clientAddress)) {
            return Mono.just(REQUEST_ACCEPTED);
        }
        return users.findById(username)
                .filter(AppUser::isEnabled)
                .flatMap(user -> findProfile(username)
                        .flatMap(profile -> Mono.justOrEmpty(destination(profile, channel)
                                .map(recipient -> new RecoveryTarget(recipient, channel)))))
                .flatMap(target -> Mono.fromCallable(() -> {
                            String code = "%06d".formatted(RANDOM.nextInt(1_000_000));
                            return new CodeMaterial(code, passwordEncoder.encode(code));
                        })
                        .subscribeOn(Schedulers.boundedElastic())
                        .flatMap(material -> saveAndPublish(username, target, material)))
                .thenReturn(REQUEST_ACCEPTED);
    }

    public Mono<Void> confirm(
            String username, RecoveryChannel channel, String code, String newPassword) {
        if (newPassword.getBytes(java.nio.charset.StandardCharsets.UTF_8).length > 72) {
            return Mono.error(new IllegalArgumentException(
                    "A senha deve conter no máximo 72 bytes em UTF-8"));
        }

        return findRecoveryCode(username, channel)
                .switchIfEmpty(Mono.error(new InvalidRecoveryCodeException()))
                .flatMap(record -> Mono.fromCallable(() ->
                                passwordEncoder.matches(code, record.codeHash()))
                        .subscribeOn(Schedulers.boundedElastic())
                        .flatMap(matches -> matches
                                ? consumeAndChangePassword(record, username, newPassword)
                                : recordFailedAttempt(record.id())));
    }

    private Mono<UserProfile> findProfile(String username) {
        return Mono.fromCallable(() -> privateStore.findProfile(username))
                .subscribeOn(Schedulers.boundedElastic())
                .flatMap(Mono::justOrEmpty);
    }

    private Optional<String> destination(UserProfile profile, RecoveryChannel channel) {
        String value = channel == RecoveryChannel.EMAIL ? profile.email() : profile.phone();
        return Optional.ofNullable(value).filter(contact -> !contact.isBlank());
    }

    private Mono<Void> saveAndPublish(
            String username, RecoveryTarget target, CodeMaterial material) {
        Instant expiresAt = Instant.now().plus(codeLifetime);
        return Mono.fromRunnable(() -> privateStore.replaceRecoveryCode(
                        username, target.channel().name(), material.hash(), expiresAt))
                .subscribeOn(Schedulers.boundedElastic())
                .then(notifications.publish(new RecoveryNotification(
                        target.channel().name(), target.destination(), material.code())));
    }

    private Mono<RecoveryCodeRecord> findRecoveryCode(String username, RecoveryChannel channel) {
        return Mono.fromCallable(() -> privateStore.findRecoveryCode(username, channel.name()))
                .subscribeOn(Schedulers.boundedElastic())
                .flatMap(Mono::justOrEmpty);
    }

    private Mono<Void> recordFailedAttempt(long codeId) {
        return Mono.fromRunnable(() -> privateStore.recordFailedAttempt(codeId))
                .subscribeOn(Schedulers.boundedElastic())
                .then(Mono.error(new InvalidRecoveryCodeException()));
    }

    private Mono<Void> consumeAndChangePassword(
            RecoveryCodeRecord record, String username, String newPassword) {
        return users.findById(username)
                .filter(AppUser::isEnabled)
                .switchIfEmpty(Mono.error(new InvalidRecoveryCodeException()))
                .then(Mono.fromCallable(() -> privateStore.consumeRecoveryCode(record.id()))
                        .subscribeOn(Schedulers.boundedElastic()))
                .filter(consumed -> consumed)
                .switchIfEmpty(Mono.error(new InvalidRecoveryCodeException()))
                .then(userProvisioning.changePassword(username, newPassword))
                .then();
    }

    private record RecoveryTarget(String destination, RecoveryChannel channel) {}
    private record CodeMaterial(String code, String hash) {}
}
