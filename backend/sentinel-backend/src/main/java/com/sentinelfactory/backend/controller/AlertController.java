/**
 * AlertController.java
 * 
 * PURPOSE: REST Controller for handling alert/incident HTTP requests.
 * 
 * ENDPOINTS:
 * - POST   /api/alerts       : Create new alert from Vision system
 * - GET    /api/alerts       : Get all alerts for frontend dashboard
 * - GET    /api/alerts/{id}  : Get specific alert by ID
 * - GET    /api/alerts/{id}/image : Serve incident image file
 * 
 * This controller receives incident data from the Vision/YOLO system
 * and serves it to the React frontend dashboard.
 */

package com.sentinelfactory.backend.controller;

import java.io.File;        // Entity class for DB
import java.util.List; // Service layer for business logic

import org.springframework.core.io.FileSystemResource;   // For serving image files
import org.springframework.core.io.Resource;              // Resource interface for files
import org.springframework.http.HttpHeaders;              // HTTP response headers
import org.springframework.http.MediaType;                // Content types (JPEG, JSON, etc.)
import org.springframework.http.ResponseEntity;           // HTTP response wrapper
import org.springframework.web.bind.annotation.GetMapping;         // REST annotations
import org.springframework.web.bind.annotation.PathVariable;      // File operations for images
import org.springframework.web.bind.annotation.PostMapping;    // List return type
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import com.sentinelfactory.backend.entity.Alert;
import com.sentinelfactory.backend.service.AlertService;

@RestController                    // Marks class as REST API controller
@RequestMapping("/api/alerts")    // All endpoints start with /api/alerts
public class AlertController {

    private final AlertService alertService;  // Service layer dependency

    /**
     * Constructor - injects AlertService
     * Spring automatically provides the service instance
     */
    public AlertController(AlertService alertService) {
        this.alertService = alertService;
    }

    /**
     * CREATE ALERT
     * POST /api/alerts
     * 
     * Receives alert data from Vision system
     * @param alert - Alert object with incident data
     * @return Saved alert with generated ID
     */
    @PostMapping
    public ResponseEntity<Alert> createAlert(
            @RequestBody Alert alert) {  // @RequestBody converts JSON to Alert object

        // Save alert to database via service layer
        Alert savedAlert =
                alertService.saveAlert(alert);

        // Return saved alert with 200 OK
        return ResponseEntity.ok(
                savedAlert
        );
    }

    /**
     * GET ALL ALERTS
     * GET /api/alerts
     * 
     * Returns all alerts for frontend dashboard display
     * @return List of all alerts from database
     */
    @GetMapping
    public ResponseEntity<List<Alert>> getAllAlerts() {

        // Fetch all alerts from service layer
        return ResponseEntity.ok(
                alertService.getAllAlerts()
        );
    }

    /**
     * GET ALERT BY ID
     * GET /api/alerts/{id}
     * 
     * Returns specific alert for detail view
     * @param id - Alert ID from URL path
     * @return Alert if found, 404 if not
     */
    @GetMapping("/{id}")
    public ResponseEntity<Alert> getAlertById(
            @PathVariable Long id) {  // Extracts ID from URL

        // Fetch alert by ID from service
        Alert alert =
                alertService.getAlertById(id);

        // If alert not found, return 404
        if (alert == null) {
            return ResponseEntity
                    .notFound()
                    .build();
        }

        // Return alert with 200 OK
        return ResponseEntity.ok(
                alert
        );
    }

    // =========================================================
    // INCIDENT IMAGE ENDPOINT
    // =========================================================

    /**
     * GET ALERT IMAGE
     * GET /api/alerts/{id}/image
     * 
     * Serves the incident image file to frontend
     * @param id - Alert ID from URL path
     * @return Image file as JPEG, 404 if no image or file missing
     */
    @GetMapping("/{id}/image")
    public ResponseEntity<Resource> getAlertImage(
            @PathVariable Long id) {

        // Step 1: Get alert from database
        Alert alert =
                alertService.getAlertById(id);

        // Step 2: Check if alert exists
        if (alert == null) {
            return ResponseEntity
                    .notFound()
                    .build();
        }

        // Step 3: Get picture path from alert
        String picturePath =
                alert.getPicture();

        // Step 4: Check if picture path exists
        // If NULL or blank, no image available
        if (picturePath == null ||
                picturePath.isBlank()) {
            return ResponseEntity
                    .notFound()
                    .build();
        }

        // Step 5: Create File object from path
        File imageFile =
                new File(picturePath);

        // Step 6: Check if file exists on disk
        if (!imageFile.exists() ||
                !imageFile.isFile()) {
            return ResponseEntity
                    .notFound()
                    .build();
        }

        // Step 7: Create resource from file
        Resource resource =
                new FileSystemResource(
                        imageFile
                );

        // Step 8: Return image with proper headers
        return ResponseEntity.ok()
                .contentType(
                        MediaType.IMAGE_JPEG  // Set content type as JPEG
                )
                .header(
                        HttpHeaders.CACHE_CONTROL,
                        "no-cache"  // Prevent browser caching
                )
                .body(resource);
    }
}