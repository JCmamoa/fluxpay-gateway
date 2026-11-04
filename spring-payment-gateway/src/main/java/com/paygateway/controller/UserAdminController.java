package com.paygateway.controller;

import com.paygateway.domain.AppUser;
import com.paygateway.dto.CreateUserRequest;
import com.paygateway.dto.UpdateUserProfileRequest;
import com.paygateway.profile.UserProfileService;
import com.paygateway.service.UserProvisioningService;
import jakarta.validation.Valid;
import org.springframework.http.HttpStatus;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.PatchMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestController;
import reactor.core.publisher.Mono;

import java.util.List;
import jakarta.validation.constraints.NotNull;

@RestController
@RequestMapping("/api/users")
public class UserAdminController {

    private final UserProvisioningService userProvisioningService;
    private final UserProfileService userProfileService;

    public UserAdminController(
            UserProvisioningService userProvisioningService,
            UserProfileService userProfileService) {
        this.userProvisioningService = userProvisioningService;
        this.userProfileService = userProfileService;
    }

    public record UserResponse(String username, List<String> scopes, boolean enabled) {}
    public record EnabledRequest(@NotNull Boolean enabled) {}
    public record PasswordRequest(
            @jakarta.validation.constraints.NotBlank
            @jakarta.validation.constraints.Size(min = 12, max = 72)
            String password) {}
    public record ProfileUpdatedResponse(String message) {}

    @PostMapping
    @ResponseStatus(HttpStatus.CREATED)
    public Mono<UserResponse> createUser(@Valid @RequestBody CreateUserRequest request) {
        return userProvisioningService.createUser(request)
                .map(this::toResponse);
    }

    @PatchMapping("/{username}/enabled")
    public Mono<UserResponse> setEnabled(
            @PathVariable String username,
            @Valid @RequestBody EnabledRequest request,
            @AuthenticationPrincipal Jwt principal) {
        if (!request.enabled() && username.equals(principal.getSubject())) {
            return Mono.error(new IllegalArgumentException("Não é permitido desativar a própria conta"));
        }
        return userProvisioningService.setEnabled(username, request.enabled()).map(this::toResponse);
    }

    @PutMapping("/{username}/password")
    public Mono<UserResponse> changePassword(
            @PathVariable String username,
            @Valid @RequestBody PasswordRequest request) {
        return userProvisioningService.changePassword(username, request.password()).map(this::toResponse);
    }

    @org.springframework.web.bind.annotation.PutMapping("/{username}/profile")
    public Mono<ProfileUpdatedResponse> updateProfile(
            @PathVariable String username,
            @Valid @RequestBody UpdateUserProfileRequest request) {
        return userProfileService.update(username, request)
                .thenReturn(new ProfileUpdatedResponse("Cadastro atualizado"));
    }

    private UserResponse toResponse(AppUser user) {
        return new UserResponse(user.getUsername(), user.getScopeList(), user.isEnabled());
    }
}
