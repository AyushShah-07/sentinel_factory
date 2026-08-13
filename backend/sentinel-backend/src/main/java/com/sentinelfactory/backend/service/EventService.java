package com.sentinelfactory.backend.service;

import com.sentinelfactory.backend.entity.Alert;
import com.sentinelfactory.backend.entity.Event;
import com.sentinelfactory.backend.repository.EventRepository;
import org.springframework.stereotype.Service;

import java.util.List;

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

    public Event saveEvent(Event event) {

        System.out.println("===== EVENT SERVICE =====");
        System.out.println("Saving event...");

        Event savedEvent = eventRepository.save(event);

        System.out.println("Event saved successfully!");
        System.out.println("Event ID    : " + savedEvent.getId());
        System.out.println("Event Type  : " + savedEvent.getEventType());
        System.out.println("Camera ID   : " + savedEvent.getCameraId());
        System.out.println("Risk Score  : " + savedEvent.getRiskScore());

        // Check whether this event is dangerous
        if ("DANGER_ZONE".equalsIgnoreCase(savedEvent.getEventType())) {

            System.out.println("===== DANGER DETECTED =====");

            Alert alert = new Alert();

            alert.setEventId(savedEvent.getEventId());
            alert.setAlertType("DANGER_ZONE");
            alert.setSeverity("HIGH");
            alert.setMessage("Employee entered danger zone");
            alert.setZone(savedEvent.getZone());
            alert.setRiskScore(savedEvent.getRiskScore());
            alert.setCameraId(savedEvent.getCameraId());
            alert.setStatus("ACTIVE");

            alertService.saveAlert(alert);

            System.out.println("Alert generated successfully!");
            System.out.println("===========================");
        }

        System.out.println("=========================");

        return savedEvent;
    }

    public List<Event> getAllEvents() {
        return eventRepository.findAll();
    }

    public Event getEventById(Long id) {
        return eventRepository.findById(id).orElse(null);
    }
}