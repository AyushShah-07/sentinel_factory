package com.sentinelfactory.backend.dto;

import com.fasterxml.jackson.annotation.JsonProperty;

public class IncidentRequest {

    // Field 1: timestamp
    private String timestamp;

    // Field 2: person_id
    @JsonProperty("person_id")
    private String personId;

    // Field 3: zone_name - NEW (this was missing)
    @JsonProperty("zone_name")
    private String zoneName;

    // Field 4: incident_type
    @JsonProperty("incident_type")
    private String incidentType;

    // Field 5: picture_url → goes to picture field
    private String picture;

    // Field 6: dwell_seconds
    @JsonProperty("dwell_seconds")
    private Double dwellSeconds;

    // ============ CONSTRUCTOR ============
    
    public IncidentRequest() {
    }

    // ============ GETTERS AND SETTERS ============

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

    // NEW: zone_name
    public String getZoneName() {
        return zoneName;
    }

    // NEW: zone_name
    public void setZoneName(String zoneName) {
        this.zoneName = zoneName;
    }

    public String getIncidentType() {
        return incidentType;
    }

    public void setIncidentType(String incidentType) {
        this.incidentType = incidentType;
    }

    // This gets picture_url from JSON
    public String getPicture() {
        return picture;
    }

    // This sets picture_url from JSON
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