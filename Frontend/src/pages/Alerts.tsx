import { useMemo, useState } from 'react'
import { useData } from '../context/DataContext'
import { LoadingState } from '../components/common/LoadingState'
import { ErrorState } from '../components/common/ErrorState'
import { EmptyState } from '../components/common/EmptyState'
import { AlertDetailDrawer } from '../components/alerts/AlertDetailDrawer'
import type { AlertRecord } from '../types'

// ✅ UPDATED: Only JSON fields + Picture
const alertHeaders = [
  'Alert ID',    // Database primary key
  'Type',        // From JSON - incident_type
  'Person',      // From JSON - person_id
  'Zone',        // From JSON - zone_name
  'Dwell',       // From JSON - dwell_seconds
  'Picture',     // From JSON - picture_url
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

  // Filter alerts based on search query
  const filtered = useMemo(() => {
    const query = search.toLowerCase()

    return alerts.filter((alert) =>
      [
        alert.id.toString(),
        alert.incidentType,
        alert.personId,
        alert.zoneName,
        alert.dwellSeconds?.toString(),
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

  // Get image URL from backend - UNCHANGED
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
                    {/* Alert ID - from database */}
                    <td>{alert.id}</td>

                    {/* Type - from JSON incident_type */}
                    <td>{alert.incidentType ?? 'Unknown'}</td>

                    {/* Person - from JSON person_id */}
                    <td>{alert.personId ?? 'Unknown'}</td>

                    {/* Zone - from JSON zone_name */}
                    <td>{alert.zoneName ?? 'Unknown'}</td>

                    {/* Dwell - from JSON dwell_seconds */}
                    <td>
                      {alert.dwellSeconds != null
                        ? `${alert.dwellSeconds}s`
                        : '--'}
                    </td>

                    {/* Picture - from JSON picture_url - UNCHANGED */}
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