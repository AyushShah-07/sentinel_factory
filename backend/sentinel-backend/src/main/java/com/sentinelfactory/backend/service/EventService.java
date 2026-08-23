package com.sentinelfactory.backend.service;

import java.util.List;

import org.springframework.stereotype.Service;

import com.sentinelfactory.backend.entity.Alert;
import com.sentinelfactory.backend.entity.Event;
import com.sentinelfactory.backend.repository.EventRepository;

// Handles event database operations and creates alerts for danger events
@Service
public class EventService {

    private final EventRepository eventRepository;
    private final AlertService alertService;

    public EventService(
            EventRepository eventRepository,
            AlertService alertService) {

        this.eventRepository = eventRepository;
        this.alertService = alertService;
    }

    // Saves event and generates alert if danger zone detected
    public Event saveEvent(Event event) {

        System.out.println("===== EVENT SERVICE =====");
        System.out.println("Saving event...");

        Event savedEvent = eventRepository.save(event);

        System.out.println("Event saved successfully!");
        System.out.println("Event ID    : " + savedEvent.getId());
        System.out.println("Event Type  : " + savedEvent.getEventType());
        System.out.println("Camera ID   : " + savedEvent.getCameraId());
        System.out.println("Risk Score  : " + savedEvent.getRiskScore());

        // If event is danger zone, create an alert
        if ("DANGER_ZONE".equalsIgnoreCase(savedEvent.getEventType())) {

            System.out.println("===== DANGER DETECTED =====");

            Alert alert = new Alert();

            // Copy event ID
            alert.setEventId(savedEvent.getEventId());

            // Set incident type (from event type)
            alert.setIncidentType("DANGER");  // ✅ Changed from setAlertType

            // Set zone name from event zone (NEW field)
            alert.setZoneName(savedEvent.getZone());  // ✅ Changed from setZone

            // Camera ID from event
            alert.setCameraId(savedEvent.getCameraId());

            // Status always ACTIVE
            alert.setStatus("ACTIVE");

            // Note: personId, picture, dwellSeconds, timestamp are not set from event
            // They will be null/default

            alertService.saveAlert(alert);

            System.out.println("Alert generated successfully!");
            System.out.println("===========================");
        }

        System.out.println("=========================");

        return savedEvent;
    }

    // Gets all events
    public List<Event> getAllEvents() {
        return eventRepository.findAll();
    }

    // Gets single event by ID
    public Event getEventById(Long id) {
        return eventRepository.findById(id).orElse(null);
    }
}