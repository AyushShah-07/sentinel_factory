import './LiveVideoPlayer.css'

interface LiveVideoPlayerProps {
  streamUrl?: string
}

export const LiveVideoPlayer = ({
  streamUrl = 'http://localhost:8000/video',
}: LiveVideoPlayerProps) => {
  return (
    <section className="live-player">
      <div className="live-player__header">
        <div>
          <span>Live camera stream</span>
          <p>Zone X real-time monitoring</p>
        </div>

        <span className="live-player__status">
          ● LIVE
        </span>
      </div>

      <div className="live-player__body">
        {streamUrl ? (
          <img
            src={streamUrl}
            alt="Zone X live camera stream"
            className="live-player__video"
          />
        ) : (
          <div className="live-player__placeholder">
            <span>Live camera stream unavailable</span>
          </div>
        )}
      </div>
    </section>
  )
}