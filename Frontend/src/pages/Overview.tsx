import { MetricCard } from '../components/dashboard/MetricCard'
import { SafetyStatus } from '../components/dashboard/SafetyStatus'
import { RecentAlerts } from '../components/dashboard/RecentAlerts'
import { RecentEvents } from '../components/dashboard/RecentEvents'
import { useData } from '../context/DataContext'
import { LoadingState } from '../components/common/LoadingState'
import { ErrorState } from '../components/common/ErrorState'

export const Overview = () => {
  const { events, alerts, loadingEvents, loadingAlerts, errorEvents, errorAlerts } = useData()

  const activeAlerts = alerts.filter((alert) => alert.status?.toLowerCase() === 'active').length
  const criticalAlerts = alerts.filter((alert) => alert.severity?.toLowerCase() === 'critical').length
  const latestEvent = events[0]
  const deviceCount = 0

  return (
    <main className="page page--overview">
      <SafetyStatus alerts={alerts} />

      <div className="kpi-grid">
        <MetricCard label="Active Alerts" value={loadingAlerts ? '...' : activeAlerts} />
        <MetricCard label="Critical Alerts" value={loadingAlerts ? '...' : criticalAlerts} />
        <MetricCard label="Total Events" value={loadingEvents ? '...' : events.length} />
        <MetricCard
          label="Latest Detection"
          value={
            latestEvent
              ? `${latestEvent.eventType} @ ${latestEvent.cameraId ?? 'Unknown'}`
              : loadingEvents
              ? '...'
              : 'None'
          }
          detail={latestEvent ? new Date(latestEvent.timestamp).toLocaleString() : undefined}
        />
        <MetricCard label="Cameras / Devices" value={deviceCount} detail="Backend integration pending" />
      </div>

      {loadingEvents || loadingAlerts ? (
        <LoadingState message="Loading factory status..." />
      ) : errorEvents || errorAlerts ? (
        <ErrorState
          message={errorEvents || errorAlerts || 'Unable to retrieve factory safety data.'}
          onRetry={() => window.location.reload()}
        />
      ) : (
        <div className="overview-panels">
          <RecentEvents events={events} />
          <RecentAlerts alerts={alerts} />
        </div>
      )}
    </main>
  )
}
