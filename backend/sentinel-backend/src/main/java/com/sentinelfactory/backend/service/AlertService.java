package com.sentinelfactory.backend.service;

import com.sentinelfactory.backend.entity.Alert;
import com.sentinelfactory.backend.repository.AlertRepository;
import org.springframework.stereotype.Service;

import java.util.List;

@Service
public class AlertService {

    private final AlertRepository alertRepository;

    public AlertService(AlertRepository alertRepository) {
        this.alertRepository = alertRepository;
    }

    public Alert saveAlert(Alert alert) {

        System.out.println("===== ALERT SERVICE =====");
        System.out.println("Saving alert...");

        Alert savedAlert = alertRepository.save(alert);

        System.out.println("Alert saved successfully!");
        System.out.println("Alert ID   : " + savedAlert.getId());
        System.out.println("Alert Type : " + savedAlert.getAlertType());
        System.out.println("Severity   : " + savedAlert.getSeverity());
        System.out.println("Camera ID  : " + savedAlert.getCameraId());
        System.out.println("========================");

        return savedAlert;
    }

    public List<Alert> getAllAlerts() {
        return alertRepository.findAll();
    }

    public Alert getAlertById(Long id) {
        return alertRepository.findById(id).orElse(null);
    }
}