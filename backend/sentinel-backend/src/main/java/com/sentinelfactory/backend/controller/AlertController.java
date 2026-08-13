package com.sentinelfactory.backend.controller;

import com.sentinelfactory.backend.entity.Alert;
import com.sentinelfactory.backend.service.AlertService;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.List;

@RestController
@RequestMapping("/api/alerts")
public class AlertController {

    private final AlertService alertService;

    public AlertController(AlertService alertService) {
        this.alertService = alertService;
    }

    @PostMapping
    public ResponseEntity<Alert> createAlert(@RequestBody Alert alert) {

        Alert savedAlert = alertService.saveAlert(alert);

        return ResponseEntity.ok(savedAlert);
    }

    @GetMapping
    public ResponseEntity<List<Alert>> getAllAlerts() {

        return ResponseEntity.ok(alertService.getAllAlerts());
    }

    @GetMapping("/{id}")
    public ResponseEntity<Alert> getAlertById(@PathVariable Long id) {

        Alert alert = alertService.getAlertById(id);

        if (alert == null) {
            return ResponseEntity.notFound().build();
        }

        return ResponseEntity.ok(alert);
    }
}