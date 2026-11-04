package com.paygateway.service;

import com.paygateway.domain.AppUser;
import com.paygateway.dto.CreateUserRequest;
import com.paygateway.repository.AppUserRepository;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.stereotype.Service;
import reactor.core.publisher.Mono;
import reactor.core.scheduler.Schedulers;

import java.nio.charset.StandardCharsets;
import java.time.Instant;
@Service
public class UserProvisioningService {

    private static final String DEFAULT_SCOPES = "payments metrics";

    private final AppUserRepository repository;
    private final PasswordEncoder passwordEncoder;

    public UserProvisioningService(AppUserRepository repository, PasswordEncoder passwordEncoder) {
        this.repository = repository;
        this.passwordEncoder = passwordEncoder;
    }

    public Mono<AppUser> createUser(CreateUserRequest request) {
        validateBcryptPasswordLength(request.password());
        return Mono.fromCallable(() -> passwordEncoder.encode(request.password()))
                .subscribeOn(Schedulers.boundedElastic())
                .flatMap(passwordHash -> repository.insert(
                                request.username(), passwordHash, DEFAULT_SCOPES, true, 0)
                        .thenReturn(new AppUser(
                                request.username(), passwordHash, DEFAULT_SCOPES, true, 0)))
                .onErrorMap(DuplicateKeyException.class,
                        error -> new UserAlreadyExistsException(request.username()));
    }

    public Mono<AppUser> setEnabled(String username, boolean enabled) {
        return repository.findById(username)
                .switchIfEmpty(Mono.error(new UserNotFoundException(username)))
                .flatMap(user -> {
                    user.setEnabled(enabled);
                    return repository.save(user);
                });
    }

    public Mono<AppUser> changePassword(String username, String newPassword) {
        validateBcryptPasswordLength(newPassword);
        return Mono.fromCallable(() -> passwordEncoder.encode(newPassword))
                .subscribeOn(Schedulers.boundedElastic())
                .flatMap(passwordHash -> repository.findById(username)
                        .switchIfEmpty(Mono.error(new UserNotFoundException(username)))
                        .flatMap(user -> {
                            user.setPasswordHash(passwordHash);
                            user.setTokenVersion(user.getTokenVersion() + 1);
                            return repository.save(user);
                        }));
    }

    private void validateBcryptPasswordLength(String password) {
        if (password.getBytes(StandardCharsets.UTF_8).length > 72) {
            throw new IllegalArgumentException("A senha deve conter no máximo 72 bytes em UTF-8");
        }
    }
}
