import { StatusBadge } from '../common/StatusBadge'
import type { AlertRecord } from '../../types'

interface Props {
  alerts: AlertRecord[]
}

export const SafetyStatus = ({ alerts }: Props) => {
  const hasCritical = alerts.some((alert) => alert.severity?.toLowerCase() === 'critical')
  const hasWarning = alerts.some((alert) => alert.severity?.toLowerCase() === 'warning')

  const status = hasCritical ? 'CRITICAL' : hasWarning ? 'WARNING' : 'SAFE'
  const tone = hasCritical ? 'critical' : hasWarning ? 'warning' : 'safe'
  const subtitle = hasCritical
    ? 'Immediate attention required.'
    : hasWarning
    ? 'Potential safety concern detected.'
    : 'All monitored zones are currently clear.'

  return (
    <section className="status-card">
      <div className="status-card__label">FACTORY SAFETY STATUS</div>
      <div className="status-card__status">
        <span>{status}</span>
        <StatusBadge label={tone.toUpperCase()} tone={tone} />
      </div>
      <p>{subtitle}</p>
    </section>
  )
}
