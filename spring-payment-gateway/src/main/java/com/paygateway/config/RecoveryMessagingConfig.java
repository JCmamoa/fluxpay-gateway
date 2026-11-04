package com.paygateway.config;

import org.springframework.amqp.core.Queue;
import org.springframework.amqp.support.converter.Jackson2JsonMessageConverter;
import org.springframework.amqp.rabbit.annotation.EnableRabbit;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.context.annotation.Profile;

@Configuration
@EnableRabbit
@Profile("dev")
public class RecoveryMessagingConfig {

    @Bean
    public Queue recoveryNotificationsQueue(
            @Value("${app.messaging.recovery-queue}") String queueName) {
        return new Queue(queueName, true);
    }

    @Bean
    public Jackson2JsonMessageConverter recoveryNotificationMessageConverter() {
        return new Jackson2JsonMessageConverter();
    }
}
