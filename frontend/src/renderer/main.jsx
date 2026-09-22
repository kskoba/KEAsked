import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import RosterEditor from './RosterEditor'
import PhysicianScheduleViewer from './PhysicianScheduleViewer'
import MonthlyRequestsPanel from './MonthlyRequestsPanel'
import SurveyResponsesViewer from './SurveyResponsesViewer'
import './styles/index.css'

const isRosterWindow = window.location.hash === '#roster'
const isScheduleViewerWindow = window.location.hash === '#schedule-viewer'
const isMonthlyRequestsWindow = window.location.hash === '#monthly-requests'
const isSurveyResponsesWindow = window.location.hash === '#survey-responses'

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    {isRosterWindow ? (
      <RosterEditor />
    ) : isScheduleViewerWindow ? (
      <PhysicianScheduleViewer />
    ) : isMonthlyRequestsWindow ? (
      <MonthlyRequestsPanel />
    ) : isSurveyResponsesWindow ? (
      <SurveyResponsesViewer />
    ) : (
      <App />
    )}
  </React.StrictMode>
)
