import React from 'react'
import { getApiBaseUrl } from '../api'

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

export default function Header({ view, onBack, hasSchedule, onViewSchedule, onOpenSettings, onOpenRoster, onOpenIndividualSchedules }) {
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
          onClick={onOpenRoster}
          className="text-slate-400 hover:text-white transition-colors"
          title="Physician Roster"
        >
          <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.8}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0zm6 3a2 2 0 11-4 0 2 2 0 014 0zM7 10a2 2 0 11-4 0 2 2 0 014 0z" />
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
    </header>
  )
}
