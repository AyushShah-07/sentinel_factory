import { useEffect, useState } from 'react'
import type { AlertRecord } from '../types'
import { fetchAlerts } from '../services/alertService'

export const useAlerts = () => {
  const [alerts, setAlerts] = useState<AlertRecord[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const loadAlerts = async () => {
    setLoading(true)
    setError(null)

    try {
      const data = await fetchAlerts()
      setAlerts(data)
    } catch (err) {
      setError('Unable to load alerts.')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadAlerts()
    const interval = window.setInterval(loadAlerts, 5000)
    return () => window.clearInterval(interval)
  }, [])

  return { alerts, loading, error, reload: loadAlerts }
}
