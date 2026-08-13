package com.sentinelfactory.backend.controller;

import com.sentinelfactory.backend.dto.IncidentRequest;
import com.sentinelfactory.backend.dto.PersonDetectionRequest;
import com.sentinelfactory.backend.entity.Alert;
import com.sentinelfactory.backend.entity.Event;
import com.sentinelfactory.backend.service.AlertService;
import com.sentinelfactory.backend.service.EventService;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.ArrayList;
import java.util.List;

@RestController
@RequestMapping("/api/yolo")
public class YoloController {

    private final EventService eventService;
    private final AlertService alertService;

    public YoloController(
            EventService eventService,
            AlertService alertService) {

        this.eventService = eventService;
        this.alertService = alertService;
    }

    // ==============================
    // PERSON DETECTION
    // ==============================

    @PostMapping("/detections")
    public ResponseEntity<List<Event>> receiveDetections(
            @RequestParam String cameraId,
            @RequestBody List<PersonDetectionRequest> detections) {

        List<Event> savedEvents = new ArrayList<>();

        for (PersonDetectionRequest request : detections) {

            Event event = new Event();

            event.setEventId(
                    "YOLO-" + System.currentTimeMillis()
            );

            event.setTimestamp(request.getTimestamp());

            event.setCameraId(cameraId);

            event.setEventType(request.getEvent());

            // P1, P2, P3
            event.setTrackId(request.getPersonId());

            event.setEmployeeId(null);

            event.setZone(null);

            event.setMachineState(null);

            // Person JSON doesn't contain confidence
            event.setRiskScore(null);

            Event savedEvent = eventService.saveEvent(event);

            savedEvents.add(savedEvent);
        }

        return ResponseEntity.ok(savedEvents);
    }


    // ==============================
    // SAFETY INCIDENT / ALERT
    // ==============================

    @PostMapping("/incidents")
    public ResponseEntity<List<Alert>> receiveIncidents(
            @RequestParam String cameraId,
            @RequestBody List<IncidentRequest> incidents) {

        List<Alert> savedAlerts = new ArrayList<>();

        for (IncidentRequest request : incidents) {

            Alert alert = new Alert();

            alert.setEventId(
                    "YOLO-" + System.currentTimeMillis()
            );

            alert.setAlertType(
                    request.getIncidentType()
            );

            alert.setSeverity(
                    request.getIncidentType()
            );

            alert.setMessage(
                    request.getIncidentName()
            );

            if (request.getHandZone() != null) {
                alert.setZone(request.getHandZone());
            } else {
                alert.setZone(request.getBodyZone());
            }

            alert.setCameraId(cameraId);

            alert.setStatus("ACTIVE");

            // Incident JSON doesn't contain risk score
            alert.setRiskScore(null);

            Alert savedAlert = alertService.saveAlert(alert);

            savedAlerts.add(savedAlert);
        }

        return ResponseEntity.ok(savedAlerts);
    }
}