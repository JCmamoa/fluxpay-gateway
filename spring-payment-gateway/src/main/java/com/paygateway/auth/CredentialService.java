package com.paygateway.auth;

import com.paygateway.domain.AppUser;
import com.paygateway.repository.AppUserRepository;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.stereotype.Service;
import reactor.core.publisher.Mono;
import reactor.core.scheduler.Schedulers;

import java.nio.charset.StandardCharsets;
import java.util.UUID;

@Service
public class CredentialService {

    private final AppUserRepository userRepository;
    private final PasswordEncoder passwordEncoder;
    private final String dummyPasswordHash;

    public CredentialService(AppUserRepository userRepository, PasswordEncoder passwordEncoder) {
        this.userRepository = userRepository;
        this.passwordEncoder = passwordEncoder;
        this.dummyPasswordHash = passwordEncoder.encode(UUID.randomUUID().toString());
    }

    public Mono<AppUser> authenticate(String username, String password) {
        if (username == null || password == null) {
            return Mono.empty();
        }
        if (password.getBytes(StandardCharsets.UTF_8).length > 72) {
            return matches(password, dummyPasswordHash).then(Mono.empty());
        }

        return userRepository.findById(username)
                .filter(AppUser::isEnabled)
                .flatMap(user -> matches(password, user.getPasswordHash())
                        .flatMap(matches -> matches ? Mono.just(user) : Mono.empty()))
                .switchIfEmpty(matches(password, dummyPasswordHash).then(Mono.empty()));
    }

    private Mono<Boolean> matches(String password, String encodedPassword) {
        return Mono.fromCallable(() -> passwordEncoder.matches(password, encodedPassword))
                .subscribeOn(Schedulers.boundedElastic());
    }
}
