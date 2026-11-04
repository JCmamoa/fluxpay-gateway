package com.paygateway.controller;

import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.core.ParameterizedTypeReference;
import com.paygateway.repository.AppUserRepository;
import com.paygateway.notification.SimulatedNotificationMailbox;
import org.springframework.security.oauth2.jwt.ReactiveJwtDecoder;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.test.web.reactive.server.WebTestClient;
import org.springframework.test.web.reactive.server.EntityExchangeResult;

import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.CompletableFuture;

import static org.assertj.core.api.Assertions.assertThat;

@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
@ActiveProfiles("test")
class ProductionSecurityIntegrationTest {

    private static final ParameterizedTypeReference<Map<String, String>> STRING_MAP =
            new ParameterizedTypeReference<>() {};

    @Autowired
    private WebTestClient webClient;

    @Autowired
    private AppUserRepository appUserRepository;

    @Autowired
    private ReactiveJwtDecoder jwtDecoder;

    @Autowired
    private SimulatedNotificationMailbox notificationMailbox;

    @Test
    void passwordRecoveryUsesSimulatedDeliveryAndRevokesOldCredentials() {
        String adminToken = login("test-admin", "test-admin-password-123");
        String username = "recovery-" + UUID.randomUUID();
        webClient.post()
                .uri("/api/users")
                .header("Authorization", "Bearer " + adminToken)
                .bodyValue(Map.of("username", username, "password", "test-user-password-123"))
                .exchange()
                .expectStatus().isCreated();
        webClient.put()
                .uri("/api/users/{username}/profile", username)
                .header("Authorization", "Bearer " + adminToken)
                .bodyValue(Map.of(
                        "fullName", "Recovery Test",
                        "email", username + "@example.test",
                        "phone", "+15551234567"))
                .exchange()
                .expectStatus().isOk();

        webClient.post()
                .uri("/auth/password-recovery/request")
                .bodyValue(Map.of("username", username, "channel", "EMAIL"))
                .exchange()
                .expectStatus().isAccepted();

        var notification = notificationMailbox.latest().stream()
                .filter(item -> item.destination().equals(username + "@example.test"))
                .findFirst()
                .orElseThrow();
        assertThat(notification.code()).matches("[0-9]{6}");

        webClient.post()
                .uri("/auth/password-recovery/request")
                .bodyValue(Map.of("username", username, "channel", "SMS"))
                .exchange()
                .expectStatus().isAccepted();
        assertThat(notificationMailbox.latest())
                .anySatisfy(item -> {
                    assertThat(item.channel()).isEqualTo("SMS");
                    assertThat(item.destination()).isEqualTo("+15551234567");
                });

        webClient.post()
                .uri("/auth/password-recovery/confirm")
                .bodyValue(Map.of(
                        "username", username,
                        "channel", "EMAIL",
                        "code", notification.code(),
                        "newPassword", "recovered-password-789"))
                .exchange()
                .expectStatus().isNoContent();

        login(username, "recovered-password-789");
        webClient.post()
                .uri("/auth/login")
                .bodyValue(Map.of("username", username, "password", "test-user-password-123"))
                .exchange()
                .expectStatus().isUnauthorized();
        webClient.post()
                .uri("/auth/password-recovery/confirm")
                .bodyValue(Map.of(
                        "username", username,
                        "channel", "EMAIL",
                        "code", notification.code(),
                        "newPassword", "another-password-789"))
                .exchange()
                .expectStatus().isBadRequest();
    }

    @Test
    void assistantFaqIsPublicAndDoesNotNeedAnExternalModel() {
        webClient.get()
                .uri("/assistant.html")
                .exchange()
                .expectStatus().isOk()
                .expectHeader().contentTypeCompatibleWith(org.springframework.http.MediaType.TEXT_HTML)
                .expectBody(String.class)
                .value(html -> assertThat(html).contains("assistant.js", "assistant.css"));

        webClient.get()
                .uri("/assistant.js")
                .exchange()
                .expectStatus().isOk()
                .expectBody(String.class)
                .value(script -> assertThat(script).contains("fetch(\"/api/support/assistant\""));

        webClient.post()
                .uri("/api/support/assistant")
                .bodyValue(Map.of("message", "Como recuperar minha senha?"))
                .exchange()
                .expectStatus().isOk()
                .expectBody()
                .jsonPath("$.topic").isEqualTo("password-recovery")
                .jsonPath("$.humanSupportAvailable").isEqualTo(false);
    }

    @Test
    void idempotencyKeyPreventsDuplicatePaymentAndRejectsChangedPayload() {
        String token = login("test-admin", "test-admin-password-123");
        String key = "api-test-" + UUID.randomUUID();
        String payload = """
                {"payerId":"payer-1","payeeId":"payee-1","amount":42.50,"currency":"BRL"}
                """;

        Map<String, Object> first = createPayment(token, key, payload);
        Map<String, Object> duplicate = createPayment(token, key, payload);
        assertThat(duplicate.get("transactionId")).isEqualTo(first.get("transactionId"));

        webClient.post()
                .uri("/api/payments")
                .header("Authorization", "Bearer " + token)
                .header("Idempotency-Key", key)
                .header("Content-Type", "application/json")
                .bodyValue("""
                        {"payerId":"payer-1","payeeId":"payee-1","amount":99.00,"currency":"BRL"}
                        """)
                .exchange()
                .expectStatus().isEqualTo(409);
    }

    @Test
    void concurrentRequestsWithSameIdempotencyKeyDoNotCreateTwoPayments() {
        String token = login("test-admin", "test-admin-password-123");
        String key = "concurrent-test-" + UUID.randomUUID();
        String payload = """
                {"payerId":"payer-1","payeeId":"payee-1","amount":42.50,"currency":"BRL"}
                """;

        CompletableFuture<EntityExchangeResult<Map<String, Object>>> first =
                CompletableFuture.supplyAsync(() -> sendPayment(token, key, payload));
        CompletableFuture<EntityExchangeResult<Map<String, Object>>> second =
                CompletableFuture.supplyAsync(() -> sendPayment(token, key, payload));
        CompletableFuture.allOf(first, second).join();

        List<EntityExchangeResult<Map<String, Object>>> responses = List.of(first.join(), second.join());
        assertThat(responses).allSatisfy(response ->
                assertThat(response.getStatus().value()).isIn(201, 409));
        List<String> transactionIds = responses.stream()
                .filter(response -> response.getStatus().value() == 201)
                .map(response -> (String) response.getResponseBody().get("transactionId"))
                .toList();
        assertThat(transactionIds).isNotEmpty().doesNotHaveDuplicates();
    }

    @Test
    void paymentRequiresAnIdempotencyKey() {
        String token = login("test-admin", "test-admin-password-123");
        webClient.post()
                .uri("/api/payments")
                .header("Authorization", "Bearer " + token)
                .bodyValue(Map.of(
                        "payerId", "payer-1",
                        "payeeId", "payee-1",
                        "amount", 10.00,
                        "currency", "BRL"))
                .exchange()
                .expectStatus().isBadRequest();
    }

    @Test
    void metricsKeepDifferentCurrencyVolumesSeparate() {
        String token = login("test-admin", "test-admin-password-123");
        createPayment(token, "currency-test-" + UUID.randomUUID(), """
                {"payerId":"payer-1","payeeId":"payee-1","amount":18.25,"currency":"USD"}
                """);

        Map<String, Object> summary = webClient.get()
                .uri("/api/metrics/summary")
                .header("Authorization", "Bearer " + token)
                .exchange()
                .expectStatus().isOk()
                .expectBody(new ParameterizedTypeReference<Map<String, Object>>() {})
                .returnResult()
                .getResponseBody();
        assertThat(summary).containsKey("volumeByCurrency").doesNotContainKey("totalVolume");
        assertThat(((Map<?, ?>) summary.get("volumeByCurrency")).get("USD")).isNotNull();
    }

    @Test
    void adminCanCreateUsersButCreatedUserCannotCreateAdmins() {
        String adminToken = login("test-admin", "test-admin-password-123");
        String username = "user-" + UUID.randomUUID();
        webClient.post()
                .uri("/api/users")
                .header("Authorization", "Bearer " + adminToken)
                .bodyValue(Map.of("username", username, "password", "test-user-password-123"))
                .exchange()
                .expectStatus().isCreated();

        String userToken = login(username, "test-user-password-123");
        var savedUser = appUserRepository.findById(username).block();
        var decodedToken = jwtDecoder.decode(userToken).block();
        assertThat(savedUser).isNotNull();
        assertThat(savedUser.isEnabled()).isTrue();
        assertThat(decodedToken).isNotNull();
        assertThat(((Number) decodedToken.getClaim("token_version")).intValue())
                .isEqualTo(savedUser.getTokenVersion());

        webClient.post()
                .uri("/api/users")
                .header("Authorization", "Bearer " + userToken)
                .bodyValue(Map.of("username", "another-user", "password", "test-user-password-123"))
                .exchange()
                .expectStatus().isForbidden();
    }

    @Test
    void disablingUserInvalidatesItsPreviouslyIssuedToken() {
        String adminToken = login("test-admin", "test-admin-password-123");
        String username = "disable-" + UUID.randomUUID();
        webClient.post()
                .uri("/api/users")
                .header("Authorization", "Bearer " + adminToken)
                .bodyValue(Map.of("username", username, "password", "test-user-password-123"))
                .exchange()
                .expectStatus().isCreated();
        String userToken = login(username, "test-user-password-123");

        webClient.patch()
                .uri("/api/users/{username}/enabled", username)
                .header("Authorization", "Bearer " + adminToken)
                .bodyValue(Map.of("enabled", false))
                .exchange()
                .expectStatus().isOk();

        webClient.get()
                .uri("/api/metrics/summary")
                .header("Authorization", "Bearer " + userToken)
                .exchange()
                .expectStatus().isUnauthorized();
    }

    @Test
    void logoutRevokesIssuedJwt() {
        String token = login("test-admin", "test-admin-password-123");
        webClient.post()
                .uri("/auth/logout")
                .header("Authorization", "Bearer " + token)
                .exchange()
                .expectStatus().isNoContent();

        webClient.get()
                .uri("/api/metrics/summary")
                .header("Authorization", "Bearer " + token)
                .exchange()
                .expectStatus().isUnauthorized();
    }

    @Test
    void changingPasswordInvalidatesPreviousAccessTokens() {
        String adminToken = login("test-admin", "test-admin-password-123");
        String username = "rotate-" + UUID.randomUUID();
        webClient.post()
                .uri("/api/users")
                .header("Authorization", "Bearer " + adminToken)
                .bodyValue(Map.of("username", username, "password", "test-user-password-123"))
                .exchange()
                .expectStatus().isCreated();
        String oldToken = login(username, "test-user-password-123");

        webClient.put()
                .uri("/api/users/{username}/password", username)
                .header("Authorization", "Bearer " + adminToken)
                .bodyValue(Map.of("password", "new-test-user-password-456"))
                .exchange()
                .expectStatus().isOk();

        webClient.get()
                .uri("/api/metrics/summary")
                .header("Authorization", "Bearer " + oldToken)
                .exchange()
                .expectStatus().isUnauthorized();
        login(username, "new-test-user-password-456");
    }

    @Test
    void repeatedInvalidLoginsAreRateLimited() {
        String adminToken = login("test-admin", "test-admin-password-123");
        String username = "limited-" + UUID.randomUUID();
        webClient.post()
                .uri("/api/users")
                .header("Authorization", "Bearer " + adminToken)
                .bodyValue(Map.of("username", username, "password", "test-user-password-123"))
                .exchange()
                .expectStatus().isCreated();

        for (int attempt = 0; attempt < 5; attempt++) {
            webClient.post()
                    .uri("/auth/login")
                    .bodyValue(Map.of("username", username, "password", "wrong-password"))
                    .exchange()
                    .expectStatus().isUnauthorized();
        }

        webClient.post()
                .uri("/auth/login")
                .bodyValue(Map.of("username", username, "password", "test-user-password-123"))
                .exchange()
                .expectStatus().isEqualTo(429)
                .expectHeader().exists("Retry-After");
    }

    private String login(String username, String password) {
        Map<String, String> response = webClient.post()
                .uri("/auth/login")
                .bodyValue(Map.of("username", username, "password", password))
                .exchange()
                .expectStatus().isOk()
                .expectBody(STRING_MAP)
                .returnResult()
                .getResponseBody();
        assertThat(response).isNotNull();
        return response.get("access_token");
    }

    private Map<String, Object> createPayment(String token, String key, String payload) {
        return webClient.post()
                .uri("/api/payments")
                .header("Authorization", "Bearer " + token)
                .header("Idempotency-Key", key)
                .header("Content-Type", "application/json")
                .bodyValue(payload)
                .exchange()
                .expectStatus().isCreated()
                .expectBody(new ParameterizedTypeReference<Map<String, Object>>() {})
                .returnResult()
                .getResponseBody();
    }

    private EntityExchangeResult<Map<String, Object>> sendPayment(
            String token, String key, String payload) {
        return webClient.post()
                .uri("/api/payments")
                .header("Authorization", "Bearer " + token)
                .header("Idempotency-Key", key)
                .header("Content-Type", "application/json")
                .bodyValue(payload)
                .exchange()
                .expectBody(new ParameterizedTypeReference<Map<String, Object>>() {})
                .returnResult();
    }
}
