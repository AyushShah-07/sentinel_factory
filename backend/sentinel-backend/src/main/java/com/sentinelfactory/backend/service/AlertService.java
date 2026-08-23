package com.sentinelfactory.backend.service;

import java.util.List;

import org.springframework.stereotype.Service;

import com.sentinelfactory.backend.entity.Alert;
import com.sentinelfactory.backend.repository.AlertRepository;

// This class handles alert database operations
@Service
public class AlertService {

    private final AlertRepository alertRepository;

    public AlertService(AlertRepository alertRepository) {
        this.alertRepository = alertRepository;
    }

    // Saves alert to database and logs details
    public Alert saveAlert(Alert alert) {

        System.out.println("===== ALERT SERVICE =====");
        System.out.println("Saving alert...");

        Alert savedAlert = alertRepository.save(alert);

        System.out.println("Alert saved successfully!");
        System.out.println("Alert ID      : " + savedAlert.getId());

        // From JSON: incident_type
        System.out.println("Incident Type : " + savedAlert.getIncidentType());

        // Severity same as incident type
        System.out.println("Severity      : " + savedAlert.getIncidentType());

        // From request parameter
        System.out.println("Camera ID     : " + savedAlert.getCameraId());

        // From JSON: zone_name
        System.out.println("Zone Name     : " + savedAlert.getZoneName());

        // From JSON: person_id
        System.out.println("Person ID     : " + savedAlert.getPersonId());

        // From JSON: dwell_seconds
        System.out.println("Dwell Seconds : " + savedAlert.getDwellSeconds());

        System.out.println("========================");

        return savedAlert;
    }

    // Gets all alerts from database
    public List<Alert> getAllAlerts() {
        return alertRepository.findAll();
    }

    // Gets single alert by ID
    public Alert getAlertById(Long id) {
        return alertRepository.findById(id).orElse(null);
    }
}