interface EmptyStateProps {
  message: string
}

export const EmptyState = ({ message }: EmptyStateProps) => (
  <div className="empty-state">{message}</div>
)
