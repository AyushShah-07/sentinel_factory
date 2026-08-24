/**
 * Alert.java
 * 
 * PURPOSE: JPA Entity class representing the "alerts" table in MySQL database.
 * This class maps Java objects to database records and vice versa.
 * 
 * TABLE STRUCTURE:
 * alerts (
 *   id BIGINT PRIMARY KEY AUTO_INCREMENT,
 *   event_id VARCHAR,
 *   person_id VARCHAR,
 *   zone_name VARCHAR,
 *   incident_type VARCHAR,
 *   picture VARCHAR,
 *   dwell_seconds DOUBLE,
 *   timestamp VARCHAR,
 *   camera_id VARCHAR,
 *   status VARCHAR,
 *   created_at TIMESTAMP,
 *   updated_at TIMESTAMP
 * )
 */

package com.sentinelfactory.backend.entity;

import com.fasterxml.jackson.annotation.JsonProperty;              // Maps field to specific DB column

import jakarta.persistence.Column;             // Marks class as JPA entity
import jakarta.persistence.Entity;     // Auto-generates ID values
import jakarta.persistence.GeneratedValue;     // Specifies ID generation strategy
import jakarta.persistence.GenerationType;                 // Marks primary key field
import jakarta.persistence.Id;              // Specifies table name
import jakarta.persistence.Table; // For JSON serialization

@Entity                                         // Tells JPA this is a database entity
@Table(name = "alerts")                        // Maps to "alerts" table in MySQL
public class Alert {

    @Id                                          // Primary key
    @GeneratedValue(strategy = GenerationType.IDENTITY) // Auto-increment
    private Long id;

    @Column(name = "event_id")                   // Maps to "event_id" column
    @JsonProperty("event_id")                    // JSON response will use "event_id"
    private String eventId;

    @Column(name = "person_id")                  // Maps to "person_id" column
    @JsonProperty("person_id")                   // JSON response will use "person_id"
    private String personId;

    @Column(name = "zone_name")                  // Maps to "zone_name" column
    @JsonProperty("zone_name")                   // JSON response will use "zone_name"
    private String zoneName;

    @Column(name = "incident_type")              // Maps to "incident_type" column
    @JsonProperty("incident_type")               // JSON response will use "incident_type"
    private String incidentType;

    @Column(name = "picture")                    // Maps to "picture" column
    @JsonProperty("picture")                     // JSON response will use "picture"
    private String picture;                      // Stores image URL/path

    @Column(name = "dwell_seconds")              // Maps to "dwell_seconds" column
    @JsonProperty("dwell_seconds")               // JSON response will use "dwell_seconds"
    private Double dwellSeconds;                 // Time spent in zone

    @JsonProperty("timestamp")                   // JSON response will use "timestamp"
    private String timestamp;                    // Incident timestamp

    @Column(name = "camera_id")                  // Maps to "camera_id" column
    @JsonProperty("camera_id")                   // JSON response will use "camera_id"
    private String cameraId;

    @JsonProperty("status")                      // JSON response will use "status"
    private String status;                       // Active, resolved, etc.

    // ============ CONSTRUCTOR ============
    // Default constructor required by JPA
    public Alert() {
    }

    // ============ GETTERS AND SETTERS ============
    // Required for JPA and Jackson to access private fields

    public Long getId() {
        return id;
    }

    public void setId(Long id) {
        this.id = id;
    }

    public String getEventId() {
        return eventId;
    }

    public void setEventId(String eventId) {
        this.eventId = eventId;
    }

    public String getPersonId() {
        return personId;
    }

    public void setPersonId(String personId) {
        this.personId = personId;
    }

    public String getZoneName() {
        return zoneName;
    }

    public void setZoneName(String zoneName) {
        this.zoneName = zoneName;
    }

    public String getIncidentType() {
        return incidentType;
    }

    public void setIncidentType(String incidentType) {
        this.incidentType = incidentType;
    }

    public String getPicture() {
        return picture;
    }

    public void setPicture(String picture) {
        this.picture = picture;
    }

    public Double getDwellSeconds() {
        return dwellSeconds;
    }

    public void setDwellSeconds(Double dwellSeconds) {
        this.dwellSeconds = dwellSeconds;
    }

    public String getTimestamp() {
        return timestamp;
    }

    public void setTimestamp(String timestamp) {
        this.timestamp = timestamp;
    }

    public String getCameraId() {
        return cameraId;
    }

    public void setCameraId(String cameraId) {
        this.cameraId = cameraId;
    }

    public String getStatus() {
        return status;
    }

    public void setStatus(String status) {
        this.status = status;
    }
}