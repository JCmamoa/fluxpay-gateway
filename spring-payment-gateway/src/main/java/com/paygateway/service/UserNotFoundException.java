package com.paygateway.service;

public class UserNotFoundException extends RuntimeException {
    public UserNotFoundException(String username) {
        super("Usuário não encontrado: " + username);
    }
}
