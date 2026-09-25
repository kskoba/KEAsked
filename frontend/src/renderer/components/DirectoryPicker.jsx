import React, { useState, useEffect, useRef } from 'react'
import { importSubmissions, importFlatFile, importFromSked, resendMonthlyRequest, getSkedPeriods, generateSchedule, cancelGenerate, detectFlatMonth, getGenerateProgress, loadScheduleFromFile, getApiBaseUrl } from '../api'

// Whether the active backend is this machine or a remote one (e.g. a Docker
// container on Unraid). The native file/folder picker only browses this
// computer's filesystem, so on a remote backend the path field needs to
// accept a typed path instead — one that exists on the *backend's* side
// (e.g. inside a mounted /config volume), not this machine's.
function isRemoteBackend() {
  return !/^https?:\/\/(127\.0\.0\.1|localhost)(:|\/|$)/.test(getApiBaseUrl())
}

const MONTHS = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December'
]

const currentDate = new Date()

export default function DirectoryPicker({ onImportDone, onScheduleGenerated, onScheduleLoaded, importResult }) {
  const [mode, setMode] = useState('flat')        // 'flat' | 'directory' | 'sked' | 'load'
  const [path, setPath] = useState('')
  const [skedPeriods, setSkedPeriods] = useState(null)
  const [skedPeriodId, setSkedPeriodId] = useState('')
  const [skedPeriodsError, setSkedPeriodsError] = useState(null)
  const [resendState, setResendState] = useState({}) // physicianId -> 'sending' | 'sent' | error message
  const [month, setMonth] = useState(currentDate.getMonth() + 1)   // 1-based
  const [year, setYear] = useState(currentDate.getFullYear())
  const [importing, setImporting] = useState(false)
  const [generating, setGenerating] = useState(false)
  const [cancelling, setCancelling] = useState(false)
  const [timeLimitMinutes, setTimeLimitMinutes] = useState(10)
  const [loading, setLoading] = useState(false)   // for 'load' mode
  // Preferences sub-section in 'load' mode
  const [prefPath, setPrefPath] = useState('')
  const [prefMode, setPrefMode] = useState('flat')  // 'flat' | 'directory'
  const [progress, setProgress] = useState(null)   // { current, total, best_unfilled, solver, time_limit }
  const [countdown, setCountdown] = useState(null)  // seconds remaining for CP-SAT
  const [importError, setImportError] = useState(null)
  const [generateError, setGenerateError] = useState(null)
  const [loadError, setLoadError] = useState(null)
  const pollRef = useRef(null)
  const countdownRef = useRef(null)
  const countdownStartedRef = useRef(false)
  const remote = isRemoteBackend()

  useEffect(() => {
    if (mode !== 'sked' || skedPeriods !== null) return
    getSkedPeriods('shift_request')
      .then((r) => {
        setSkedPeriods(r.periods)
        if (r.periods.length > 0 && !skedPeriodId) setSkedPeriodId(r.periods[0].id)
      })
      .catch((err) => setSkedPeriodsError(err.message))
  }, [mode, skedPeriods, skedPeriodId])

  async function handleBrowse() {
    let selected = null
    if (window.electronAPI) {
      selected = mode === 'directory'
        ? await window.electronAPI.openDirectory()
        : await window.electronAPI.openFile()
    } else {
      selected = prompt(`Enter ${mode === 'directory' ? 'directory' : 'file'} path:`)
    }
    if (!selected) return
    setPath(selected)
    if (mode === 'flat') {
      try {
        const detected = await detectFlatMonth(selected)
        setYear(detected.year)
        setMonth(detected.month)
      } catch {
        // ignore — user can set manually
      }
    }
  }

  async function handleBrowsePref() {
    let selected = null
    if (window.electronAPI) {
      selected = prefMode === 'directory'
        ? await window.electronAPI.openDirectory()
        : await window.electronAPI.openFile()
    } else {
      selected = prompt(`Enter ${prefMode === 'directory' ? 'directory' : 'file'} path:`)
    }
    if (selected) setPrefPath(selected)
  }

  async function handleLoadSchedule() {
    if (!path) return
    setLoading(true)
    setLoadError(null)
    try {
      // Step 1: import preferences if provided
      let prefResult = null
      if (prefPath.trim()) {
        try {
          prefResult = prefMode === 'flat'
            ? await importFlatFile(prefPath, year, month)
            : await importSubmissions(prefPath, year, month)
          onImportDone(prefResult)
        } catch (err) {
          setLoadError(`Preferences import failed: ${err.message}`)
          return
        }
      }
      // Step 2: load the schedule (backend validates month consistency)
      const result = await loadScheduleFromFile(path)
      onScheduleLoaded(result)
    } catch (err) {
      setLoadError(err.message)
    } finally {
      setLoading(false)
    }
  }

  async function handleImport() {
    if (mode === 'sked' ? !skedPeriodId : !path) return
    setImporting(true)
    setImportError(null)
    try {
      const result = mode === 'sked'
        ? await importFromSked(skedPeriodId, year, month)
        : mode === 'flat'
          ? await importFlatFile(path, year, month)
          : await importSubmissions(path, year, month)
      onImportDone(result)
    } catch (err) {
      setImportError(err.message)
    } finally {
      setImporting(false)
    }
  }

  async function handleResend(physicianId) {
    if (!skedPeriodId || resendState[physicianId] === 'sending') return
    setResendState((s) => ({ ...s, [physicianId]: 'sending' }))
    try {
      await resendMonthlyRequest(skedPeriodId, physicianId)
      setResendState((s) => ({ ...s, [physicianId]: 'sent' }))
      setTimeout(() => setResendState((s) => ({ ...s, [physicianId]: undefined })), 2500)
    } catch (err) {
      setResendState((s) => ({ ...s, [physicianId]: err.message }))
    }
  }

  async function handleGenerate() {
    if (!importResult) return
    setGenerating(true)
    setCancelling(false)
    setGenerateError(null)
    setProgress({ current: 0, total: 200, best_unfilled: null })
    countdownStartedRef.current = false

    // Start polling progress every 600ms
    pollRef.current = setInterval(async () => {
      try {
        const p = await getGenerateProgress()
        setProgress(p)
        // Start countdown when we first learn this is a CP-SAT run (only once per generate)
        if (p.solver === 'cpsat' && p.running && !countdownStartedRef.current) {
          countdownStartedRef.current = true
          const secs = p.time_limit || 300
          setCountdown(secs)
          countdownRef.current = setInterval(() => {
            setCountdown(prev => {
              if (prev === null || prev <= 1) {
                clearInterval(countdownRef.current)
                countdownRef.current = null
                return 0
              }
              return prev - 1
            })
          }, 1000)
        }
        if (!p.running && p.current > 0) {
          clearInterval(pollRef.current)
          pollRef.current = null
        }
      } catch { /* ignore poll errors */ }
    }, 600)

    try {
      const result = await generateSchedule(year, month, timeLimitMinutes * 60)
      onScheduleGenerated(result)
    } catch (err) {
      setGenerateError(err.message)
    } finally {
      clearInterval(pollRef.current)
      pollRef.current = null
      clearInterval(countdownRef.current)
      countdownRef.current = null
      setGenerating(false)
      setCancelling(false)
      setProgress(null)
      setCountdown(null)
    }
  }

  async function handleCancel() {
    setCancelling(true)
    try {
      await cancelGenerate()
    } catch {
      // ignore — the in-flight generate request will still resolve on its own
    }
  }

  // Clean up poll and countdown on unmount
  useEffect(() => () => {
    if (pollRef.current) clearInterval(pollRef.current)
    if (countdownRef.current) clearInterval(countdownRef.current)
  }, [])

  const canImport = (mode === 'sked' ? skedPeriodId.trim().length > 0 : path.trim().length > 0) && !importing && !generating
  const canGenerate = importResult !== null && !generating && !importing

  // Count valid physicians for status badge
  const validCount = importResult
    ? importResult.physicians.filter(p => p.is_valid).length
    : 0
  const totalCount = importResult ? importResult.total_physicians : 0

  return (
    <div className={mode === 'load' ? '' : 'flex flex-col md:flex-row gap-4 items-start'}>
      {/* Import card */}
      <div className={`bg-white rounded-xl shadow-sm border border-slate-200 p-6 ${mode === 'load' ? '' : 'flex-1 min-w-0 w-full'}`}>
        <h2 className="text-lg font-semibold text-slate-800 mb-4 flex items-center gap-2">
          <svg className="w-5 h-5 text-sky-500" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M3 7a2 2 0 012-2h4l2 2h8a2 2 0 012 2v9a2 2 0 01-2 2H5a2 2 0 01-2-2V7z" />
          </svg>
          Schedule Setup
        </h2>

        {/* Mode toggle */}
        <div className="flex gap-1 mb-5 p-1 bg-slate-100 rounded-lg w-fit">
          {[['flat', 'Single flat file'], ['directory', 'Directory'], ['sked', 'From Web (sked)'], ['load', 'Load Saved Schedule']].map(([val, label]) => (
            <button
              key={val}
              onClick={() => { setMode(val); setPath(''); setPrefPath(''); setImportError(null); setGenerateError(null); setLoadError(null) }}
              disabled={importing || generating || loading}
              className={`px-3 py-1.5 text-sm font-medium rounded-md transition-colors ${
                mode === val
                  ? 'bg-white text-slate-800 shadow-sm'
                  : 'text-slate-500 hover:text-slate-700'
              }`}
            >
              {label}
            </button>
          ))}
        </div>

        {/* Path row (sked mode: period picker instead) */}
        {mode === 'sked' ? (
          <div className="mb-5">
            <label className="block text-sm font-medium text-slate-700 mb-1">Sked Period</label>
            {skedPeriodsError && (
              <p className="mb-1.5 text-xs text-red-600">{skedPeriodsError}</p>
            )}
            <select
              value={skedPeriodId}
              onChange={(e) => setSkedPeriodId(e.target.value)}
              disabled={importing || !skedPeriods || skedPeriods.length === 0}
              className="w-full px-3 py-2 rounded-md border border-slate-300 bg-white text-slate-700 text-sm focus:outline-none focus:ring-2 focus:ring-sky-400"
            >
              {skedPeriods === null && <option>Loading periods…</option>}
              {skedPeriods && skedPeriods.length === 0 && <option>No periods found on sked</option>}
              {skedPeriods?.map((p) => (
                <option key={p.id} value={p.id}>{p.label}</option>
              ))}
            </select>
          </div>
        ) : (
          <div className="mb-5">
            <label className="block text-sm font-medium text-slate-700 mb-1">
              {mode === 'flat' ? 'Preferences File (.xlsx)' : mode === 'directory' ? 'Submissions Directory' : 'Schedule File (.xlsx)'}
            </label>
            <div className="flex gap-2">
              <input
                type="text"
                readOnly={!remote}
                value={path}
                onChange={remote ? (e) => setPath(e.target.value) : undefined}
                placeholder={
                  remote ? 'Type the path as it exists on the remote backend, e.g. /config/request-imports/october' :
                  mode === 'flat' ? 'Select the flat preferences Excel file…' :
                  mode === 'directory' ? 'Select the folder containing per-physician request files…' :
                  'Select a previously exported schedule .xlsx…'
                }
                className={`flex-1 px-3 py-2 rounded-md border border-slate-300 text-slate-700 text-sm focus:outline-none ${remote ? 'bg-white focus:ring-2 focus:ring-sky-400' : 'bg-slate-50 cursor-default'}`}
              />
              <button
                onClick={handleBrowse}
                disabled={importing || generating || loading}
                title={remote ? 'Browses this computer, not the remote backend — usually you want to type the path instead' : undefined}
                className="px-4 py-2 bg-slate-700 hover:bg-slate-600 disabled:bg-slate-400 text-white text-sm font-medium rounded-md transition-colors"
              >
                Browse…
              </button>
            </div>
            {remote && (
              <p className="mt-1.5 text-xs text-amber-600">
                Backend is remote — paths are resolved on the backend's filesystem, not this computer.
              </p>
            )}
          </div>
        )}

        {/* Month / Year row */}
        {mode !== 'load' && (
          <div className="flex gap-4 mb-5">
            <div className="flex-1">
              <label className="block text-sm font-medium text-slate-700 mb-1">Month</label>
              <select
                value={month}
                onChange={e => setMonth(Number(e.target.value))}
                disabled={importing || generating}
                className="w-full px-3 py-2 rounded-md border border-slate-300 bg-white text-slate-700 text-sm focus:outline-none focus:ring-2 focus:ring-sky-400"
              >
                {MONTHS.map((name, idx) => (
                  <option key={name} value={idx + 1}>{name}</option>
                ))}
              </select>
            </div>
            <div className="w-36">
              <label className="block text-sm font-medium text-slate-700 mb-1">Year</label>
              <input
                type="number"
                value={year}
                onChange={e => setYear(Number(e.target.value))}
                min={2020}
                max={2099}
                disabled={importing || generating}
                className="w-full px-3 py-2 rounded-md border border-slate-300 bg-white text-slate-700 text-sm focus:outline-none focus:ring-2 focus:ring-sky-400"
              />
            </div>
          </div>
        )}

        {/* Physician preferences sub-section — only in load mode */}
        {mode === 'load' && (
          <div className="mb-5 p-4 bg-slate-50 border border-slate-200 rounded-lg">
            <div className="flex items-center justify-between mb-3">
              <span className="text-sm font-medium text-slate-700">
                Physician Preferences
                <span className="ml-2 text-slate-400 font-normal">(optional — enables accurate constraint checking)</span>
              </span>
            </div>

            {/* Pref mode toggle */}
            <div className="flex gap-1 mb-3 p-1 bg-slate-200 rounded-md w-fit">
              {[['flat', 'Flat file'], ['directory', 'Directory']].map(([val, label]) => (
                <button
                  key={val}
                  onClick={() => { setPrefMode(val); setPrefPath('') }}
                  disabled={loading}
                  className={`px-2.5 py-1 text-xs font-medium rounded transition-colors ${
                    prefMode === val ? 'bg-white text-slate-800 shadow-sm' : 'text-slate-500 hover:text-slate-700'
                  }`}
                >
                  {label}
                </button>
              ))}
            </div>

            {/* Month / year for preferences */}
            <div className="flex gap-3 mb-3">
              <div className="flex-1">
                <label className="block text-xs font-medium text-slate-600 mb-1">Month</label>
                <select
                  value={month}
                  onChange={e => setMonth(Number(e.target.value))}
                  disabled={loading}
                  className="w-full px-2 py-1.5 rounded border border-slate-300 bg-white text-slate-700 text-sm focus:outline-none focus:ring-2 focus:ring-sky-400"
                >
                  {MONTHS.map((name, idx) => (
                    <option key={name} value={idx + 1}>{name}</option>
                  ))}
                </select>
              </div>
              <div className="w-28">
                <label className="block text-xs font-medium text-slate-600 mb-1">Year</label>
                <input
                  type="number"
                  value={year}
                  onChange={e => setYear(Number(e.target.value))}
                  min={2020}
                  max={2099}
                  disabled={loading}
                  className="w-full px-2 py-1.5 rounded border border-slate-300 bg-white text-slate-700 text-sm focus:outline-none focus:ring-2 focus:ring-sky-400"
                />
              </div>
            </div>

            {/* Pref path row */}
            <div className="flex gap-2">
              <input
                type="text"
                readOnly={!remote}
                value={prefPath}
                onChange={remote ? (e) => setPrefPath(e.target.value) : undefined}
                placeholder={remote ? 'Type the path as it exists on the remote backend…' : (prefMode === 'flat' ? 'Select flat preferences file…' : 'Select submissions directory…')}
                className={`flex-1 px-3 py-1.5 rounded border border-slate-300 text-slate-700 text-sm focus:outline-none ${remote ? 'focus:ring-2 focus:ring-sky-400' : 'cursor-default'} bg-white`}
              />
              <button
                onClick={handleBrowsePref}
                disabled={loading}
                title={remote ? 'Browses this computer, not the remote backend — usually you want to type the path instead' : undefined}
                className="px-3 py-1.5 bg-slate-600 hover:bg-slate-500 disabled:bg-slate-300 text-white text-sm font-medium rounded transition-colors"
              >
                Browse…
              </button>
              {prefPath && (
                <button
                  onClick={() => setPrefPath('')}
                  disabled={loading}
                  className="px-2 py-1.5 text-slate-400 hover:text-slate-600 text-sm"
                  title="Clear"
                >
                  ✕
                </button>
              )}
            </div>
            {remote && (
              <p className="mt-1.5 text-xs text-amber-600">
                Backend is remote — paths are resolved on the backend's filesystem, not this computer.
              </p>
            )}
          </div>
        )}

        {/* Error messages */}
        {importError && (
          <div className="mb-4 p-3 bg-red-50 border border-red-200 rounded-md text-red-700 text-sm">
            <strong>Import error:</strong> {importError}
          </div>
        )}
        {loadError && (
          <div className="mb-4 p-3 bg-red-50 border border-red-200 rounded-md text-red-700 text-sm">
            <strong>Load error:</strong> {loadError}
          </div>
        )}

        {/* Action buttons */}
        <div className="flex items-center gap-3">
          {mode === 'load' ? (
            <button
              onClick={handleLoadSchedule}
              disabled={!path.trim() || loading}
              className="flex items-center gap-2 px-5 py-2.5 bg-sky-600 hover:bg-sky-500 disabled:bg-slate-300 disabled:cursor-not-allowed text-white font-medium text-sm rounded-md transition-colors"
            >
              {loading ? (
                <>
                  <Spinner />
                  Loading…
                </>
              ) : (
                <>
                  <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M4 16v2a2 2 0 002 2h12a2 2 0 002-2v-2M7 10l5 5 5-5M12 15V3" />
                  </svg>
                  Open Schedule
                </>
              )}
            </button>
          ) : (
            <>
              <button
                onClick={handleImport}
                disabled={!canImport}
                className="flex items-center gap-2 px-5 py-2.5 bg-sky-600 hover:bg-sky-500 disabled:bg-slate-300 disabled:cursor-not-allowed text-white font-medium text-sm rounded-md transition-colors"
              >
                {importing ? (
                  <>
                    <Spinner />
                    {mode === 'sked' ? 'Pulling in…' : 'Importing…'}
                  </>
                ) : (
                  <>
                    <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}>
                      <path strokeLinecap="round" strokeLinejoin="round" d="M4 16v2a2 2 0 002 2h12a2 2 0 002-2v-2M7 10l5 5 5-5M12 15V3" />
                    </svg>
                    {mode === 'sked' ? 'Pull In Requests' : 'Import & Validate'}
                  </>
                )}
              </button>

              {importResult && (
                <span className={`ml-auto text-sm font-medium ${validCount === totalCount ? 'text-emerald-600' : 'text-amber-600'}`}>
                  {validCount}/{totalCount} physicians valid
                </span>
              )}
            </>
          )}
        </div>

        {/* Not-submitted highlight (sked mode only) */}
        {mode === 'sked' && importResult?.not_submitted?.length > 0 && (
          <div className="mt-5 pt-5 border-t border-slate-200">
            <div className="flex items-center justify-between mb-2">
              <h3 className="text-sm font-medium text-slate-700">
                Not submitted yet <span className="text-slate-400 font-normal">({importResult.not_submitted.length})</span>
              </h3>
            </div>
            <div className="border border-amber-200 bg-amber-50 rounded-md divide-y divide-amber-100 max-h-72 overflow-auto">
              {importResult.not_submitted.map((row) => {
                const state = resendState[row.physician_id]
                const isSending = state === 'sending'
                const isSent = state === 'sent'
                const isError = state && !isSending && !isSent
                return (
                  <div key={row.physician_id} className="px-3 py-2 flex items-center justify-between gap-3 text-sm">
                    <div className="min-w-0">
                      <div className="font-medium text-slate-800 truncate">{row.physician_name}</div>
                      <div className="text-xs text-slate-500">
                        {row.status === 'draft' ? 'Draft — not yet submitted' : 'Not started'}
                        {!row.email && ' · no email on file'}
                      </div>
                    </div>
                    <button
                      onClick={() => handleResend(row.physician_id)}
                      disabled={!row.email || isSending}
                      title={isError ? state : undefined}
                      className={
                        'flex-shrink-0 text-xs px-2.5 py-1 rounded-md border font-medium disabled:opacity-50 disabled:cursor-not-allowed transition-colors ' +
                        (isError
                          ? 'border-red-300 bg-red-50 text-red-700 hover:bg-red-100'
                          : 'border-amber-300 bg-white text-amber-800 hover:bg-amber-100')
                      }
                    >
                      {isSending ? 'Sending…' : isSent ? 'Sent!' : isError ? 'Failed — retry' : 'Send reminder'}
                    </button>
                  </div>
                )
              })}
            </div>
          </div>
        )}
      </div>

      {/* CP-SAT solver card */}
      {mode !== 'load' && (
        <div className="flex-1 min-w-0 w-full bg-white rounded-xl shadow-sm border border-slate-200 p-6">
          <h2 className="text-lg font-semibold text-slate-800 mb-4 flex items-center gap-2">
            <svg className="w-5 h-5 text-emerald-500" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2" />
            </svg>
            CP-SAT Solver
          </h2>

          {/* Solver time limit */}
          <div className="mb-5">
            <label className="block text-sm font-medium text-slate-700 mb-2">
              Solver Time Limit
              <span className="ml-2 text-slate-400 font-normal">— longer runs get closer to optimal</span>
            </label>
            <div className="flex items-center gap-2 flex-wrap">
              {[5, 10, 20, 30].map(mins => (
                <button
                  key={mins}
                  onClick={() => setTimeLimitMinutes(mins)}
                  disabled={generating}
                  className={`px-3 py-1.5 text-sm font-medium rounded-md border transition-colors ${
                    timeLimitMinutes === mins
                      ? 'bg-sky-600 border-sky-600 text-white'
                      : 'bg-white border-slate-300 text-slate-600 hover:border-sky-400 hover:text-sky-600'
                  }`}
                >
                  {mins} min
                </button>
              ))}
              <div className="flex items-center gap-1.5 ml-1">
                <input
                  type="number"
                  min={1}
                  max={180}
                  step={1}
                  value={timeLimitMinutes}
                  disabled={generating}
                  onChange={(e) => {
                    const v = parseInt(e.target.value, 10)
                    if (Number.isFinite(v)) setTimeLimitMinutes(Math.min(180, Math.max(1, v)))
                  }}
                  className={`w-16 px-2 py-1.5 text-sm text-center rounded-md border transition-colors ${
                    [5, 10, 20, 30].includes(timeLimitMinutes)
                      ? 'bg-white border-slate-300 text-slate-600'
                      : 'bg-sky-50 border-sky-400 text-sky-700 font-medium'
                  } focus:outline-none focus:ring-2 focus:ring-sky-400 disabled:opacity-50`}
                />
                <span className="text-sm text-slate-500">min (custom)</span>
              </div>
            </div>
          </div>

          {generateError && (
            <div className="mb-4 p-3 bg-red-50 border border-red-200 rounded-md text-red-700 text-sm">
              <strong>Generate error:</strong> {generateError}
            </div>
          )}

          {/* Generation progress bar */}
          {generating && progress && (
            <div className="mb-4">
              {progress.solver === 'cpsat' ? (
                <>
                  <div className="flex justify-between text-xs text-slate-500 mb-1">
                    <span className="font-medium text-sky-700">
                      {cancelling ? 'Cancelling — finishing up with best result so far…' :
                        countdown === 0 ? 'Finishing up…' : 'CP-SAT solver running…'}
                    </span>
                    {countdown !== null && countdown > 0 && !cancelling && (
                      <span className={`font-mono font-bold ${countdown <= 30 ? 'text-amber-600' : 'text-sky-700'}`}>
                        {Math.floor(countdown / 60)}:{String(countdown % 60).padStart(2, '0')}
                      </span>
                    )}
                  </div>
                  {/* Time-elapsed bar: fills left-to-right as solve progresses */}
                  <div className="w-full bg-slate-200 rounded-full h-2 overflow-hidden">
                    <div
                      className={`h-2 rounded-full transition-all duration-1000 ${cancelling ? 'bg-amber-500' : 'bg-sky-500'}`}
                      style={{
                        width: countdown !== null
                          ? `${((progress.time_limit || 300) - countdown) / (progress.time_limit || 300) * 100}%`
                          : '5%'
                      }}
                    />
                  </div>
                </>
              ) : (
                <>
                  <div className="flex justify-between text-xs text-slate-500 mb-1">
                    <span>
                      Running iteration {progress.current} / {progress.total}
                      {progress.best_unfilled != null && ` — best so far: ${progress.best_unfilled} unfilled`}
                    </span>
                    <span>{progress.total > 0 ? Math.round(progress.current / progress.total * 100) : 0}%</span>
                  </div>
                  <div className="w-full bg-slate-200 rounded-full h-2 overflow-hidden">
                    <div
                      className="bg-sky-500 h-2 rounded-full transition-all duration-300"
                      style={{ width: `${progress.total > 0 ? (progress.current / progress.total * 100) : 0}%` }}
                    />
                  </div>
                </>
              )}
            </div>
          )}

          {/* Action buttons */}
          <div className="flex items-center gap-3">
            <button
              onClick={handleGenerate}
              disabled={!canGenerate}
              className="flex items-center gap-2 px-5 py-2.5 bg-emerald-600 hover:bg-emerald-500 disabled:bg-slate-300 disabled:cursor-not-allowed text-white font-medium text-sm rounded-md transition-colors"
            >
              {generating ? (
                <>
                  <Spinner />
                  {countdown !== null
                    ? `Solving… ${Math.floor(countdown / 60)}:${String(countdown % 60).padStart(2, '0')}`
                    : 'Generating…'
                  }
                </>
              ) : (
                <>
                  <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2" />
                  </svg>
                  Generate Schedule
                </>
              )}
            </button>

            {generating && (
              <button
                onClick={handleCancel}
                disabled={cancelling}
                className="flex items-center gap-2 px-4 py-2.5 bg-white hover:bg-red-50 disabled:bg-slate-100 disabled:text-slate-400 disabled:cursor-not-allowed text-red-600 font-medium text-sm rounded-md border border-red-300 transition-colors"
              >
                {cancelling ? 'Cancelling…' : 'Cancel Run'}
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

function Spinner() {
  return (
    <svg className="w-4 h-4 animate-spin" fill="none" viewBox="0 0 24 24">
      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v4l3-3-3-3v4a8 8 0 00-8 8h4z" />
    </svg>
  )
}
