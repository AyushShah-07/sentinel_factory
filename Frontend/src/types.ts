export interface EventRecord {
  id: number
  cameraId: string
  zoneName: string        // From JSON - zone_name (NEW)
  dwellSeconds: number    // From JSON - dwell_seconds (NEW)
  incidentType: string    // From JSON - incident_type (NEW)
  eventType: string
  timestamp: string
}

export interface AlertRecord {
  id: number

  status: string

  message?: string

  timestamp?: string

  eventId?: string | number

  cameraId?: string

  personId?: string | null

  zoneName: string        // From JSON - zone_name (NEW)
  incidentType: string    // From JSON - incident_type (NEW)

  picture?: string | null

  dwellSeconds?: number | null
}