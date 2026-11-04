package com.paygateway.profile;

import java.time.Instant;

public record RecoveryCodeRecord(long id, String codeHash, Instant expiresAt, int attempts) {}
