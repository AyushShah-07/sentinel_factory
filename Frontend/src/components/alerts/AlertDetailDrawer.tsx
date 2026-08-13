import type { AlertRecord } from '../../types'
import './AlertDetailDrawer.css'

interface Props {
  alert: AlertRecord
  onClose: () => void
}

export const AlertDetailDrawer = ({ alert, onClose }: Props) => {
  return (
    <div className="drawer-overlay" onClick={onClose}>
      <div className="drawer" onClick={(event) => event.stopPropagation()}>
        <div className="drawer__header">
          <h3>Alert Details</h3>
          <button type="button" onClick={onClose}>
            Close
          </button>
        </div>
        <div className="drawer__content">
          <div className="detail-row">
            <span>Alert ID</span>
            <strong>{alert.id}</strong>
          </div>
          <div className="detail-row">
            <span>Event ID</span>
            <strong>{alert.eventId ?? 'Unknown'}</strong>
          </div>
          <div className="detail-row">
            <span>Alert Type</span>
            <strong>{alert.alertType ?? 'Unknown'}</strong>
          </div>
          <div className="detail-row">
            <span>Severity</span>
            <strong>{alert.severity ?? 'Unknown'}</strong>
          </div>
          <div className="detail-row">
            <span>Status</span>
            <strong>{alert.status ?? 'Unknown'}</strong>
          </div>
          <div className="detail-row">
            <span>Camera ID</span>
            <strong>{alert.cameraId ?? 'Unknown'}</strong>
          </div>
          <div className="detail-row">
            <span>Zone</span>
            <strong>{alert.zone ?? 'Pending'}</strong>
          </div>
          <div className="detail-row">
            <span>Risk Score</span>
            <strong>{alert.riskScore != null ? Math.round(alert.riskScore * 100) + '%' : 'Unknown'}</strong>
          </div>
          <div className="detail-row detail-row--message">
            <span>Message</span>
            <p>{alert.message ?? 'No message available.'}</p>
          </div>
        </div>
      </div>
    </div>
  )
}
