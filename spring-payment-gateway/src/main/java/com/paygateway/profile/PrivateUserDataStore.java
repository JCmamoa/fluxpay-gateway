package com.paygateway.profile;

import org.flywaydb.core.Flyway;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.datasource.DriverManagerDataSource;
import org.springframework.stereotype.Repository;

import javax.sql.DataSource;
import java.sql.Timestamp;
import java.time.Instant;
import java.util.List;
import java.util.Optional;

@Repository
public class PrivateUserDataStore {

    private final JdbcTemplate jdbc;

    public PrivateUserDataStore(
            @Value("${app.private-profile-database.url}") String url,
            @Value("${app.private-profile-database.username:}") String username,
            @Value("${app.private-profile-database.password:}") String password) {
        DataSource dataSource = new DriverManagerDataSource(url, username, password);
        Flyway.configure()
                .dataSource(dataSource)
                .locations("classpath:db/private-migration")
                .load()
                .migrate();
        this.jdbc = new JdbcTemplate(dataSource);
    }

    public Optional<UserProfile> findProfile(String username) {
        List<UserProfile> rows = jdbc.query("""
                        SELECT username, full_name, email, phone
                        FROM user_profiles WHERE username = ?
                        """,
                (result, row) -> new UserProfile(
                        result.getString("username"),
                        result.getString("full_name"),
                        result.getString("email"),
                        result.getString("phone")),
                username);
        return rows.stream().findFirst();
    }

    public void saveProfile(UserProfile profile) {
        int updated = jdbc.update("""
                        UPDATE user_profiles
                        SET full_name = ?, email = ?, phone = ?, updated_at = ?
                        WHERE username = ?
                        """,
                profile.fullName(), profile.email(), profile.phone(),
                Timestamp.from(Instant.now()), profile.username());
        if (updated == 0) {
            jdbc.update("""
                            INSERT INTO user_profiles (username, full_name, email, phone, updated_at)
                            VALUES (?, ?, ?, ?, ?)
                            """,
                    profile.username(), profile.fullName(), profile.email(), profile.phone(),
                    Timestamp.from(Instant.now()));
        }
    }

    public void replaceRecoveryCode(
            String username, String channel, String codeHash, Instant expiresAt) {
        jdbc.update("""
                DELETE FROM password_recovery_codes
                WHERE username = ? AND channel = ? AND consumed = FALSE
                """, username, channel);
        jdbc.update("""
                        INSERT INTO password_recovery_codes
                            (username, channel, code_hash, expires_at, attempts, consumed, created_at)
                        VALUES (?, ?, ?, ?, 0, FALSE, ?)
                        """,
                username, channel, codeHash, Timestamp.from(expiresAt), Timestamp.from(Instant.now()));
    }

    public Optional<RecoveryCodeRecord> findRecoveryCode(String username, String channel) {
        List<RecoveryCodeRecord> rows = jdbc.query("""
                        SELECT id, code_hash, expires_at, attempts
                        FROM password_recovery_codes
                        WHERE username = ? AND channel = ? AND consumed = FALSE
                          AND expires_at > CURRENT_TIMESTAMP AND attempts < 5
                        ORDER BY id DESC FETCH FIRST 1 ROW ONLY
                        """,
                (result, row) -> new RecoveryCodeRecord(
                        result.getLong("id"),
                        result.getString("code_hash"),
                        result.getTimestamp("expires_at").toInstant(),
                        result.getInt("attempts")),
                username, channel);
        return rows.stream().findFirst();
    }

    public void recordFailedAttempt(long codeId) {
        jdbc.update("""
                UPDATE password_recovery_codes SET attempts = attempts + 1
                WHERE id = ? AND consumed = FALSE AND attempts < 5
                """, codeId);
    }

    public boolean consumeRecoveryCode(long codeId) {
        return jdbc.update("""
                UPDATE password_recovery_codes SET consumed = TRUE
                WHERE id = ? AND consumed = FALSE AND attempts < 5
                  AND expires_at > CURRENT_TIMESTAMP
                """, codeId) == 1;
    }
}
