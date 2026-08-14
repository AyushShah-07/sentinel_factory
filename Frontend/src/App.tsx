import { BrowserRouter, Route, Routes } from 'react-router-dom'
import { DataProvider } from './context/DataContext'
import { Sidebar } from './components/layout/Sidebar'
import { Topbar } from './components/layout/Topbar'
import { ScrollToTop } from './components/layout/ScrollToTop'
import { Overview } from './pages/Overview'
import { LiveMonitoring } from './pages/LiveMonitoring'
import { Events } from './pages/Events'
import { Alerts } from './pages/Alerts'
import { SafetyZones } from './pages/SafetyZones'
import { Devices } from './pages/Devices'
import { SafetyAgent } from './pages/SafetyAgent'
import './App.css'
import './components/common/LoadingState.css'
import './components/common/ErrorState.css'
import './components/common/EmptyState.css'
import './components/common/StatusBadge.css'
import './components/layout/Sidebar.css'
import './components/layout/Topbar.css'
import './components/dashboard/SafetyStatus.css'
import './components/dashboard/MetricCard.css'
import './components/dashboard/RecentEvents.css'
import './components/dashboard/RecentAlerts.css'
import './components/video/LiveVideoPlayer.css'
import './components/alerts/AlertDetailDrawer.css'
import './pages/SafetyAgent.css'

function App() {
  return (
    <BrowserRouter>
      <DataProvider>
        <div className="app-shell">
          <Sidebar />
          <div className="app-main">
            <ScrollToTop />
            <Topbar />
            <div className="page-content">
              <Routes>
                <Route path="/" element={<Overview />} />
                <Route path="/live" element={<LiveMonitoring />} />
                <Route path="/events" element={<Events />} />
                <Route path="/alerts" element={<Alerts />} />
                <Route path="/zones" element={<SafetyZones />} />
                <Route path="/devices" element={<Devices />} />
                <Route path="/agent" element={<SafetyAgent />} />
              </Routes>
              <footer className="app-footer">
                Advisory monitoring system. Does not replace machine guarding, emergency stops, interlocks, or established safety procedures.
              </footer>
            </div>
          </div>
        </div>
      </DataProvider>
    </BrowserRouter>
  )
}

export default App
