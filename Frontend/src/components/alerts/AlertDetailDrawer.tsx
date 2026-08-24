import { useState } from 'react'
import type { AlertRecord } from '../../types'
import './AlertDetailDrawer.css'

interface Props {
  alert: AlertRecord
  onClose: () => void
}

export const AlertDetailDrawer = ({ alert, onClose }: Props) => {
  const [zoomLevel, setZoomLevel] = useState(1)
  const [showFullscreen, setShowFullscreen] = useState(false)

  const imageUrl = alert.picture
    ? `http://localhost:8080/api/alerts/${alert.id}/image`
    : null

  const isWarning = alert.incident_type?.toUpperCase() === 'WARNING'

  // Zoom functions
  const zoomIn = () => setZoomLevel((prev) => Math.min(prev + 0.5, 3))
  const zoomOut = () => setZoomLevel((prev) => Math.max(prev - 0.5, 0.5))
  const resetZoom = () => setZoomLevel(1)

  return (
    <div className="drawer-overlay" onClick={onClose}>
      <div className="drawer" onClick={(e) => e.stopPropagation()}>
        <div className="drawer__header">
          <div>
            <h3>Alert Details</h3>
            <span>{isWarning ? 'WARNING INCIDENT' : 'DANGER INCIDENT'}</span>
          </div>
          <button type="button" onClick={onClose}>Close</button>
        </div>

        <div className="drawer__content">
          {/* All detail rows here (same as before) */}
          <div className="detail-row">
            <span>Alert ID</span>
            <strong>{alert.id}</strong>
          </div>
          
          <div className="detail-row">
            <span>Incident Type</span>
            <strong>{alert.incident_type ?? 'Unknown'}</strong>
          </div>
          
          <div className="detail-row">
            <span>Person</span>
            <strong>{alert.person_id ?? 'Unknown'}</strong>
          </div>
          
          <div className="detail-row">
            <span>Zone</span>
            <strong>{alert.zone_name ?? 'Unknown'}</strong>
          </div>
          
          <div className="detail-row">
            <span>Dwell Time</span>
            <strong>
              {alert.dwell_seconds != null ? `${alert.dwell_seconds} sec` : 'Not applicable'}
            </strong>
          </div>

          {/* Picture with Zoom */}
          {imageUrl && (
            <div className="alert-image">
              <span>Incident Picture</span>
              
              {/* Zoom Buttons */}
              <div style={{ display: 'flex', gap: '8px', marginBottom: '8px' }}>
                <button
                  type="button"
                  onClick={zoomOut}
                  style={{
                    padding: '8px 12px',
                    background: '#1f2830',
                    color: 'white',
                    border: '1px solid #29333d',
                    borderRadius: '6px',
                    cursor: 'pointer',
                    fontSize: '18px',
                  }}
                >
                  −
                </button>
                
                <span style={{ color: '#7d8a9a', alignSelf: 'center', minWidth: '40px', textAlign: 'center' }}>
                  {Math.round(zoomLevel * 100)}%
                </span>
                
                <button
                  type="button"
                  onClick={zoomIn}
                  style={{
                    padding: '8px 12px',
                    background: '#1f2830',
                    color: 'white',
                    border: '1px solid #29333d',
                    borderRadius: '6px',
                    cursor: 'pointer',
                    fontSize: '18px',
                  }}
                >
                  +
                </button>
                
                <button
                  type="button"
                  onClick={resetZoom}
                  style={{
                    padding: '8px 12px',
                    background: '#1f2830',
                    color: '#9cb3c7',
                    border: '1px solid #29333d',
                    borderRadius: '6px',
                    cursor: 'pointer',
                    fontSize: '12px',
                  }}
                >
                  Reset
                </button>
                
                <button
                  type="button"
                  onClick={() => setShowFullscreen(true)}
                  style={{
                    padding: '8px 12px',
                    background: '#1f2830',
                    color: '#9cb3c7',
                    border: '1px solid #29333d',
                    borderRadius: '6px',
                    cursor: 'pointer',
                    fontSize: '12px',
                  }}
                >
                  ⛶ Fullscreen
                </button>
              </div>

              {/* Image with Zoom */}
              <div
                style={{
                  overflow: 'auto',
                  maxHeight: '400px',
                  border: '1px solid #29333d',
                  borderRadius: '8px',
                  background: '#05080b',
                }}
              >
                <img
                  src={imageUrl}
                  alt="Incident"
                  style={{
                    width: '100%',
                    transform: `scale(${zoomLevel})`,
                    transformOrigin: 'top left',
                    transition: 'transform 0.2s ease',
                  }}
                />
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Fullscreen Modal */}
      {showFullscreen && imageUrl && (
        <div
          onClick={() => setShowFullscreen(false)}
          style={{
            position: 'fixed',
            inset: 0,
            background: 'rgba(0,0,0,0.95)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            zIndex: 100,
            cursor: 'zoom-out',
          }}
        >
          <button
            onClick={() => setShowFullscreen(false)}
            style={{
              position: 'absolute',
              top: '20px',
              right: '20px',
              padding: '10px 16px',
              background: '#1f2830',
              color: 'white',
              border: 'none',
              borderRadius: '8px',
              cursor: 'pointer',
            }}
          >
            Close ✕
          </button>
          
          <img
            src={imageUrl}
            alt="Fullscreen incident"
            style={{
              maxWidth: '90%',
              maxHeight: '90%',
              objectFit: 'contain',
            }}
          />
        </div>
      )}
    </div>
  )
}