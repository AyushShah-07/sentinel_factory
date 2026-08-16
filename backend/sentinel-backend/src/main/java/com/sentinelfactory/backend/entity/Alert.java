package com.sentinelfactory.backend.entity;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.Table;

@Entity
@Table(name = "alerts")
public class Alert {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "event_id")
    private String eventId;

    @Column(name = "alert_type")
    private String alertType;

    private String severity;

    private String message;

    private String zone;

    @Column(name = "risk_score")
    private Double riskScore;

    @Column(name = "camera_id")
    private String cameraId;

    private String status;

    private String timestamp;

    @Column(name = "person_id")
    private String personId;

    @Column(name = "incident_name")
    private String incidentName;

    private String source;

    @Column(name = "body_zone")
    private String bodyZone;

    @Column(name = "body_zone_id")
    private String bodyZoneId;

    @Column(name = "hand_zone")
    private String handZone;

    @Column(name = "hand_zone_id")
    private String handZoneId;

    @Column(name = "picture")
    private String picture;

    @Column(name = "dwell_seconds")
    private Double dwellSeconds;

    public Alert() {
    }

    public Long getId() {
        return id;
    }

    public String getEventId() {
        return eventId;
    }

    public void setEventId(String eventId) {
        this.eventId = eventId;
    }

    public String getAlertType() {
        return alertType;
    }

    public void setAlertType(String alertType) {
        this.alertType = alertType;
    }

    public String getSeverity() {
        return severity;
    }

    public void setSeverity(String severity) {
        this.severity = severity;
    }

    public String getMessage() {
        return message;
    }

    public void setMessage(String message) {
        this.message = message;
    }

    public String getZone() {
        return zone;
    }

    public void setZone(String zone) {
        this.zone = zone;
    }

    public Double getRiskScore() {
        return riskScore;
    }

    public void setRiskScore(Double riskScore) {
        this.riskScore = riskScore;
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