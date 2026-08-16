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
// PERSON DETECTION
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
    // DANGER / WARNING INCIDENTS
    // =========================================================

    @PostMapping("/incidents")
    public ResponseEntity<List<Alert>> receiveIncidents(
            @RequestParam String cameraId,
            @RequestBody List<IncidentRequest> incidents) {

        List<Alert> savedAlerts =
                new ArrayList<>();

        for (IncidentRequest request : incidents) {

            Alert alert = new Alert();

            alert.setEventId(
                    "YOLO-" + System.currentTimeMillis()
            );

            // -------------------------------------------------
            // Main alert fields
            // -------------------------------------------------

            alert.setAlertType(
                    request.getIncidentType()
            );

            alert.setSeverity(
                    request.getIncidentType()
            );

            alert.setMessage(
                    request.getIncidentName()
            );

            alert.setCameraId(
                    cameraId
            );

            alert.setStatus(
                    "ACTIVE"
            );

            // -------------------------------------------------
            // Incident details
            // -------------------------------------------------

            alert.setTimestamp(
                    request.getTimestamp()
            );

            alert.setPersonId(
                    request.getPersonId()
            );

            alert.setIncidentName(
                    request.getIncidentName()
            );

            alert.setSource(
                    request.getSource()
            );

            alert.setBodyZone(
                    request.getBodyZone()
            );

            alert.setBodyZoneId(
                    request.getBodyZoneId()
            );

            alert.setHandZone(
                    request.getHandZone()
            );

            alert.setHandZoneId(
                    request.getHandZoneId()
            );

            alert.setPicture(
                    request.getPicture()
            );

            alert.setDwellSeconds(
                    request.getDwellSeconds()
            );

            // -------------------------------------------------
            // Zone shown in existing alert table
            // -------------------------------------------------

            if (request.getBodyZoneId() != null) {

                alert.setZone(
                        request.getBodyZoneId()
                );

            } else if (request.getHandZoneId() != null) {

                alert.setZone(
                        request.getHandZoneId()
                );

            } else {

                alert.setZone(
                        request.getBodyZone()
                );
            }

            // -------------------------------------------------
            // Risk is currently not included in the Python
            // incident record.
            // -------------------------------------------------

            alert.setRiskScore(
                    null
            );

            Alert savedAlert =
                    alertService.saveAlert(
                            alert
                    );

            savedAlerts.add(
                    savedAlert
            );
        }

        return ResponseEntity.ok(
                savedAlerts
        );
    }
}