import { useMemo, useState } from 'react'
import { useData } from '../context/DataContext'
import { LoadingState } from '../components/common/LoadingState'
import { ErrorState } from '../components/common/ErrorState'
import { EmptyState } from '../components/common/EmptyState'

const eventHeaders = ['ID', 'Camera', 'Event', 'Risk', 'Timestamp']

type SortKey = 'timestamp' | 'riskScore' | 'cameraId' | 'eventType'

export const Events = () => {
  const { events, loadingEvents: loading, errorEvents: error, reloadData: reload } = useData()
  const [search, setSearch] = useState('')
  const [sortKey, setSortKey] = useState<SortKey>('timestamp')
  const [sortDirection, setSortDirection] = useState<'asc' | 'desc'>('desc')

  const filtered = useMemo(() => {
    const query = search.toLowerCase()
    return events
      .filter((event) =>
        [event.cameraId, event.eventType, event.timestamp]
          .filter(Boolean)
          .some((value) => value!.toString().toLowerCase().includes(query)),
      )
      .sort((a, b) => {
        const aValue = sortKey === 'riskScore' ? a.riskScore : (a as any)[sortKey]
        const bValue = sortKey === 'riskScore' ? b.riskScore : (b as any)[sortKey]

        if (typeof aValue === 'string' && typeof bValue === 'string') {
          return sortDirection === 'asc' ? aValue.localeCompare(bValue) : bValue.localeCompare(aValue)
        }

        return sortDirection === 'asc' ? (Number(aValue) as number) - (Number(bValue) as number) : (Number(bValue) as number) - (Number(aValue) as number)
      })
  }, [events, search, sortDirection, sortKey])

  const handleSort = (key: SortKey) => {
    if (sortKey === key) {
      setSortDirection((current) => (current === 'asc' ? 'desc' : 'asc'))
    } else {
      setSortKey(key)
      setSortDirection('desc')
    }
  }

  const mapLabelToKey = (label: string): SortKey => {
    switch (label) {
      case 'Timestamp':
        return 'timestamp'
      case 'Risk':
        return 'riskScore'
      case 'Camera':
        return 'cameraId'
      case 'Event':
        return 'eventType'
      default:
        return 'timestamp'
    }
  }

  return (
    <main className="page page--events">
      <div className="page__header">
        <div>
          <h2>Events</h2>
          <p>Detection records from the backend event stream.</p>
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
          placeholder="Search camera, event, timestamp"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
        />
      </div>

      {loading ? (
        <LoadingState message="Loading events..." />
      ) : error ? (
        <ErrorState message={error} onRetry={reload} />
      ) : filtered.length === 0 ? (
        <EmptyState message="No detection events recorded." />
      ) : (
        <div className="data-table-wrapper">
          <table className="data-table">
            <thead>
              <tr>
                {eventHeaders.map((label) => (
                  <th key={label} onClick={() => handleSort(mapLabelToKey(label))}>{label}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {filtered.map((event) => (
                <tr key={event.id}>
                  <td>{event.id}</td>
                  <td>{event.cameraId}</td>
                  <td>{event.eventType}</td>
                  <td>{event.riskScore != null ? Math.round(event.riskScore * 100) + '%' : 'Unknown'}</td>
                  <td>{new Date(event.timestamp).toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </main>
  )
}
