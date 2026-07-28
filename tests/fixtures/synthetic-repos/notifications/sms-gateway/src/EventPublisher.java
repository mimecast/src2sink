package com.example.sms;

import org.springframework.kafka.core.KafkaTemplate;

public class EventPublisher {
    private final KafkaTemplate<String, String> kafkaTemplate;

    public EventPublisher(KafkaTemplate<String, String> kafkaTemplate) {
        this.kafkaTemplate = kafkaTemplate;
    }

    public void publishSms(String phoneNumber, String body) {
        kafkaTemplate.send("customer-phone-updates", phoneNumber + ":" + body);
    }
}
