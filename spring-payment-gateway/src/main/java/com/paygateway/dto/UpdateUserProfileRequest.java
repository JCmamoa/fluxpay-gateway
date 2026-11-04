package com.paygateway.dto;

import jakarta.validation.constraints.Email;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.Size;

public record UpdateUserProfileRequest(
        @NotBlank @Size(max = 120) String fullName,
        @Email @Size(max = 254) String email,
        @Pattern(regexp = "^\\+?[0-9 ()-]{8,24}$") String phone
) {}
