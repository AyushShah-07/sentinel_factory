package com.sentinelfactory.backend.dto;

import com.fasterxml.jackson.annotation.JsonProperty;

public class PersonDetectionRequest {

    private String timestamp;

    private String event;

    @JsonProperty("person_id")
    private String personId;

    public PersonDetectionRequest() {
    }

    public String getTimestamp() {
        return timestamp;
    }

    public void setTimestamp(String timestamp) {
        this.timestamp = timestamp;
    }

    public String getEvent() {
        return event;
    }

    public void setEvent(String event) {
        this.event = event;
    }

    public String getPersonId() {
        return personId;
    }

    public void setPersonId(String personId) {
        this.personId = personId;
    }
}