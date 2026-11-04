package com.paygateway.controller;

import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import reactor.core.publisher.Mono;

import java.util.Locale;

@RestController
@RequestMapping("/api/support/assistant")
public class AssistantController {

    public record AssistantRequest(@NotBlank @Size(max = 500) String message) {}
    public record AssistantResponse(String topic, String answer, boolean humanSupportAvailable) {}

    @PostMapping
    public Mono<AssistantResponse> answer(@Valid @RequestBody AssistantRequest request) {
        String message = request.message().toLowerCase(Locale.ROOT);
        AssistantResponse response;
        if (containsAny(message, "senha", "recuperar", "recuperação", "código")) {
            response = new AssistantResponse("password-recovery",
                    "Use POST /auth/password-recovery/request para receber um código "
                            + "e POST /auth/password-recovery/confirm para definir uma nova senha.",
                    false);
        } else if (containsAny(message, "idempotência", "idempotencia", "duplicado", "repetir")) {
            response = new AssistantResponse("idempotency",
                    "Envie a mesma Idempotency-Key ao repetir uma solicitação de pagamento. "
                            + "A chave não pode ser reutilizada com dados diferentes.",
                    false);
        } else if (containsAny(message, "pagamento", "transação", "transacao", "status")) {
            response = new AssistantResponse("payments",
                    "A API de pagamentos exige autenticação e uma Idempotency-Key. "
                            + "Este ambiente demonstrativo usa um gateway simulado.",
                    false);
        } else if (containsAny(message, "email", "e-mail", "sms", "telefone", "contato")) {
            response = new AssistantResponse("contact",
                    "Os contatos da conta são mantidos no cadastro privado e atualizados por um administrador.",
                    false);
        } else {
            response = new AssistantResponse("general",
                    "Posso orientar sobre pagamentos, idempotência e recuperação de senha. "
                            + "Não envie senhas, códigos ou dados de cartão nesta conversa.",
                    false);
        }
        return Mono.just(response);
    }

    private boolean containsAny(String message, String... terms) {
        for (String term : terms) {
            if (message.contains(term)) {
                return true;
            }
        }
        return false;
    }
}
