package com.paygateway.service;

import com.paygateway.domain.AppUser;
import com.paygateway.repository.AppUserRepository;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.ApplicationArguments;
import org.springframework.boot.ApplicationRunner;
import org.springframework.core.env.Environment;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.stereotype.Component;

import java.nio.charset.StandardCharsets;
import java.util.Arrays;

@Component
public class BootstrapAdminInitializer implements ApplicationRunner {

    private static final String ADMIN_SCOPES = "payments metrics users:write";

    private final AppUserRepository repository;
    private final PasswordEncoder passwordEncoder;
    private final String username;
    private final String password;
    private final boolean production;

    public BootstrapAdminInitializer(
            AppUserRepository repository,
            PasswordEncoder passwordEncoder,
            @Value("${app.bootstrap-admin.username:}") String username,
            @Value("${app.bootstrap-admin.password:}") String password,
            Environment environment) {
        this.repository = repository;
        this.passwordEncoder = passwordEncoder;
        this.username = username;
        this.password = password;
        this.production = Arrays.asList(environment.getActiveProfiles()).contains("prod");
    }

    @Override
    public void run(ApplicationArguments args) {
        if (username.isBlank() != password.isBlank()) {
            throw new IllegalStateException(
                    "BOOTSTRAP_ADMIN_USERNAME and BOOTSTRAP_ADMIN_PASSWORD must be configured together");
        }
        if (production && (username.isBlank() || password.length() < 12)) {
            throw new IllegalStateException(
                    "Production requires bootstrap admin credentials; password must contain at least 12 characters");
        }
        if (password.getBytes(StandardCharsets.UTF_8).length > 72) {
            throw new IllegalStateException("Bootstrap admin password must not exceed 72 UTF-8 bytes");
        }
        if (username.isBlank()) {
            return;
        }

        repository.findById(username)
                .switchIfEmpty(repository.insert(
                        username, passwordEncoder.encode(password), ADMIN_SCOPES, true, 0)
                        .thenReturn(new AppUser(username, "", ADMIN_SCOPES, true, 0)))
                .block();
    }
}
