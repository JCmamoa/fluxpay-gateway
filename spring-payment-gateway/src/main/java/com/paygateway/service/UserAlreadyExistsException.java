package com.paygateway.service;

public class UserAlreadyExistsException extends RuntimeException {
    public UserAlreadyExistsException(String username) {
        super("Usuário já existe: " + username);
    }
}
