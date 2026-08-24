/**
 * IncidentRequest.java
 * 
 * PURPOSE: Data Transfer Object (DTO) for incoming incident data.
 * This class receives JSON data from the Vision/YOLO system and maps
 * snake_case JSON fields to Java camelCase fields.
 * 
 * JSON INPUT EXAMPLE:
 * {
 *   "timestamp": "2026-08-24T11:06:04.938",
 *   "person_id": "P6",
 *   "zone_name": "DANGER_1",
 *   "incident_type": "DANGER",
 *   "picture_url": "C:\\...\\P6_2026-08-24_11-06-04-938924.jpg",
 *   "dwell_seconds": 0.0
 * }
 */

package com.sentinelfactory.backend.dto;

import com.fasterxml.jackson.annotation.JsonProperty; // For mapping JSON fields to Java fields

public class IncidentRequest {

    // Field 1: timestamp
    // Maps JSON "timestamp" to Java "timestamp"
    // @JsonProperty ensures correct mapping even if names don't match exactly
    @JsonProperty("timestamp")
    private String timestamp;

    // Field 2: person_id
    // Maps JSON "person_id" to Java "personId"
    @JsonProperty("person_id")
    private String personId;

    // Field 3: zone_name
    // Maps JSON "zone_name" to Java "zoneName"
    // This field was previously missing, causing NULL values in DB
    @JsonProperty("zone_name")
    private String zoneName;

    // Field 4: incident_type
    // Maps JSON "incident_type" to Java "incidentType"
    @JsonProperty("incident_type")
    private String incidentType;

    // Field 5: picture_url → picture
    // Maps JSON "picture_url" to Java "picture"
    // CRITICAL FIX: JSON sends "picture_url" but DB column is "picture"
    @JsonProperty("picture_url")
    private String picture;

    // Field 6: dwell_seconds
    // Maps JSON "dwell_seconds" to Java "dwellSeconds"
    @JsonProperty("dwell_seconds")
    private Double dwellSeconds;

    // ============ CONSTRUCTOR ============
    // Default constructor required by Jackson for deserialization
    public IncidentRequest() {
    }

    // ============ GETTERS AND SETTERS ============
    // Getters and setters allow Jackson to access private fields
    // and allow controller to retrieve values for DB mapping

    public String getTimestamp() {
        return timestamp;
    }

    public void setTimestamp(String timestamp) {
        this.timestamp = timestamp;
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
}