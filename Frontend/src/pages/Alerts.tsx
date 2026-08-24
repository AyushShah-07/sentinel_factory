import { useMemo, useState } from 'react'
import { useData } from '../context/DataContext'
import { LoadingState } from '../components/common/LoadingState'
import { ErrorState } from '../components/common/ErrorState'
import { EmptyState } from '../components/common/EmptyState'
import { AlertDetailDrawer } from '../components/alerts/AlertDetailDrawer'
import type { AlertRecord } from '../types'

const alertHeaders = [
  'Alert ID',
  'Type',
  'Person',
  'Zone',
  'Dwell',
  'Picture',
]

export const Alerts = () => {
  const {
    alerts,
    loadingAlerts: loading,
    errorAlerts: error,
    reloadData: reload,
  } = useData()

  const [search, setSearch] = useState('')
  const [selectedAlert, setSelectedAlert] =
    useState<AlertRecord | null>(null)

  const filtered = useMemo(() => {
    const query = search.toLowerCase()

    return alerts.filter((alert) =>
      [
        alert.id?.toString(),
        alert.incident_type,      // ✅ Changed from incidentType
        alert.person_id,          // ✅ Changed from personId
        alert.zone_name,          // ✅ Changed from zoneName
        alert.dwell_seconds?.toString(),  // ✅ Changed from dwellSeconds
      ]
        .filter(Boolean)
        .some((value) =>
          value!
            .toString()
            .toLowerCase()
            .includes(query),
        )
    )
  }, [alerts, search])

  const getImageUrl = (alert: AlertRecord) => {
    if (!alert.picture) {
      return null
    }
    return `http://localhost:8080/api/alerts/${alert.id}/image`
  }

  return (
    <main className="page page--alerts">
      <div className="page__header">
        <div>
          <h2>Alerts</h2>
          <p>
            Danger incidents and warning-zone dwell incidents
            recorded by the backend.
          </p>
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
          placeholder="Search person, zone, type..."
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
              {filtered.map((alert) => {
                const imageUrl = getImageUrl(alert)

                return (
                  <tr
                    key={alert.id}
                    onClick={() => setSelectedAlert(alert)}
                    style={{ cursor: 'pointer' }}
                  >
                    {/* Alert ID */}
                    <td>{alert.id}</td>

                    {/* Type - snake_case */}
                    <td>{alert.incident_type ?? 'Unknown'}</td>

                    {/* Person - snake_case */}
                    <td>{alert.person_id ?? 'Unknown'}</td>

                    {/* Zone - snake_case */}
                    <td>{alert.zone_name ?? 'Unknown'}</td>

                    {/* Dwell - snake_case */}
                    <td>
                      {alert.dwell_seconds != null
                        ? `${alert.dwell_seconds}s`
                        : '--'}
                    </td>

                    {/* Picture */}
                    <td>
                      {imageUrl ? (
                        <img
                          src={imageUrl}
                          alt="Incident"
                          onClick={(event) => {
                            event.stopPropagation()
                            setSelectedAlert(alert)
                          }}
                          style={{
                            width: '90px',
                            height: '55px',
                            objectFit: 'cover',
                            borderRadius: '6px',
                            border: '1px solid #334155',
                            cursor: 'pointer',
                            background: '#05080b',
                          }}
                        />
                      ) : (
                        'No image'
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}

      {selectedAlert ? (
        <AlertDetailDrawer
          alert={selectedAlert}
          onClose={() => setSelectedAlert(null)}
        />
      ) : null}
    </main>
  )
}