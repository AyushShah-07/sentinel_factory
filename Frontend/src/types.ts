export interface EventRecord {
  id: number
  cameraId: string
  employeeId: number | null
  eventId: string
  eventType: string
  riskScore: number | null
  timestamp: string
  trackId: string | null
  zone: string | null
  machineState: string | null
}

export interface AlertRecord {
  id: number

  alertType: string
  severity: string
  status: string

  message?: string

  timestamp?: string

  eventId?: string

  cameraId?: string

  zone?: string | null

  riskScore?: number | null

  personId?: string | null

  incidentName?: string | null

  source?: string | null

  bodyZone?: string | null

  bodyZoneId?: string | null

  handZone?: string | null

  handZoneId?: string | null

  picture?: string | null

  dwellSeconds?: number | null
}