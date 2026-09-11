import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import RosterEditor from './RosterEditor'
import './styles/index.css'

const isRosterWindow = window.location.hash === '#roster'

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    {isRosterWindow ? <RosterEditor /> : <App />}
  </React.StrictMode>
)
