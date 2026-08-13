import { useEffect, useState } from 'react'
import type { EventRecord } from '../types'
import { fetchEvents } from '../services/eventService'

export const useEvents = () => {
  const [events, setEvents] = useState<EventRecord[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const loadEvents = async () => {
    setLoading(true)
    setError(null)

    try {
      const data = await fetchEvents()
      setEvents(data)
    } catch (err) {
      setError('Unable to load events.')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadEvents()
    const interval = window.setInterval(loadEvents, 5000)
    return () => window.clearInterval(interval)
  }, [])

  return { events, loading, error, reload: loadEvents }
}
