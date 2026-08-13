import { EmptyState } from '../components/common/EmptyState'

export const SafetyZones = () => {
  return (
    <main className="page page--zones">
      <div className="page__header">
        <div>
          <h2>Safety Zones</h2>
          <p>Zone management is a planned feature. Backend integration pending.</p>
        </div>
      </div>

      <section className="panel panel--pending">
        <div className="panel__header">
          <h3>Zone monitoring concept</h3>
        </div>
        <div className="panel__body">
          <EmptyState message="Safety zone API not yet available. UI prepared for future integration." />
        </div>
      </section>
    </main>
  )
}
