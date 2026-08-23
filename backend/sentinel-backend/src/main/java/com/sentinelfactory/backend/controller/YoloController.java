package com.sentinelfactory.backend.controller;

import java.util.ArrayList;
import java.util.List;

import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import com.sentinelfactory.backend.dto.IncidentRequest;
import com.sentinelfactory.backend.dto.PersonDetectionRequest;
import com.sentinelfactory.backend.entity.Alert;
import com.sentinelfactory.backend.entity.Event;
import com.sentinelfactory.backend.service.AlertService;
import com.sentinelfactory.backend.service.EventService;

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

    // =========================================================
    // PERSON DETECTION - UNCHANGED
    // =========================================================

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

            event.setTimestamp(
                    request.getTimestamp()
            );

            event.setCameraId(
                    cameraId
            );

            event.setEventType(
                    request.getEvent()
            );

            event.setTrackId(
                    request.getPersonId()
            );

            event.setEmployeeId(null);
            event.setZone(null);
            event.setMachineState(null);
            event.setRiskScore(null);

            Event savedEvent =
                    eventService.saveEvent(event);

            savedEvents.add(
                    savedEvent
            );
        }

        return ResponseEntity.ok(
                savedEvents
        );
    }

    // =========================================================
    // DANGER / WARNING INCIDENTS - UPDATED
    // =========================================================

    @PostMapping("/incidents")
    public ResponseEntity<List<Alert>> receiveIncidents(
            @RequestParam String cameraId,
            @RequestBody List<IncidentRequest> incidents) {

        List<Alert> savedAlerts = new ArrayList<>();

        for (IncidentRequest request : incidents) {

            Alert alert = new Alert();

            // Generated event ID
            alert.setEventId(
                    "YOLO-" + System.currentTimeMillis()
            );

            // Camera ID from request param
            alert.setCameraId(
                    cameraId
            );

            // Status - always ACTIVE
            alert.setStatus(
                    "ACTIVE"
            );

            // =========================================================
            // FIELDS FROM YOUR JSON - 6 fields only
            // =========================================================

            // 1. timestamp from JSON
            alert.setTimestamp(
                    request.getTimestamp()
            );

            // 2. person_id from JSON
            alert.setPersonId(
                    request.getPersonId()
            );

            // 3. zone_name from JSON (NEW - fixes Unknown zone)
            alert.setZoneName(
                    request.getZoneName()
            );

            // 4. incident_type from JSON
            alert.setIncidentType(
                    request.getIncidentType()
            );

            // 5. picture from JSON (picture_url goes here) - UNCHANGED
            alert.setPicture(
                    request.getPicture()
            );

            // 6. dwell_seconds from JSON
            alert.setDwellSeconds(
                    request.getDwellSeconds()
            );

            // Save alert
            Alert savedAlert =
                    alertService.saveAlert(alert);

            savedAlerts.add(
                    savedAlert
            );
        }

        return ResponseEntity.ok(
                savedAlerts
        );
    }
}