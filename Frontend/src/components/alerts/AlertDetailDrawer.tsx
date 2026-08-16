import type { AlertRecord } from '../../types'
import './AlertDetailDrawer.css'

interface Props {
  alert: AlertRecord
  onClose: () => void
}

export const AlertDetailDrawer = ({
  alert,
  onClose,
}: Props) => {

  const imageUrl = alert.picture
    ? `http://localhost:8080/api/alerts/${alert.id}/image`
    : null

  const isWarning =
    alert.alertType?.toUpperCase() === 'WARNING'

  return (
    <div
      className="drawer-overlay"
      onClick={onClose}
    >
      <div
        className="drawer"
        onClick={(event) =>
          event.stopPropagation()
        }
      >

        <div className="drawer__header">
          <div>
            <h3>Alert Details</h3>

            <span>
              {isWarning
                ? 'WARNING INCIDENT'
                : 'DANGER INCIDENT'}
            </span>
          </div>

          <button
            type="button"
            onClick={onClose}
          >
            Close
          </button>
        </div>

        <div className="drawer__content">

          <div className="detail-row">
            <span>Alert ID</span>
            <strong>
              {alert.id}
            </strong>
          </div>

          <div className="detail-row">
            <span>Event ID</span>
            <strong>
              {alert.eventId ?? 'Unknown'}
            </strong>
          </div>

          <div className="detail-row">
            <span>Incident Type</span>
            <strong>
              {alert.alertType ?? 'Unknown'}
            </strong>
          </div>

          <div className="detail-row">
            <span>Incident Name</span>
            <strong>
              {alert.incidentName ??
                alert.message ??
                'Unknown'}
            </strong>
          </div>

          <div className="detail-row">
            <span>Person</span>
            <strong>
              {alert.personId ?? 'Unknown'}
            </strong>
          </div>

          <div className="detail-row">
            <span>Camera ID</span>
            <strong>
              {alert.cameraId ?? 'Unknown'}
            </strong>
          </div>

          <div className="detail-row">
            <span>Source</span>
            <strong>
              {alert.source ?? 'Unknown'}
            </strong>
          </div>

          <div className="detail-row">
            <span>Severity</span>
            <strong>
              {alert.severity ?? 'Unknown'}
            </strong>
          </div>

          <div className="detail-row">
            <span>Status</span>
            <strong>
              {alert.status ?? 'Unknown'}
            </strong>
          </div>

          <div className="detail-row">
            <span>Body Zone</span>
            <strong>
              {alert.bodyZone ?? 'None'}
              {alert.bodyZoneId
                ? ` (${alert.bodyZoneId})`
                : ''}
            </strong>
          </div>

          <div className="detail-row">
            <span>Hand Zone</span>
            <strong>
              {alert.handZone ?? 'None'}
              {alert.handZoneId
                ? ` (${alert.handZoneId})`
                : ''}
            </strong>
          </div>

          <div className="detail-row">
            <span>Zone</span>
            <strong>
              {alert.zone ?? 'Unknown'}
            </strong>
          </div>

          <div className="detail-row">
            <span>Risk Score</span>
            <strong>
              {alert.riskScore != null
                ? `${Math.round(
                    alert.riskScore * 100
                  )}%`
                : 'Not provided'}
            </strong>
          </div>

          <div className="detail-row">
            <span>Timestamp</span>
            <strong>
              {alert.timestamp
                ? new Date(
                    alert.timestamp
                  ).toLocaleString()
                : 'Unknown'}
            </strong>
          </div>

          <div className="detail-row">
            <span>Dwell Time</span>
            <strong>
              {alert.dwellSeconds != null
                ? `${alert.dwellSeconds} sec`
                : 'Not applicable'}
            </strong>
          </div>

          <div className="detail-row detail-row--message">
            <span>Message</span>
            <p>
              {alert.message ??
                'No message available.'}
            </p>
          </div>

          {imageUrl ? (
            <div className="alert-image">
              <span>Incident Picture</span>

              <img
                src={imageUrl}
                alt="Safety incident"
              />
            </div>
          ) : (
            <div className="detail-row">
              <span>Incident Picture</span>
              <strong>
                Not available
              </strong>
            </div>
          )}

        </div>
      </div>
    </div>
  )
}