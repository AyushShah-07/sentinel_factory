export interface EventRecord {
  id: number
  cameraId: string
  zoneName: string        // From JSON - zone_name (NEW)
  dwellSeconds: number    // From JSON - dwell_seconds (NEW)
  incidentType: string    // From JSON - incident_type (NEW)
  eventType: string
  timestamp: string
}

// types.ts
export interface AlertRecord {
    id: number
    event_id: string
    person_id: string
    zone_name: string
    incident_type: string
    picture: string
    dwell_seconds: number
    timestamp: string
    camera_id: string
    status: string
}