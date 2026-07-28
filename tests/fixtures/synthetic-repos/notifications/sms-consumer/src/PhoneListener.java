package com.example.sms;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.kafka.annotation.KafkaListener;

public class PhoneListener {
    private static final Logger logger = LoggerFactory.getLogger(PhoneListener.class);

    @KafkaListener(topics = "customer-phone-updates")
    public void onPhoneUpdate(String payload) {
        String phoneNumber = payload.split(":")[0];
        logger.info("Delivering SMS to phoneNumber={}", phoneNumber);
    }
}
