import React, { useState, useEffect } from 'react'
import { getApiBaseUrl, getGoogleSheetsStatus, pushToMasterSheet } from '../api'

async function downloadExport() {
  const res = await fetch(`${getApiBaseUrl()}/api/export`)
  if (!res.ok) return
  const blob = await res.blob()
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  const disposition = res.headers.get('Content-Disposition') || ''
  const match = disposition.match(/filename="([^"]+)"/)
  a.href = url
  a.download = match ? match[1] : 'schedule.xlsx'
  a.click()
  URL.revokeObjectURL(url)
}

function PushToMasterSheetModal({ onClose }) {
  const [confirmText, setConfirmText] = useState('')
  const [pushing, setPushing] = useState(false)
  const [result, setResult] = useState(null) // null | { sheet, sked, spreadsheet_url }
  const [error, setError] = useState(null)
  const canPush = !pushing && !result && confirmText === 'CONFIRM'

  async function handlePush() {
    setPushing(true)
    setError(null)
    try {
      const res = await pushToMasterSheet(confirmText)
      setResult(res)
    } catch (err) {
      setError(err.message)
    } finally {
      setPushing(false)
    }
  }

  function ResultRow({ label, detail }) {
    return (
      <div className={`text-sm rounded-md px-3 py-2 border ${
        detail.ok
          ? 'text-emerald-800 bg-emerald-50 border-emerald-200'
          : 'text-red-800 bg-red-50 border-red-200'
      }`}>
        <div className="font-medium mb-0.5">{label}: {detail.ok ? 'OK' : 'Failed'}</div>
        <div className="text-xs">{detail.detail}</div>
      </div>
    )
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40" onClick={onClose}>
      <div
        className="bg-white rounded-xl shadow-xl border border-slate-200 w-full max-w-lg mx-4"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-5 py-4 border-b border-slate-200">
          <h2 className="font-semibold text-slate-800 text-sm">Push to Master Sheet</h2>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-600 text-lg leading-none">✕</button>
        </div>

        <div className="px-5 py-4 space-y-4">
          {error && <p className="text-sm text-red-600">{error}</p>}

          {!result && (
            <>
              <p className="text-sm text-slate-700">
                This writes the active schedule's physician assignments into the
                department's real shared Google Sheet for this month, and pushes
                the same schedule to every physician's personal calendar feed.
                It does not touch learner (resident) pairing cells. This cannot
                be undone from here. Type <span className="font-mono font-semibold">CONFIRM</span> below to proceed.
              </p>
              <input
                type="text"
                value={confirmText}
                onChange={(e) => setConfirmText(e.target.value)}
                placeholder="Type CONFIRM"
                className="w-full px-3 py-2 text-sm border border-slate-300 rounded-md focus:outline-none focus:ring-2 focus:ring-emerald-500"
              />
            </>
          )}

          {result && (
            <div className="space-y-2">
              <ResultRow label="Master Sheet" detail={result.sheet} />
              <ResultRow label="sked calendar feed" detail={result.sked} />
              {result.spreadsheet_url && (
                <a
                  href={result.spreadsheet_url}
                  target="_blank"
                  rel="noreferrer"
                  className="text-sm text-sky-600 hover:underline block"
                >
                  Open the spreadsheet →
                </a>
              )}
            </div>
          )}
        </div>

        <div className="flex justify-end gap-3 px-5 py-3 border-t border-slate-200">
          <button
            onClick={onClose}
            className="px-4 py-2 text-sm font-medium text-slate-700 bg-white border border-slate-300 rounded-md hover:bg-slate-50 transition-colors"
          >
            {result ? 'Close' : 'Cancel'}
          </button>
          {!result && (
            <button
              onClick={handlePush}
              disabled={!canPush}
              className="px-4 py-2 text-sm font-medium text-white bg-emerald-600 rounded-md hover:bg-emerald-700 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
            >
              {pushing ? 'Pushing…' : 'Push'}
            </button>
          )}
        </div>
      </div>
    </div>
  )
}

export default function Header({ view, onBack, hasSchedule, onViewSchedule, onOpenSettings, onOpenRoster, onOpenIndividualSchedules, onOpenMonthlyRequests, onOpenSchedulingRules }) {
  const [sheetsConfigured, setSheetsConfigured] = useState(null) // null while checking
  const [showPushModal, setShowPushModal] = useState(false)

  useEffect(() => {
    getGoogleSheetsStatus()
      .then((s) => setSheetsConfigured(s.configured))
      .catch(() => setSheetsConfigured(false))
  }, [])
  return (
    <header
      className="flex items-center justify-between px-6 py-3 shadow-md flex-shrink-0"
      style={{ backgroundColor: '#1e293b' }}
    >
      <div className="flex items-center gap-3">
        {/* Simple calendar icon */}
        <svg
          className="w-7 h-7 text-sky-400"
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
          strokeWidth={1.8}
        >
          <rect x="3" y="4" width="18" height="18" rx="2" ry="2" />
          <line x1="16" y1="2" x2="16" y2="6" />
          <line x1="8" y1="2" x2="8" y2="6" />
          <line x1="3" y1="10" x2="21" y2="10" />
        </svg>

        <div>
          <h1 className="text-white font-bold text-lg leading-tight">
            KEA Physician Scheduler
          </h1>
          <p className="text-slate-400 text-xs">
            Emergency Department Shift Management
          </p>
        </div>
      </div>

      <div className="flex items-center gap-4">
        {view === 'schedule' && (
          <div className="flex items-center gap-2">
            <button
              onClick={downloadExport}
              className="flex items-center gap-2 px-4 py-1.5 rounded-md bg-emerald-700 hover:bg-emerald-600 text-white text-sm transition-colors"
              title="Export schedule as Excel"
            >
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M4 16v2a2 2 0 002 2h12a2 2 0 002-2v-2M7 10l5 5 5-5M12 15V3" />
              </svg>
              Export .xlsx
            </button>
            <button
              onClick={() => setShowPushModal(true)}
              disabled={!sheetsConfigured}
              className="flex items-center gap-2 px-4 py-1.5 rounded-md bg-emerald-700 hover:bg-emerald-600 text-white text-sm transition-colors disabled:opacity-40 disabled:cursor-not-allowed disabled:hover:bg-emerald-700"
              title={sheetsConfigured ? 'Push the active schedule to the master Google Sheet' : 'Master Sheet not configured — see scheduler/config/google_sheets_template.yaml'}
            >
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M12 12v9m0-9l-3 3m3-3l3 3" />
              </svg>
              Push to Master Sheet
            </button>
            <button
              onClick={onOpenIndividualSchedules}
              className="flex items-center gap-2 px-4 py-1.5 rounded-md bg-slate-700 hover:bg-slate-600 text-slate-200 text-sm transition-colors"
              title="View each physician's individual monthly schedule"
            >
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z" />
              </svg>
              Individual Schedules
            </button>
            <button
              onClick={onBack}
              className="flex items-center gap-2 px-4 py-1.5 rounded-md bg-slate-700 hover:bg-slate-600 text-slate-200 text-sm transition-colors"
            >
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M15 19l-7-7 7-7" />
              </svg>
              Back to Setup
            </button>
          </div>
        )}
        {view === 'setup' && hasSchedule && (
          <button
            onClick={onViewSchedule}
            className="flex items-center gap-2 px-4 py-1.5 rounded-md bg-sky-700 hover:bg-sky-600 text-white text-sm transition-colors"
          >
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
            </svg>
            View Schedule
          </button>
        )}

        <span className="text-slate-500 text-xs">
          {view === 'setup' ? 'Setup & Import' : 'Schedule View'}
        </span>

        <button
          onClick={onOpenMonthlyRequests}
          className="text-slate-400 hover:text-white transition-colors"
          title="Send Monthly Shift Requests"
        >
          <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.8}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M3 8l7.89 5.26a2 2 0 002.22 0L21 8M5 19h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" />
          </svg>
        </button>

        <button
          onClick={onOpenRoster}
          className="text-slate-400 hover:text-white transition-colors"
          title="Physician Roster"
        >
          <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.8}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0zm6 3a2 2 0 11-4 0 2 2 0 014 0zM7 10a2 2 0 11-4 0 2 2 0 014 0z" />
          </svg>
        </button>

        <button
          onClick={onOpenSchedulingRules}
          className="text-slate-400 hover:text-white transition-colors"
          title="Scheduling Rules (read-only)"
        >
          <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.8}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
          </svg>
        </button>

        <button
          onClick={onOpenSettings}
          className="text-slate-400 hover:text-white transition-colors"
          title="Settings"
        >
          <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.8}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
            <circle cx="12" cy="12" r="3" />
          </svg>
        </button>
      </div>

      {showPushModal && <PushToMasterSheetModal onClose={() => setShowPushModal(false)} />}
    </header>
  )
}
