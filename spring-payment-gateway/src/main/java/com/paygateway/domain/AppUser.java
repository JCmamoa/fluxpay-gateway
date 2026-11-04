package com.paygateway.domain;

import org.springframework.data.annotation.Id;
import org.springframework.data.relational.core.mapping.Table;

import java.util.Arrays;
import java.util.List;

@Table("app_users")
public class AppUser {

    @Id
    private String username;
    private String passwordHash;
    private String scopes;
    private boolean enabled;
    private int tokenVersion;

    public AppUser() {}

    public AppUser(String username, String passwordHash, String scopes,
                   boolean enabled, int tokenVersion) {
        this.username = username;
        this.passwordHash = passwordHash;
        this.scopes = scopes;
        this.enabled = enabled;
        this.tokenVersion = tokenVersion;
    }

    public String getUsername() { return username; }
    public void setUsername(String username) { this.username = username; }
    public String getPasswordHash() { return passwordHash; }
    public void setPasswordHash(String passwordHash) { this.passwordHash = passwordHash; }
    public String getScopes() { return scopes; }
    public void setScopes(String scopes) { this.scopes = scopes; }
    public boolean isEnabled() { return enabled; }
    public void setEnabled(boolean enabled) { this.enabled = enabled; }
    public int getTokenVersion() { return tokenVersion; }
    public void setTokenVersion(int tokenVersion) { this.tokenVersion = tokenVersion; }

    public List<String> getScopeList() {
        return Arrays.stream(scopes.split("\\s+")).filter(scope -> !scope.isBlank()).toList();
    }
}
