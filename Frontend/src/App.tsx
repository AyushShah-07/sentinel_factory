import { BrowserRouter, Route, Routes } from 'react-router-dom'
import { DataProvider } from './context/DataContext'
import { Sidebar } from './components/layout/Sidebar'
import { Topbar } from './components/layout/Topbar'
import { ScrollToTop } from './components/layout/ScrollToTop'
import { Overview } from './pages/Overview'
import { LiveMonitoring } from './pages/LiveMonitoring'
import { Events } from './pages/Events'
import { Alerts } from './pages/Alerts'
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
import './components/chat/SafetyChat.css'
import { SafetyChat } from './components/chat/SafetyChat'

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
              </Routes>
              <footer className="app-footer">
                Advisory monitoring system. Does not replace machine guarding, emergency stops, interlocks, or established safety procedures.
              </footer>
            </div>
            <SafetyChat />
          </div>
        </div>
      </DataProvider>
    </BrowserRouter>
  )
}

export default App
