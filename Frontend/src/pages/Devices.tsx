import { EmptyState } from '../components/common/EmptyState'

export const Devices = () => {
  return (
    <main className="page page--devices">
      <div className="page__header">
        <div>
          <h2>Devices</h2>
          <p>Device telemetry integration is pending. The backend does not expose a device API yet.</p>
        </div>
      </div>

      <section className="panel panel--pending">
        <div className="panel__header">
          <h3>Device monitoring framework</h3>
        </div>
        <div className="panel__body">
          <EmptyState message="Device inventory and status will connect once backend APIs are available." />
        </div>
      </section>
    </main>
  )
}
