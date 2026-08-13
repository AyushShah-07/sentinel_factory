interface LoadingStateProps {
  message?: string
}

export const LoadingState = ({ message = 'Loading...' }: LoadingStateProps) => (
  <div className="loading-state">
    <div className="loading-state__spinner" />
    <div>{message}</div>
  </div>
)
