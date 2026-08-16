package com.sentinelfactory.backend.controller;

import com.sentinelfactory.backend.entity.Alert;
import com.sentinelfactory.backend.service.AlertService;
import org.springframework.core.io.FileSystemResource;
import org.springframework.core.io.Resource;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.io.File;
import java.util.List;

@RestController
@RequestMapping("/api/alerts")
public class AlertController {

    private final AlertService alertService;

    public AlertController(AlertService alertService) {
        this.alertService = alertService;
    }

    @PostMapping
    public ResponseEntity<Alert> createAlert(
            @RequestBody Alert alert) {

        Alert savedAlert =
                alertService.saveAlert(alert);

        return ResponseEntity.ok(
                savedAlert
        );
    }

    @GetMapping
    public ResponseEntity<List<Alert>> getAllAlerts() {

        return ResponseEntity.ok(
                alertService.getAllAlerts()
        );
    }

    @GetMapping("/{id}")
    public ResponseEntity<Alert> getAlertById(
            @PathVariable Long id) {

        Alert alert =
                alertService.getAlertById(id);

        if (alert == null) {
            return ResponseEntity
                    .notFound()
                    .build();
        }

        return ResponseEntity.ok(
                alert
        );
    }

    // =========================================================
    // INCIDENT IMAGE
    // =========================================================

    @GetMapping("/{id}/image")
    public ResponseEntity<Resource> getAlertImage(
            @PathVariable Long id) {

        Alert alert =
                alertService.getAlertById(id);

        if (alert == null) {

            return ResponseEntity
                    .notFound()
                    .build();
        }

        String picturePath =
                alert.getPicture();

        if (picturePath == null ||
                picturePath.isBlank()) {

            return ResponseEntity
                    .notFound()
                    .build();
        }

        File imageFile =
                new File(picturePath);

        if (!imageFile.exists() ||
                !imageFile.isFile()) {

            return ResponseEntity
                    .notFound()
                    .build();
        }

        Resource resource =
                new FileSystemResource(
                        imageFile
                );

        return ResponseEntity.ok()
                .contentType(
                        MediaType.IMAGE_JPEG
                )
                .header(
                        HttpHeaders.CACHE_CONTROL,
                        "no-cache"
                )
                .body(resource);
    }
}