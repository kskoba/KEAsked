import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import RosterEditor from './RosterEditor'
import PhysicianScheduleViewer from './PhysicianScheduleViewer'
import './styles/index.css'

const isRosterWindow = window.location.hash === '#roster'
const isScheduleViewerWindow = window.location.hash === '#schedule-viewer'

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    {isRosterWindow ? <RosterEditor /> : isScheduleViewerWindow ? <PhysicianScheduleViewer /> : <App />}
  </React.StrictMode>
)
