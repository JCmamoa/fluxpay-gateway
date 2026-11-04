package com.paygateway.exception;

import io.github.resilience4j.circuitbreaker.CallNotPermittedException;
import com.paygateway.service.IdempotencyConflictException;
import com.paygateway.service.InvalidRecoveryCodeException;
import com.paygateway.service.PaymentInProgressException;
import com.paygateway.service.UserAlreadyExistsException;
import com.paygateway.service.UserNotFoundException;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;
import org.springframework.web.bind.support.WebExchangeBindException;
import org.springframework.web.server.ServerWebInputException;
import reactor.core.publisher.Mono;

import java.util.Map;

@RestControllerAdvice
public class GlobalExceptionHandler {

    @ExceptionHandler(WebExchangeBindException.class)
    public Mono<ResponseEntity<Map<String, String>>> handleValidation(WebExchangeBindException ex) {
        String details = ex.getFieldErrors().stream()
                .map(error -> error.getField() + ": " + error.getDefaultMessage())
                .reduce((first, second) -> first + "; " + second)
                .orElse("Dados inválidos");
        return Mono.just(ResponseEntity.status(HttpStatus.BAD_REQUEST)
                .body(Map.of("error", "Validação falhou", "message", details)));
    }

    @ExceptionHandler(ServerWebInputException.class)
    public Mono<ResponseEntity<Map<String, String>>> handleBadInput(ServerWebInputException ex) {
        return Mono.just(ResponseEntity.status(HttpStatus.BAD_REQUEST)
                .body(Map.of("error", "Requisição inválida", "message", "Corpo da requisição malformado")));
    }

    @ExceptionHandler(IllegalArgumentException.class)
    public Mono<ResponseEntity<Map<String, String>>> handleIllegalArgument(IllegalArgumentException ex) {
        return Mono.just(ResponseEntity.status(HttpStatus.BAD_REQUEST)
                .body(Map.of("error", "Requisição inválida", "message", String.valueOf(ex.getMessage()))));
    }

    @ExceptionHandler(InvalidRecoveryCodeException.class)
    public Mono<ResponseEntity<Map<String, String>>> handleInvalidRecoveryCode(
            InvalidRecoveryCodeException ex) {
        return Mono.just(ResponseEntity.status(HttpStatus.BAD_REQUEST)
                .body(Map.of("error", "Recuperação inválida", "message", ex.getMessage())));
    }

    @ExceptionHandler({IdempotencyConflictException.class, PaymentInProgressException.class,
            UserAlreadyExistsException.class})
    public Mono<ResponseEntity<Map<String, String>>> handleConflict(RuntimeException ex) {
        return Mono.just(ResponseEntity.status(HttpStatus.CONFLICT)
                .body(Map.of("error", "Conflito", "message", ex.getMessage())));
    }

    @ExceptionHandler(UserNotFoundException.class)
    public Mono<ResponseEntity<Map<String, String>>> handleUserNotFound(UserNotFoundException ex) {
        return Mono.just(ResponseEntity.status(HttpStatus.NOT_FOUND)
                .body(Map.of("error", "Não encontrado", "message", ex.getMessage())));
    }

    @ExceptionHandler(CallNotPermittedException.class)
    public Mono<ResponseEntity<Map<String, String>>> handleCircuitOpen(CallNotPermittedException ex) {
        return Mono.just(ResponseEntity.status(HttpStatus.SERVICE_UNAVAILABLE)
                .body(Map.of("error", "Serviço temporariamente indisponível",
                        "message", "Circuit breaker aberto, tente novamente em instantes")));
    }

    @ExceptionHandler(Exception.class)
    public Mono<ResponseEntity<Map<String, String>>> handleGeneric(Exception ex) {
        return Mono.just(ResponseEntity.status(HttpStatus.INTERNAL_SERVER_ERROR)
                .body(Map.of("error", "Erro interno", "message", "Ocorreu um erro inesperado")));
    }
}
