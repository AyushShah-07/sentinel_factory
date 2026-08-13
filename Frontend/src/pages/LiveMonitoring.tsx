import { LiveVideoPlayer } from '../components/video/LiveVideoPlayer'
import { useData } from '../context/DataContext'
import { RecentAlerts } from '../components/dashboard/RecentAlerts'
import { LoadingState } from '../components/common/LoadingState'
import { ErrorState } from '../components/common/ErrorState'

export const LiveMonitoring = () => {
  const { alerts, loadingAlerts, errorAlerts } = useData()

  return (
    <main className="page page--live">
      <div className="live-layout">
        <LiveVideoPlayer />

        <aside className="live-sidebar">
          {loadingAlerts ? (
            <LoadingState message="Loading alerts..." />
          ) : errorAlerts ? (
            <ErrorState message={errorAlerts} onRetry={() => window.location.reload()} />
          ) : (
            <RecentAlerts alerts={alerts} />
          )}
        </aside>
      </div>
    </main>
  )
}
