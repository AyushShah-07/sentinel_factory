import './StatusBadge.css'

interface Props {
  label: string
  tone: 'safe' | 'warning' | 'critical' | 'info'
}

export const StatusBadge = ({ label, tone }: Props) => {
  return <span className={`status-badge status-badge--${tone}`}>{label}</span>
}
