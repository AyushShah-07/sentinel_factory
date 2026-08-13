export interface EventRecord {
  id: number
  cameraId: string
  employeeId: number | null
  eventId: string
  eventType: string
  riskScore: number
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
  // optional fields: backend may include references to events/devices; keep optional to avoid runtime/TS errors
  eventId?: number
  cameraId?: string
  zone?: string | null
  riskScore?: number
}