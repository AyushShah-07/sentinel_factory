interface Props {
  label: string
  value: string | number
  detail?: string
}

export const MetricCard = ({ label, value, detail }: Props) => (
  <div className="metric-card">
    <div className="metric-card__label">{label}</div>
    <div className="metric-card__value">{value}</div>
    {detail ? <div className="metric-card__detail">{detail}</div> : null}
  </div>
)
