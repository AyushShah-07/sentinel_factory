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

    if (eventsResult.status === 'fulfilled') {
      setEvents(eventsResult.value)
    } else {
      setErrorEvents('Unable to load events.')
    }

    if (alertsResult.status === 'fulfilled') {
      setAlerts(alertsResult.value)
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
