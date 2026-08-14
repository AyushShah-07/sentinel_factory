import { useMemo, useState } from 'react'
import { useData } from '../context/DataContext'
import { LoadingState } from '../components/common/LoadingState'
import { ErrorState } from '../components/common/ErrorState'
import { EmptyState } from '../components/common/EmptyState'
import { AlertDetailDrawer } from '../components/alerts/AlertDetailDrawer'
import type { AlertRecord } from '../types'

const alertHeaders = ['Alert ID', 'Event ID', 'Type', 'Severity', 'Camera', 'Zone', 'Risk', 'Status']

export const Alerts = () => {
  const { alerts, loadingAlerts: loading, errorAlerts: error, reloadData: reload } = useData()
  const [search, setSearch] = useState('')
  const [selectedAlert, setSelectedAlert] = useState<AlertRecord | null>(null)

  const filtered = useMemo(() => {
    const query = search.toLowerCase()
    return alerts.filter((alert) =>
      [
        alert.id.toString(),
        alert.eventId?.toString(),
        alert.alertType,
        alert.severity,
        alert.cameraId,
        alert.zone,
        alert.status,
        alert.message,
      ]
        .filter(Boolean)
        .some((value) => value?.toLowerCase().includes(query)),
    )
  }, [alerts, search])

  return (
    <main className="page page--alerts">
      <div className="page__header">
        <div>
          <h2>Alerts</h2>
          <p>Safety alerts forwarded by the backend service.</p>
        </div>
        <div className="page__actions">
          <button type="button" onClick={reload}>
            Refresh
          </button>
        </div>
      </div>

      <div className="table-toolbar">
        <input
          type="search"
          placeholder="Search alerts by camera, severity, status"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
        />
      </div>

      {loading ? (
        <LoadingState message="Loading alerts..." />
      ) : error ? (
        <ErrorState message={error} onRetry={reload} />
      ) : filtered.length === 0 ? (
        <EmptyState message="No safety alerts." />
      ) : (
        <div className="data-table-wrapper">
          <table className="data-table">
            <thead>
              <tr>
                {alertHeaders.map((label) => (
                  <th key={label}>{label}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {filtered.map((alert) => (
                <tr key={alert.id} onClick={() => setSelectedAlert(alert)}>
                  <td>{alert.id}</td>
                  <td>{alert.eventId ?? 'Unknown'}</td>
                  <td>{alert.alertType ?? 'Unknown'}</td>
                  <td>{alert.severity ?? 'Unknown'}</td>
                  <td>{alert.cameraId ?? 'Unknown'}</td>
                  <td>{alert.zone ?? 'Pending'}</td>
                  <td>{alert.riskScore != null ? Math.round(alert.riskScore * 100) + '%' : 'Unknown'}</td>
                  <td>{alert.status ?? 'Unknown'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {selectedAlert ? <AlertDetailDrawer alert={selectedAlert} onClose={() => setSelectedAlert(null)} /> : null}
    </main>
  )
}
