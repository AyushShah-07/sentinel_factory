import { Link } from 'react-router-dom'
import type { AlertRecord } from '../../types'

interface Props {
  alerts: AlertRecord[]
}

export const RecentAlerts = ({ alerts }: Props) => {
  const recent = alerts.slice(0, 4)

  return (
    <section className="panel">
      <div className="panel__header">
        <h3>Recent Alerts</h3>
        <Link to="/alerts">View all</Link>
      </div>
      {recent.length === 0 ? (
        <div className="panel__empty">No safety alerts.</div>
      ) : (
        <table className="panel__table">
          <thead>
            <tr>
              <th>ID</th>
              <th>Type</th>
              <th>Camera</th>
              <th>Severity</th>
            </tr>
          </thead>
          <tbody>
            {recent.map((alert) => (
              <tr key={alert.id}>
                <td>{alert.id}</td>
                <td>{alert.alertType ?? 'Unknown'}</td>
                <td>{alert.cameraId ?? 'Unknown'}</td>
                <td>{alert.severity ?? 'Unknown'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  )
}
