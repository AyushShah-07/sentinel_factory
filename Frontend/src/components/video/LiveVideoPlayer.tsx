import './LiveVideoPlayer.css'

interface LiveVideoPlayerProps {
  streamUrl?: string
}

export const LiveVideoPlayer = ({ streamUrl }: LiveVideoPlayerProps) => {
  return (
    <section className="live-player">
      <div className="live-player__header">
        <div>
          <span>Live camera stream</span>
          <p>Stream integration pending</p>
        </div>
      </div>
      <div className="live-player__body">
        <div className="live-player__placeholder">
          <span>Live camera stream integration pending</span>
          {streamUrl ? <small>Configured URL: {streamUrl}</small> : null}
        </div>
      </div>
    </section>
  )
}
