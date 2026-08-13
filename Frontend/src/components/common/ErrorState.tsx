interface ErrorStateProps {
  message: string
  onRetry?: () => void
}

export const ErrorState = ({ message, onRetry }: ErrorStateProps) => (
  <div className="error-state">
    <div>{message}</div>
    {onRetry ? (
      <button type="button" onClick={onRetry}>
        Retry
      </button>
    ) : null}
  </div>
)
