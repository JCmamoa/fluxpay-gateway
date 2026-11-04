package com.paygateway.controller;

import com.paygateway.dto.ConfirmPasswordRecoveryRequest;
import com.paygateway.dto.PasswordRecoveryRequest;
import com.paygateway.service.PasswordRecoveryService;
import jakarta.validation.Valid;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.server.ServerWebExchange;
import reactor.core.publisher.Mono;

import java.util.Map;

@RestController
@RequestMapping("/auth/password-recovery")
public class PasswordRecoveryController {

    private final PasswordRecoveryService recoveryService;

    public PasswordRecoveryController(PasswordRecoveryService recoveryService) {
        this.recoveryService = recoveryService;
    }

    @PostMapping("/request")
    public Mono<ResponseEntity<Map<String, String>>> request(
            @Valid @RequestBody PasswordRecoveryRequest request,
            ServerWebExchange exchange) {
        var remoteAddress = exchange.getRequest().getRemoteAddress();
        String clientAddress = remoteAddress == null || remoteAddress.getAddress() == null
                ? "unknown" : remoteAddress.getAddress().getHostAddress();
        return recoveryService.requestCode(request.username(), request.channel(), clientAddress)
                .map(message -> ResponseEntity.accepted().body(Map.of("message", message)));
    }

    @PostMapping("/confirm")
    public Mono<ResponseEntity<Map<String, String>>> confirm(
            @Valid @RequestBody ConfirmPasswordRecoveryRequest request) {
        return recoveryService.confirm(
                        request.username(), request.channel(), request.code(), request.newPassword())
                .thenReturn(ResponseEntity.status(HttpStatus.NO_CONTENT).<Map<String, String>>build());
    }
}
