import React, { useState, useEffect, useMemo } from 'react'
import { getSchedule, setApiBaseUrl } from './api'

const WEEKDAY_LABELS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']

function daysInMonth(year, month) {
  return new Date(year, month, 0).getDate()
}

function firstWeekday(year, month) {
  return new Date(year, month - 1, 1).getDay()
}

// Assignment/on-call dates are "YYYY-MM-DD" strings — parsed manually
// (not via `new Date(iso)`) since that parses as UTC and can shift the
// day backward once rendered in a local timezone west of UTC.
function dayOfMonth(iso) {
  return Number(iso.split('-')[2])
}

export default function PhysicianScheduleViewer() {
  const [apiBaseResolved, setApiBaseResolved] = useState(false)
  const [schedule, setSchedule] = useState(null)
  const [loadError, setLoadError] = useState(null)
  const [selectedId, setSelectedId] = useState(null)
  const [search, setSearch] = useState('')

  // This is a separate BrowserWindow from the main app window (see
  // main/index.js's scheduleViewerWindow) — a fresh renderer with its own
  // JS module state, so api.js's BASE_URL here starts back at its
  // hardcoded default (127.0.0.1:5000) regardless of what the main window
  // resolved. Must re-resolve the backend location here too (same fix as
  // RosterEditor.jsx), or this window silently talks to the local backend
  // even when the app is configured for a remote one.
  useEffect(() => {
    let cancelled = false
    window.electronAPI.getBackendConfig()
      .then(({ effectiveUrl }) => { if (!cancelled) setApiBaseUrl(effectiveUrl) })
      .finally(() => { if (!cancelled) setApiBaseResolved(true) })
    return () => { cancelled = true }
  }, [])

  useEffect(() => {
    if (!apiBaseResolved) return
    getSchedule()
      .then(data => setSchedule(data))
      .catch(err => setLoadError(err.message))
  }, [apiBaseResolved])

  // {physician_id: {id, name, byDay: {day: [{kind:'shift'|'call', label}]}}}
  const physicianData = useMemo(() => {
    if (!schedule) return {}
    const map = {}
    const ensure = (id, name) => {
      if (!map[id]) map[id] = { id, name, byDay: {} }
      return map[id]
    }
    for (const a of schedule.assignments || []) {
      const day = dayOfMonth(a.date)
      const entry = ensure(a.physician_id, a.physician_name)
      if (!entry.byDay[day]) entry.byDay[day] = []
      // Site (e.g. "RAH A side" -> "RAH A") is the actual shift being
      // worked; time alone ("0600h") doesn't say which one.
      entry.byDay[day].push({
        kind: 'shift',
        site: a.shift.site.replace(/ side$/, ''),
        time: a.shift.time,
        is2400: a.shift.time === '2400h',
        is0600: a.shift.time === '0600h',
      })
    }
    for (const c of schedule.on_calls || []) {
      const day = dayOfMonth(c.date)
      const entry = ensure(c.physician_id, c.physician_name)
      if (!entry.byDay[day]) entry.byDay[day] = []
      entry.byDay[day].push({ kind: 'call', label: c.call_type })
    }
    for (const [pid, req] of Object.entries(schedule.requested || {})) {
      const entry = map[pid]
      if (entry) entry.requested = req
    }
    return map
  }, [schedule])

  const physicianList = useMemo(() => {
    return Object.values(physicianData)
      .map(p => ({
        ...p,
        shiftCount: Object.values(p.byDay).reduce(
          (n, entries) => n + entries.filter(e => e.kind === 'shift').length, 0
        )
      }))
      .sort((a, b) => a.name.localeCompare(b.name))
  }, [physicianData])

  useEffect(() => {
    if (!selectedId && physicianList.length > 0) setSelectedId(physicianList[0].id)
  }, [physicianList, selectedId])

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase()
    if (!q) return physicianList
    return physicianList.filter(p => p.name.toLowerCase().includes(q) || p.id.toLowerCase().includes(q))
  }, [physicianList, search])

  const selected = (selectedId && physicianData[selectedId]) || null

  function handleCloseClick() {
    window.electronAPI.forceCloseSelf()
  }

  if (!apiBaseResolved || (loadError === null && schedule === null)) {
    return <div className="h-screen flex items-center justify-center bg-slate-50 text-slate-400 text-sm">Loading schedule…</div>
  }

  if (loadError) {
    const noSchedule = loadError.toLowerCase().includes('no schedule generated')
    return (
      <div className="h-screen flex items-center justify-center bg-slate-50">
        <div className="text-center max-w-md">
          <p className={`font-medium mb-2 ${noSchedule ? 'text-slate-600' : 'text-red-600'}`}>
            {noSchedule ? 'No schedule generated yet' : "Couldn't load the schedule"}
          </p>
          <p className="text-sm text-slate-500">
            {noSchedule ? 'Generate a schedule from the Setup screen first, then reopen this window.' : loadError}
          </p>
        </div>
      </div>
    )
  }

  const monthLabel = new Date(schedule.year, schedule.month - 1, 1)
    .toLocaleString('default', { month: 'long', year: 'numeric' })

  return (
    <div className="h-screen flex flex-col bg-slate-50">
      <header className="flex items-center justify-between px-5 py-3 shadow-md flex-shrink-0" style={{ backgroundColor: '#1e293b' }}>
        <div>
          <h1 className="text-white font-bold text-base leading-tight">Individual Schedules</h1>
          <p className="text-slate-400 text-xs">{monthLabel} — {physicianList.length} physicians scheduled</p>
        </div>
        <button
          onClick={handleCloseClick}
          title="Close"
          className="w-7 h-7 flex items-center justify-center rounded-full bg-red-600 hover:bg-red-500 text-white text-sm font-bold transition-colors"
        >
          ✕
        </button>
      </header>

      <div className="flex flex-1 overflow-hidden">
        {/* List pane */}
        <div className="w-72 flex-shrink-0 border-r border-slate-200 bg-white flex flex-col">
          <div className="p-3 border-b border-slate-200">
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search…"
              className="w-full px-3 py-1.5 text-sm border border-slate-300 rounded-md focus:outline-none focus:ring-2 focus:ring-sky-500"
            />
          </div>
          <div className="flex-1 overflow-auto">
            {filtered.map(p => (
              <button
                key={p.id}
                onClick={() => setSelectedId(p.id)}
                className={`w-full text-left px-3 py-2 text-sm border-b border-slate-100 transition-colors flex items-center justify-between gap-2 ${
                  p.id === selectedId ? 'bg-sky-50 text-sky-800 font-medium' : 'text-slate-700 hover:bg-slate-50'
                }`}
              >
                <span>{p.name}</span>
                <span className="text-xs text-slate-400 font-mono flex-shrink-0">{p.shiftCount}</span>
              </button>
            ))}
            {filtered.length === 0 && (
              <p className="text-sm text-slate-400 px-3 py-4">No physicians match &quot;{search}&quot;.</p>
            )}
          </div>
        </div>

        {/* Calendar pane */}
        <div className="flex-1 overflow-auto p-6">
          {selected ? (
            <MonthGrid year={schedule.year} month={schedule.month} physician={selected} />
          ) : (
            <p className="text-slate-400 text-sm">Select a physician to view their schedule.</p>
          )}
        </div>
      </div>
    </div>
  )
}

function MonthGrid({ year, month, physician }) {
  const numDays = daysInMonth(year, month)
  const lead = firstWeekday(year, month)
  const cells = []
  for (let i = 0; i < lead; i++) cells.push(null)
  for (let d = 1; d <= numDays; d++) cells.push(d)
  while (cells.length % 7 !== 0) cells.push(null)

  // Actual scheduled counts, for comparing against what they requested below.
  const allEntries = Object.values(physician.byDay).flat()
  const scheduled2400 = allEntries.filter(e => e.kind === 'shift' && e.is2400).length
  const scheduled0600 = allEntries.filter(e => e.kind === 'shift' && e.is0600).length
  const scheduledTotal = allEntries.filter(e => e.kind === 'shift').length
  const req = physician.requested

  return (
    <div className="max-w-3xl mx-auto">
      <h2 className="text-lg font-semibold text-slate-800 mb-1">{physician.name}</h2>
      <p className="text-xs text-slate-400 font-mono mb-4">{physician.id}</p>

      <div className="grid grid-cols-7 gap-1.5 mb-1.5">
        {WEEKDAY_LABELS.map(w => (
          <div key={w} className="text-xs font-medium text-slate-400 text-center py-1">{w}</div>
        ))}
      </div>
      <div className="grid grid-cols-7 gap-1.5">
        {cells.map((day, idx) => {
          if (day === null) return <div key={idx} className="aspect-square" />
          const entries = physician.byDay[day] || []
          const working = entries.length > 0
          return (
            <div
              key={idx}
              className={`aspect-square rounded-lg border p-1.5 flex flex-col ${
                working ? 'bg-sky-50 border-sky-300' : 'bg-white border-slate-200'
              }`}
            >
              <span className={`text-xs font-medium ${working ? 'text-sky-800' : 'text-slate-400'}`}>{day}</span>
              <div className="flex-1 flex flex-col items-center justify-center gap-0.5">
                {entries.map((e, i) => (
                  <span
                    key={i}
                    className={`text-[10px] leading-tight font-semibold rounded px-1 text-center ${
                      e.kind === 'call'
                        ? 'text-amber-700 bg-amber-100 border border-amber-300'
                        : 'text-sky-700 bg-sky-100'
                    }`}
                  >
                    {e.kind === 'call' ? e.label : (
                      <>
                        {e.site}
                        <br />
                        <span className="font-normal opacity-70">{e.time}</span>
                      </>
                    )}
                  </span>
                ))}
              </div>
            </div>
          )
        })}
      </div>

      <div className="flex items-center gap-4 mt-4 text-xs text-slate-500">
        <span className="flex items-center gap-1.5">
          <span className="w-3 h-3 rounded bg-sky-100 border border-sky-300 inline-block" /> Working (site + time shown)
        </span>
        <span className="flex items-center gap-1.5">
          <span className="w-3 h-3 rounded bg-amber-100 border border-amber-300 inline-block" /> On-call (backup)
        </span>
      </div>

      <div className="mt-5 pt-4 border-t border-slate-200">
        <h3 className="text-xs font-semibold text-slate-500 uppercase tracking-wide mb-2">Requested vs. scheduled</h3>
        {req ? (
          <div className="grid grid-cols-3 gap-3">
            <StatPair label="Shifts" requested={req.shifts_requested} actual={scheduledTotal} sub={`max ${req.shifts_max}`} />
            <StatPair label="2400h" requested={req.shifts_2400h_requested} actual={scheduled2400} />
            <StatPair label="0600h" requested={req.shifts_0600h_requested} actual={scheduled0600} />
          </div>
        ) : (
          <p className="text-xs text-slate-400">No submission on file for this physician this month.</p>
        )}
      </div>
    </div>
  )
}

function StatPair({ label, requested, actual, sub }) {
  const mismatch = actual !== null && actual !== requested
  return (
    <div className="bg-white border border-slate-200 rounded-lg p-2.5">
      <div className="text-[10px] text-slate-400 uppercase tracking-wide mb-1">{label}</div>
      <div className="flex items-baseline gap-1.5">
        <span className="text-sm font-semibold text-slate-700">{requested}</span>
        <span className="text-[10px] text-slate-400">requested</span>
      </div>
      {actual !== null && (
        <div className={`flex items-baseline gap-1.5 ${mismatch ? 'text-amber-600' : 'text-slate-400'}`}>
          <span className="text-sm font-semibold">{actual}</span>
          <span className="text-[10px]">scheduled</span>
        </div>
      )}
      {sub && <div className="text-[10px] text-slate-400 mt-0.5">{sub}</div>}
    </div>
  )
}
