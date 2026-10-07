import React, { useEffect, useState } from 'react'
import { loadTrailingSchedule, getTrailingSchedule, clearTrailingSchedule } from '../api'

const MONTHS = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December'
]

function previousMonth(year, month) {
  return month === 1 ? { year: year - 1, month: 12 } : { year, month: month - 1 }
}

// Remember what the user typed per previous-month, so reopening the app
// for the same solve doesn't mean re-pasting the link. Browser storage can
// be unavailable (private window etc.) -- every access is guarded.
function storageKey(prev) {
  return `keasked.previousMonth.${prev.year}-${String(prev.month).padStart(2, '0')}`
}
function readSaved(prev) {
  try {
    const raw = window.localStorage.getItem(storageKey(prev))
    return raw ? JSON.parse(raw) : null
  } catch {
    return null
  }
}
function writeSaved(prev, data) {
  try { window.localStorage.setItem(storageKey(prev), JSON.stringify(data)) } catch { /* ignore */ }
}

/**
 * "Previous month" inputs for cross-month continuity: a master-sheet link
 * for the prior month's finalized schedule plus (optionally) the folder of
 * that month's request files. Posts to /api/trailing-schedule; the next
 * Generate picks it up automatically. Entirely separate from the active
 * month's import/load state.
 *
 * Props:
 *   year, month   the month about to be generated (1-based month)
 *   disabled      true while a solve is running
 *   remote        backend is not on this machine -> paths are typed, not browsed
 */
export default function PreviousMonthPanel({ year, month, disabled, remote }) {
  const prev = previousMonth(year, month)
  const prevLabel = `${MONTHS[prev.month - 1]} ${prev.year}`

  const [sheetUrl, setSheetUrl] = useState('')
  const [requestsDir, setRequestsDir] = useState('')
  const [status, setStatus] = useState(null)      // TrailingScheduleStatus | null
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [open, setOpen] = useState(false)

  // Restore saved inputs whenever the target month changes, and refresh
  // what the backend currently has loaded.
  useEffect(() => {
    const saved = readSaved(prev)
    setSheetUrl(saved?.sheetUrl || '')
    setRequestsDir(saved?.requestsDir || '')
    setError(null)
    getTrailingSchedule().then(setStatus).catch(() => setStatus(null))
  }, [prev.year, prev.month])

  const loadedMatches = status?.loaded && status.year === prev.year && status.month === prev.month
  const loadedOtherMonth = status?.loaded && !loadedMatches

  async function handleBrowse() {
    let selected = null
    if (window.electronAPI) {
      selected = await window.electronAPI.openDirectory()
    } else {
      selected = prompt('Enter the previous month\'s requests directory path:')
    }
    if (selected) setRequestsDir(selected)
  }

  async function handleLoad() {
    if (!sheetUrl.trim()) return
    setBusy(true)
    setError(null)
    try {
      const body = { sheet_url: sheetUrl.trim(), year: prev.year, month: prev.month }
      if (requestsDir.trim()) body.preferences_directory = requestsDir.trim()
      const result = await loadTrailingSchedule(body)
      setStatus(result)
      writeSaved(prev, { sheetUrl: sheetUrl.trim(), requestsDir: requestsDir.trim() })
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  async function handleClear() {
    setBusy(true)
    setError(null)
    try {
      setStatus(await clearTrailingSchedule())
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  const inputCls = 'w-full px-3 py-1.5 rounded border border-slate-300 bg-white text-slate-700 text-sm focus:outline-none focus:ring-2 focus:ring-sky-400 disabled:opacity-50'

  return (
    <div className="mb-5 p-4 bg-slate-50 border border-slate-200 rounded-lg">
      <div className="flex items-center justify-between gap-3">
        <button
          type="button"
          onClick={() => setOpen(o => !o)}
          className="flex items-center gap-2 text-sm font-medium text-slate-700 hover:text-slate-900"
        >
          <svg className={`w-4 h-4 text-slate-400 transition-transform ${open ? 'rotate-90' : ''}`} fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
          </svg>
          Previous month ({prevLabel})
          <span className="text-slate-400 font-normal">— rest rules across the month boundary &amp; carry-over</span>
        </button>
        <StatusBadge status={status} loadedMatches={loadedMatches} loadedOtherMonth={loadedOtherMonth} />
      </div>

      {open && (
        <div className="mt-4 space-y-3">
          <div>
            <label className="block text-xs font-medium text-slate-600 mb-1">
              {prevLabel} master schedule — Google Sheet link
            </label>
            <input
              type="text"
              value={sheetUrl}
              onChange={e => setSheetUrl(e.target.value)}
              disabled={disabled || busy}
              placeholder="https://docs.google.com/spreadsheets/d/…"
              spellCheck={false}
              className={inputCls}
            />
            <p className="mt-1 text-xs text-slate-500">
              Without Google credentials on the backend, the sheet must be shared as “anyone with the link can view”.
            </p>
          </div>

          <div>
            <label className="block text-xs font-medium text-slate-600 mb-1">
              {prevLabel} requests folder
              <span className="ml-1 text-slate-400 font-normal">(optional — needed for the “went over requested count” carry-over)</span>
            </label>
            <div className="flex gap-2">
              <input
                type="text"
                readOnly={!remote}
                value={requestsDir}
                onChange={remote ? (e) => setRequestsDir(e.target.value) : undefined}
                disabled={disabled || busy}
                placeholder={remote ? 'Type the path as it exists on the remote backend…' : 'Select the folder of request .xlsx files…'}
                className={`${inputCls} ${remote ? '' : 'cursor-default'}`}
              />
              {!remote && (
                <button
                  type="button"
                  onClick={handleBrowse}
                  disabled={disabled || busy}
                  className="px-3 py-1.5 bg-white hover:bg-slate-100 disabled:opacity-50 text-slate-700 text-sm font-medium rounded border border-slate-300 transition-colors"
                >
                  Browse…
                </button>
              )}
              {requestsDir && (
                <button
                  type="button"
                  onClick={() => setRequestsDir('')}
                  disabled={disabled || busy}
                  title="Clear folder"
                  className="px-2 py-1.5 text-slate-400 hover:text-slate-600 text-sm"
                >
                  ✕
                </button>
              )}
            </div>
          </div>

          {error && (
            <div className="p-3 bg-red-50 border border-red-200 rounded-md text-red-700 text-sm">
              {error}
            </div>
          )}

          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={handleLoad}
              disabled={disabled || busy || !sheetUrl.trim()}
              className="px-4 py-1.5 bg-sky-600 hover:bg-sky-500 disabled:bg-slate-300 disabled:cursor-not-allowed text-white text-sm font-medium rounded-md transition-colors"
            >
              {busy ? 'Loading…' : loadedMatches ? 'Reload previous month' : 'Load previous month'}
            </button>
            {status?.loaded && (
              <button
                type="button"
                onClick={handleClear}
                disabled={disabled || busy}
                className="px-3 py-1.5 bg-white hover:bg-slate-100 disabled:opacity-50 text-slate-600 text-sm font-medium rounded-md border border-slate-300 transition-colors"
              >
                Clear
              </button>
            )}
          </div>

          {status?.loaded && <LoadedSummary status={status} loadedOtherMonth={loadedOtherMonth} />}
          {status?.last_used && (
            <p className="text-xs text-slate-500">
              <span className="font-medium text-slate-600">Last generate:</span> {status.last_used}
            </p>
          )}
        </div>
      )}
    </div>
  )
}

function StatusBadge({ status, loadedMatches, loadedOtherMonth }) {
  if (!status?.loaded) {
    return <span className="text-xs px-2 py-0.5 rounded-full bg-slate-200 text-slate-600">not loaded</span>
  }
  if (loadedOtherMonth) {
    return (
      <span className="text-xs px-2 py-0.5 rounded-full bg-amber-100 text-amber-800" title="Loaded month is not the one before the month being generated — it will be ignored.">
        wrong month loaded
      </span>
    )
  }
  return (
    <span className="text-xs px-2 py-0.5 rounded-full bg-emerald-100 text-emerald-800">
      loaded · {status.physician_count} physicians
    </span>
  )
}

function LoadedSummary({ status, loadedOtherMonth }) {
  const monthLabel = `${MONTHS[status.month - 1]} ${status.year}`
  const sourceLabel = {
    xlsx: 'exported xlsx',
    google_sheets: 'master sheet (Drive folder)',
    google_sheets_link: 'master sheet link (credentials)',
    google_sheets_public_link: 'master sheet link (public export)',
  }[status.source] || status.source
  return (
    <div className={`text-xs rounded-md border p-3 space-y-1 ${loadedOtherMonth ? 'bg-amber-50 border-amber-200 text-amber-900' : 'bg-white border-slate-200 text-slate-600'}`}>
      <div>
        <span className="font-medium">{monthLabel}</span> from {sourceLabel}: {status.assignment_count} shifts across {status.physician_count} physicians.
        {loadedOtherMonth && ' This is not the month before the one being generated, so it will be ignored.'}
      </div>
      <div>
        <span className="font-medium">Requested counts:</span>{' '}
        {status.requested_known_count > 0
          ? `known for ${status.requested_known_count} physicians (${status.requests_source === 'sked' ? 'from sked' : 'from the requests folder'})`
          : 'not loaded — the “went over requested” carry-over is off until a requests folder is provided'}
      </div>
      <div>
        <span className="font-medium">Acute-shift push this month:</span>{' '}
        {status.acute_debt_physicians?.length ? status.acute_debt_physicians.join(', ') : 'nobody'}
      </div>
      <div>
        <span className="font-medium">Discouraged from going over again:</span>{' '}
        {status.overage_physicians?.length ? status.overage_physicians.join(', ') : 'nobody'}
      </div>
    </div>
  )
}
