package com.paygateway.controller;

import com.paygateway.dto.PaymentRequest;
import com.paygateway.dto.PaymentResponse;
import com.paygateway.service.PaymentService;
import jakarta.validation.Valid;
import org.springframework.http.ResponseEntity;
import org.springframework.http.HttpStatus;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import reactor.core.publisher.Mono;

@RestController
@RequestMapping("/api/payments")
public class PaymentController {

    private final PaymentService paymentService;

    public PaymentController(PaymentService paymentService) {
        this.paymentService = paymentService;
    }

    @PostMapping
    public Mono<ResponseEntity<PaymentResponse>> createPayment(
            @Valid @RequestBody PaymentRequest request,
            @AuthenticationPrincipal Jwt principal,
            @RequestHeader("Idempotency-Key") String idempotencyKey) {
        if (idempotencyKey.isBlank() || idempotencyKey.length() > 128) {
            throw new IllegalArgumentException("Idempotency-Key deve ter entre 1 e 128 caracteres");
        }
        return paymentService.processPayment(request, principal.getSubject(), idempotencyKey)
                .map(response -> ResponseEntity.status(
                        "APPROVED".equals(response.status())
                                ? HttpStatus.CREATED : HttpStatus.SERVICE_UNAVAILABLE)
                        .body(response));
    }
}
