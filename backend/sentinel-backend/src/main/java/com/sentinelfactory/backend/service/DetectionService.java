package com.sentinelfactory.backend.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.sentinelfactory.backend.dto.DetectionRequest;
import com.sentinelfactory.backend.entity.Event;
import org.springframework.stereotype.Service;

@Service
public class DetectionService {

    private final ObjectMapper objectMapper;
    private final EventService eventService;

    public DetectionService(
            ObjectMapper objectMapper,
            EventService eventService) {

        this.objectMapper = objectMapper;
        this.eventService = eventService;
    }

    public void processDetection(String payload) {

        try {

            System.out.println("===== DETECTION SERVICE =====");
            System.out.println("Processing detection: " + payload);

            // Convert MQTT JSON into DetectionRequest
            DetectionRequest request =
                    objectMapper.readValue(payload, DetectionRequest.class);

            // Create Event object
            Event event = new Event();

            event.setEventId("EVT-" + System.currentTimeMillis());
            event.setTimestamp(request.getTimestamp());
            event.setCameraId(request.getCameraId());
            event.setEventType(request.getEvent());

            event.setRiskScore(request.getConfidence());

            // These values are not coming from your current MQTT message,
            // so we leave them empty for now.
            event.setEmployeeId(null);
            event.setTrackId(null);
            event.setZone(null);
            event.setMachineState(null);

            // Save event into MySQL
            eventService.saveEvent(event);

            System.out.println("Detection converted to Event successfully.");
            System.out.println("==============================");

        } catch (Exception e) {

            System.err.println("Error processing detection:");
            e.printStackTrace();
        }
    }
}