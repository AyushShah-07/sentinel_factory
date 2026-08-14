import { createContext, useContext, useEffect, useMemo, useState } from 'react'
import type { AlertRecord, EventRecord } from '../types'
import { fetchAlerts } from '../services/alertService'
import { fetchEvents } from '../services/eventService'

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

const DataContext = createContext<DataContextValue | undefined>(undefined)

export const DataProvider = ({ children }: { children: React.ReactNode }) => {
  const [events, setEvents] = useState<EventRecord[]>([])
  const [alerts, setAlerts] = useState<AlertRecord[]>([])
  const [loadingEvents, setLoadingEvents] = useState(true)
  const [loadingAlerts, setLoadingAlerts] = useState(true)
  const [errorEvents, setErrorEvents] = useState<string | null>(null)
  const [errorAlerts, setErrorAlerts] = useState<string | null>(null)
  const [lastUpdated, setLastUpdated] = useState<string | null>(null)

  const loadAll = async () => {
    setLoadingEvents(true)
    setLoadingAlerts(true)
    setErrorEvents(null)
    setErrorAlerts(null)

    const [eventsResult, alertsResult] = await Promise.allSettled([fetchEvents(), fetchAlerts()])

    const devFallback = Boolean((import.meta as any).env?.DEV)

    if (eventsResult.status === 'fulfilled') {
      setEvents(eventsResult.value)
    } else if (devFallback) {
      // use sample data in dev and do not treat as fatal error
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

    if (alertsResult.status === 'fulfilled') {
      setAlerts(alertsResult.value)
    } else if (devFallback) {
      setAlerts([
        {
          id: 101,
          alertType: 'PROXIMITY',
          severity: 'warning',
          status: 'active',
          message: 'Worker near hazardous area',
          timestamp: new Date().toISOString(),
          eventId: 1,
          cameraId: 'CAM-DEV-01',
          zone: 'DANGER',
          riskScore: 0.88,
        },
      ])
      setErrorAlerts(null)
    } else {
      setErrorAlerts('Unable to load alerts.')
    }

    if (eventsResult.status === 'fulfilled' || alertsResult.status === 'fulfilled') {
      setLastUpdated(new Date().toISOString())
    }

    setLoadingEvents(false)
    setLoadingAlerts(false)
  }

  useEffect(() => {
    loadAll()
    const interval = window.setInterval(loadAll, 5000)
    return () => window.clearInterval(interval)
  }, [])

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

export const useData = () => {
  const context = useContext(DataContext)
  if (!context) {
    throw new Error('useData must be used within DataProvider')
  }
  return context
}
