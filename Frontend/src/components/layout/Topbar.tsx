import { useData } from '../../context/DataContext'
import './Topbar.css'

export const Topbar = () => {
  const { events, alerts, loadingEvents, loadingAlerts, lastUpdated } = useData()

  return (
    <header className="topbar">
      <div className="topbar__section">
        <div>
          <span className="topbar__label">System Status</span>
          <strong>Monitoring</strong>
        </div>
      </div>

      <div className="topbar__stats">
        <div className="topbar__stat">
          <span>Events</span>
          <strong>{loadingEvents ? '...' : events.length}</strong>
        </div>
        <div className="topbar__stat">
          <span>Alerts</span>
          <strong>{loadingAlerts ? '...' : alerts.length}</strong>
        </div>
        <div className="topbar__stat">
          <span>Last update</span>
          <strong>{lastUpdated ? new Date(lastUpdated).toLocaleTimeString() : 'Checking...'}</strong>
        </div>
      </div>

      <div className="topbar__actions">
        <button type="button">Notifications</button>
        <button type="button">Settings</button>
        <button type="button">Operator</button>
      </div>
    </header>
  )
}
