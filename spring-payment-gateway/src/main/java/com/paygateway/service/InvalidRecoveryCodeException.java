package com.paygateway.service;

public class InvalidRecoveryCodeException extends RuntimeException {
    public InvalidRecoveryCodeException() {
        super("Código inválido, expirado ou já utilizado");
    }
}
