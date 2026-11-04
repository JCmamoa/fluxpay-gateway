package com.paygateway.profile;

import com.paygateway.domain.AppUser;
import com.paygateway.dto.UpdateUserProfileRequest;
import com.paygateway.repository.AppUserRepository;
import com.paygateway.service.UserNotFoundException;
import org.springframework.stereotype.Service;
import reactor.core.publisher.Mono;
import reactor.core.scheduler.Schedulers;

@Service
public class UserProfileService {

    private final PrivateUserDataStore store;
    private final AppUserRepository users;

    public UserProfileService(PrivateUserDataStore store, AppUserRepository users) {
        this.store = store;
        this.users = users;
    }

    public Mono<Void> update(String username, UpdateUserProfileRequest request) {
        if ((request.email() == null || request.email().isBlank())
                && (request.phone() == null || request.phone().isBlank())) {
            return Mono.<Void>error(new IllegalArgumentException(
                    "Informe ao menos um contato para recuperação da senha"));
        }
        return users.findById(username)
                .switchIfEmpty(Mono.<AppUser>error(new UserNotFoundException(username)))
                .then(Mono.<Void>fromRunnable(() -> store.saveProfile(new UserProfile(
                                username, request.fullName(), normalize(request.email()),
                                normalize(request.phone()))))
                        .subscribeOn(Schedulers.boundedElastic()));
    }

    private String normalize(String value) {
        return value == null || value.isBlank() ? null : value.trim();
    }
}
