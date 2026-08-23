import { Link } from 'react-router-dom'
import type { EventRecord } from '../../types'

interface Props {
  events: EventRecord[]
}

export const RecentEvents = ({ events }: Props) => {
  const recent = events.slice(0, 4)

  return (
    <section className="panel">
      <div className="panel__header">
        <h3>Recent Events</h3>
        <Link to="/events">View all</Link>
      </div>

      {recent.length === 0 ? (
        <div className="panel__empty">
          No detection events recorded.
        </div>
      ) : (
        <table className="panel__table">
          <thead>
            <tr>
              <th>ID</th>
              <th>Camera</th>
              <th>Event</th>
              <th>Risk</th>
            </tr>
          </thead>

          <tbody>
            {recent.map((event) => (
              <tr key={event.id}>
                <td>{event.id}</td>
                <td>{event.cameraId}</td>
                <td>{event.eventType}</td>
                <td>
                  {event.riskScore != null ? `${Math.round(event.riskScore * 100)}%` : 'N/A'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  )
}