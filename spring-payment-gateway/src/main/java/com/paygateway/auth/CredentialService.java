package com.paygateway.auth;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.stereotype.Service;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;

@Service
public class CredentialService {

    private final String username;
    private final String passwordHash;
    private final PasswordEncoder passwordEncoder;

    public CredentialService(
            @Value("${app.auth.username}") String username,
            @Value("${app.auth.password}") String password,
            PasswordEncoder passwordEncoder) {
        if (username.isBlank() || password.isBlank()) {
            throw new IllegalStateException(
                    "Configure AUTH_USERNAME and AUTH_PASSWORD environment variables before starting the application");
        }

        this.username = username;
        this.passwordEncoder = passwordEncoder;
        this.passwordHash = passwordEncoder.encode(password);
    }

    public boolean authenticate(String candidateUsername, String candidatePassword) {
        if (candidateUsername == null || candidatePassword == null) {
            return false;
        }

        boolean usernameMatches = MessageDigest.isEqual(
                username.getBytes(StandardCharsets.UTF_8),
                candidateUsername.getBytes(StandardCharsets.UTF_8));
        boolean passwordMatches = passwordEncoder.matches(candidatePassword, passwordHash);
        return usernameMatches & passwordMatches;
    }
}
