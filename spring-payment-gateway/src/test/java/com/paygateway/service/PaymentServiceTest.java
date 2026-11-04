package com.paygateway.service;

import com.paygateway.dto.PaymentRequest;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import reactor.test.StepVerifier;

import java.math.BigDecimal;

import static org.assertj.core.api.Assertions.assertThat;

@SpringBootTest(properties = {
        "app.auth.username=test-user",
        "app.auth.password=test-password",
        "jwt.secret=test-secret-with-at-least-thirty-two-bytes"
})
class PaymentServiceTest {

    @Autowired
    private PaymentService paymentService;

    @Test
    void deveProcessarPagamentoERetornarStatusValido() {
        PaymentRequest request = new PaymentRequest(
                "payer-1", "payee-1", new BigDecimal("150.00"), "BRL");

        StepVerifier.create(paymentService.processPayment(request))
                .assertNext(response -> {
                    assertThat(response.transactionId()).startsWith("TXN-");
                    assertThat(response.status()).isIn("APPROVED", "FAILED");
                    assertThat(response.gatewayUsed()).isIn("PRIMARY", "FALLBACK", "NONE");
                })
                .verifyComplete();
    }
}
