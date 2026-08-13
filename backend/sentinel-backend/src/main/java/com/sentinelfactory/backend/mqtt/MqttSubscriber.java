package com.sentinelfactory.backend.mqtt;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.sentinelfactory.backend.dto.DetectionRequest;
import com.sentinelfactory.backend.service.DetectionService;
import org.springframework.integration.annotation.ServiceActivator;
import org.springframework.messaging.Message;
import org.springframework.stereotype.Component;

@Component
public class MqttSubscriber {

    private final ObjectMapper objectMapper;
    private final DetectionService detectionService;

    public MqttSubscriber(
            ObjectMapper objectMapper,
            DetectionService detectionService) {

        this.objectMapper = objectMapper;
        this.detectionService = detectionService;
    }

    @ServiceActivator(inputChannel = "mqttInputChannel")
    public void receiveMessage(Message<?> message) {

        try {

            // Get MQTT payload
            String payload = message.getPayload().toString();

            System.out.println("===== MQTT MESSAGE RECEIVED =====");
            System.out.println("Payload: " + payload);

            // Convert JSON into DetectionRequest
            DetectionRequest detectionRequest =
                    objectMapper.readValue(payload, DetectionRequest.class);

            // Print detection information
            System.out.println("Event        : " + detectionRequest.getEvent());
            System.out.println("Camera ID    : " + detectionRequest.getCameraId());
            System.out.println("Confidence   : " + detectionRequest.getConfidence());
            System.out.println("Timestamp    : " + detectionRequest.getTimestamp());

            System.out.println("================================");

            // Send original JSON payload to DetectionService
            detectionService.processDetection(payload);

        } catch (Exception e) {

            System.err.println("Error processing MQTT message:");
            e.printStackTrace();
        }
    }
}