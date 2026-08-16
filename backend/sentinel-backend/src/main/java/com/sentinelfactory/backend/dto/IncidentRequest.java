package com.sentinelfactory.backend.dto;

import com.fasterxml.jackson.annotation.JsonProperty;

public class IncidentRequest {

    private String timestamp;

    @JsonProperty("person_id")
    private String personId;

    @JsonProperty("incident_name")
    private String incidentName;

    @JsonProperty("incident_type")
    private String incidentType;

    private String source;

    @JsonProperty("body_zone")
    private String bodyZone;

    @JsonProperty("body_zone_id")
    private String bodyZoneId;

    @JsonProperty("hand_zone")
    private String handZone;

    @JsonProperty("hand_zone_id")
    private String handZoneId;

    private String picture;

    @JsonProperty("dwell_seconds")
    private Double dwellSeconds;

    public IncidentRequest() {
    }

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

    public String getIncidentName() {
        return incidentName;
    }

    public void setIncidentName(String incidentName) {
        this.incidentName = incidentName;
    }

    public String getIncidentType() {
        return incidentType;
    }

    public void setIncidentType(String incidentType) {
        this.incidentType = incidentType;
    }

    public String getSource() {
        return source;
    }

    public void setSource(String source) {
        this.source = source;
    }

    public String getBodyZone() {
        return bodyZone;
    }

    public void setBodyZone(String bodyZone) {
        this.bodyZone = bodyZone;
    }

    public String getBodyZoneId() {
        return bodyZoneId;
    }

    public void setBodyZoneId(String bodyZoneId) {
        this.bodyZoneId = bodyZoneId;
    }

    public String getHandZone() {
        return handZone;
    }

    public void setHandZone(String handZone) {
        this.handZone = handZone;
    }

    public String getHandZoneId() {
        return handZoneId;
    }

    public void setHandZoneId(String handZoneId) {
        this.handZoneId = handZoneId;
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