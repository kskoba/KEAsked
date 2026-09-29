import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import RosterEditor from './RosterEditor'
import PhysicianScheduleViewer from './PhysicianScheduleViewer'
import MonthlyRequestsPanel from './MonthlyRequestsPanel'
import SurveyResponsesViewer from './SurveyResponsesViewer'
import ByteBlocPanel from './ByteBlocPanel'
import SchedulingRulesPanel from './SchedulingRulesPanel'
import './styles/index.css'

const isRosterWindow = window.location.hash === '#roster'
const isScheduleViewerWindow = window.location.hash === '#schedule-viewer'
const isMonthlyRequestsWindow = window.location.hash === '#monthly-requests'
const isSurveyResponsesWindow = window.location.hash === '#survey-responses'
const isByteBlocWindow = window.location.hash === '#bytebloc'
const isSchedulingRulesWindow = window.location.hash === '#scheduling-rules'

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
    ) : isByteBlocWindow ? (
      <ByteBlocPanel />
    ) : isSchedulingRulesWindow ? (
      <SchedulingRulesPanel />
    ) : (
      <App />
    )}
  </React.StrictMode>
)
