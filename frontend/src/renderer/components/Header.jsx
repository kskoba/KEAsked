import React from 'react'

async function downloadExport() {
  const res = await fetch('http://127.0.0.1:5000/api/export')
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

export default function Header({ view, onBack }) {
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

        <span className="text-slate-500 text-xs">
          {view === 'setup' ? 'Setup & Import' : 'Schedule View'}
        </span>
      </div>
    </header>
  )
}
