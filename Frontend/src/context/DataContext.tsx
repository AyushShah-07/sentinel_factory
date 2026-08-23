// =========================================================
// IMPORTS - FIXED
// =========================================================
import { createContext, useContext, useEffect, useMemo, useState } from 'react'
import type { AlertRecord, EventRecord } from '../types'
import { fetchAlerts } from '../services/alertService'
import { fetchEvents } from '../services/eventService'

// =========================================================
// CONTEXT TYPE DEFINITION
// =========================================================
interface DataContextValue {
  events: EventRecord[]
  alerts: AlertRecord[]
  loadingEvents: boolean
  loadingAlerts: boolean
  errorEvents: string | null
  errorAlerts: string | null
  lastUpdated: string | null
  reloadData: () => void
}

// =========================================================
// CREATE CONTEXT
// =========================================================
const DataContext = createContext<DataContextValue | undefined>(undefined)

// =========================================================
// DATA PROVIDER COMPONENT
// =========================================================
export const DataProvider = ({ children }: { children: React.ReactNode }) => {
  // State for events and alerts
  const [events, setEvents] = useState<EventRecord[]>([])
  const [alerts, setAlerts] = useState<AlertRecord[]>([])
  const [loadingEvents, setLoadingEvents] = useState(true)
  const [loadingAlerts, setLoadingAlerts] = useState(true)
  const [errorEvents, setErrorEvents] = useState<string | null>(null)
  const [errorAlerts, setErrorAlerts] = useState<string | null>(null)
  const [lastUpdated, setLastUpdated] = useState<string | null>(null)

  // =========================================================
  // FETCH DATA FROM BACKEND
  // =========================================================
  const loadAll = async () => {
    setLoadingEvents(true)
    setLoadingAlerts(true)
    setErrorEvents(null)
    setErrorAlerts(null)

    // Fetch both events and alerts in parallel
    const [eventsResult, alertsResult] = await Promise.allSettled([
      fetchEvents(),
      fetchAlerts()
    ])

    const devFallback = Boolean((import.meta as any).env?.DEV)

    // =========================================================
    // HANDLE EVENTS RESPONSE
    // =========================================================
    if (eventsResult.status === 'fulfilled') {
      setEvents(eventsResult.value)
    } else if (devFallback) {
      // Sample data for development when backend is not available
      setEvents([
        {
          id: 1,
          cameraId: 'CAM-DEV-01',
          employeeId: null,
          eventId: 'EVT-DEV-1',
          eventType: 'PERSON_DETECTED',
          riskScore: 0.12,
          timestamp: new Date().toISOString(),
          trackId: 'T-DEV-1',
          zone: 'ZONE-A',
          machineState: 'ON',
        },
        {
          id: 2,
          cameraId: 'CAM-DEV-02',
          employeeId: null,
          eventId: 'EVT-DEV-2',
          eventType: 'DANGER_ZONE',
          riskScore: 0.95,
          timestamp: new Date().toISOString(),
          trackId: 'T-DEV-2',
          zone: 'DANGER',
          machineState: 'OFF',
        },
      ])
      setErrorEvents(null)
    } else {
      setErrorEvents('Unable to load events.')
    }

    // =========================================================
    // HANDLE ALERTS RESPONSE
    // =========================================================
    if (alertsResult.status === 'fulfilled') {
      setAlerts(alertsResult.value)
    } else if (devFallback) {
      // ✅ UPDATED: Sample alert with new fields (matches Alert.java)
      setAlerts([
        {
          id: 101,
          eventId: 'YOLO-123456',
          personId: 'P1',
          zoneName: 'DANGER_1',        // ✅ From JSON - zone_name
          incidentType: 'DANGER',      // ✅ From JSON - incident_type
          picture: '',                 // From JSON - picture_url (empty for sample)
          dwellSeconds: 0,             // ✅ From JSON - dwell_seconds
          timestamp: new Date().toISOString(),
          cameraId: 'CAM-DEV-01',
          status: 'ACTIVE',
        },
        {
          id: 102,
          eventId: 'YOLO-123457',
          personId: 'P2',
          zoneName: 'WARNING_1',       // ✅ From JSON - zone_name
          incidentType: 'WARNING',     // ✅ From JSON - incident_type
          picture: '',                 // From JSON - picture_url (empty for sample)
          dwellSeconds: 5.2,           // ✅ From JSON - dwell_seconds
          timestamp: new Date().toISOString(),
          cameraId: 'CAM-DEV-02',
          status: 'ACTIVE',
        },
      ])
      setErrorAlerts(null)
    } else {
      setErrorAlerts('Unable to load alerts.')
    }

    // Update last updated timestamp
    if (eventsResult.status === 'fulfilled' || alertsResult.status === 'fulfilled') {
      setLastUpdated(new Date().toISOString())
    }

    setLoadingEvents(false)
    setLoadingAlerts(false)
  }

  // =========================================================
  // AUTO-REFRESH EVERY 5 SECONDS
  // =========================================================
  useEffect(() => {
    loadAll()
    const interval = window.setInterval(loadAll, 5000)
    return () => window.clearInterval(interval)
  }, [])

  // =========================================================
  // CONTEXT VALUE
  // =========================================================
  const value = useMemo(
    () => ({
      events,
      alerts,
      loadingEvents,
      loadingAlerts,
      errorEvents,
      errorAlerts,
      lastUpdated,
      reloadData: loadAll,
    }),
    [alerts, errorAlerts, errorEvents, lastUpdated, events, loadingAlerts, loadingEvents],
  )

  return <DataContext.Provider value={value}>{children}</DataContext.Provider>
}

// =========================================================
// USE DATA HOOK
// =========================================================
export const useData = () => {
  const context = useContext(DataContext)
  if (!context) {
    throw new Error('useData must be used within DataProvider')
  }
  return context
}