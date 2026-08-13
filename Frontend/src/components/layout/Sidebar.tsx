import { NavLink } from 'react-router-dom'
import './Sidebar.css'

const navItems = [
  { path: '/', label: 'Overview' },
  { path: '/live', label: 'Live Monitoring' },
  { path: '/events', label: 'Events' },
  { path: '/alerts', label: 'Alerts' },
  { path: '/zones', label: 'Safety Zones' },
  { path: '/devices', label: 'Devices' },
  { path: '/agent', label: 'AI Safety Agent' },
]

export const Sidebar = () => {
  return (
    <aside className="sidebar">
      <div className="sidebar__brand">
        <div>
          <span className="sidebar__title">SENTINEL FACTORY</span>
          <span className="sidebar__subtitle">AI SAFETY MONITORING</span>
        </div>
      </div>

      <nav className="sidebar__nav">
        {navItems.map((item) => (
          <NavLink
            key={item.path}
            to={item.path}
            className={({ isActive }) => (isActive ? 'sidebar__link active' : 'sidebar__link')}
            end={item.path === '/'}
          >
            {item.label}
          </NavLink>
        ))}
      </nav>

      <div className="sidebar__footer">
        <div>
          <span className="sidebar__footer-label">Backend</span>
          <span className="sidebar__footer-status status-safe">Unknown</span>
        </div>
        <div>
          <span className="sidebar__footer-label">MQTT</span>
          <span className="sidebar__footer-status status-warning">Unknown</span>
        </div>
      </div>
    </aside>
  )
}
