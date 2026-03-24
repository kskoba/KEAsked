import React, { useState, useEffect, useRef } from 'react'
import { importSubmissions, importFlatFile, generateSchedule, detectFlatMonth, getGenerateProgress, loadScheduleFromFile } from '../api'

const MONTHS = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December'
]

const currentDate = new Date()

export default function DirectoryPicker({ onImportDone, onScheduleGenerated, onScheduleLoaded, importResult }) {
  const [mode, setMode] = useState('flat')        // 'flat' | 'directory' | 'load'
  const [path, setPath] = useState('')
  const [month, setMonth] = useState(currentDate.getMonth() + 1)   // 1-based
  const [year, setYear] = useState(currentDate.getFullYear())
  const [importing, setImporting] = useState(false)
  const [generating, setGenerating] = useState(false)
  const [loading, setLoading] = useState(false)   // for 'load' mode
  const [progress, setProgress] = useState(null)   // { current, total, best_unfilled, solver, time_limit }
  const [countdown, setCountdown] = useState(null)  // seconds remaining for CP-SAT
  const [importError, setImportError] = useState(null)
  const [generateError, setGenerateError] = useState(null)
  const [loadError, setLoadError] = useState(null)
  const pollRef = useRef(null)
  const countdownRef = useRef(null)
  const countdownStartedRef = useRef(false)

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

  async function handleLoadSchedule() {
    if (!path) return
    setLoading(true)
    setLoadError(null)
    try {
      const result = await loadScheduleFromFile(path)
      onScheduleLoaded(result)
    } catch (err) {
      setLoadError(err.message)
    } finally {
      setLoading(false)
    }
  }

  async function handleImport() {
    if (!path) return
    setImporting(true)
    setImportError(null)
    try {
      const result = mode === 'flat'
        ? await importFlatFile(path, year, month)
        : await importSubmissions(path, year, month)
      onImportDone(result)
    } catch (err) {
      setImportError(err.message)
    } finally {
      setImporting(false)
    }
  }

  async function handleGenerate() {
    if (!importResult) return
    setGenerating(true)
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
      const result = await generateSchedule(year, month)
      onScheduleGenerated(result)
    } catch (err) {
      setGenerateError(err.message)
    } finally {
      clearInterval(pollRef.current)
      pollRef.current = null
      clearInterval(countdownRef.current)
      countdownRef.current = null
      setGenerating(false)
      setProgress(null)
      setCountdown(null)
    }
  }

  // Clean up poll and countdown on unmount
  useEffect(() => () => {
    if (pollRef.current) clearInterval(pollRef.current)
    if (countdownRef.current) clearInterval(countdownRef.current)
  }, [])

  const canImport = path.trim().length > 0 && !importing && !generating
  const canGenerate = importResult !== null && !generating && !importing

  // Count valid physicians for status badge
  const validCount = importResult
    ? importResult.physicians.filter(p => p.is_valid).length
    : 0
  const totalCount = importResult ? importResult.total_physicians : 0

  return (
    <div className="bg-white rounded-xl shadow-sm border border-slate-200 p-6">
      <h2 className="text-lg font-semibold text-slate-800 mb-4 flex items-center gap-2">
        <svg className="w-5 h-5 text-sky-500" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M3 7a2 2 0 012-2h4l2 2h8a2 2 0 012 2v9a2 2 0 01-2 2H5a2 2 0 01-2-2V7z" />
        </svg>
        Schedule Setup
      </h2>

      {/* Mode toggle */}
      <div className="flex gap-1 mb-5 p-1 bg-slate-100 rounded-lg w-fit">
        {[['flat', 'Single flat file'], ['directory', 'Directory'], ['load', 'Load Saved Schedule']].map(([val, label]) => (
          <button
            key={val}
            onClick={() => { setMode(val); setPath(''); setImportError(null); setGenerateError(null); setLoadError(null) }}
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

      {/* Path row */}
      <div className="mb-5">
        <label className="block text-sm font-medium text-slate-700 mb-1">
          {mode === 'flat' ? 'Preferences File (.xlsx)' : mode === 'directory' ? 'Submissions Directory' : 'Schedule File (.xlsx)'}
        </label>
        <div className="flex gap-2">
          <input
            type="text"
            readOnly
            value={path}
            placeholder={
              mode === 'flat' ? 'Select the flat preferences Excel file…' :
              mode === 'directory' ? 'Select the folder containing per-physician request files…' :
              'Select a previously exported schedule .xlsx…'
            }
            className="flex-1 px-3 py-2 rounded-md border border-slate-300 bg-slate-50 text-slate-700 text-sm cursor-default focus:outline-none"
          />
          <button
            onClick={handleBrowse}
            disabled={importing || generating || loading}
            className="px-4 py-2 bg-slate-700 hover:bg-slate-600 disabled:bg-slate-400 text-white text-sm font-medium rounded-md transition-colors"
          >
            Browse…
          </button>
        </div>
      </div>

      {/* Month / Year row — hidden in load mode (auto-detected from file) */}
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

      {/* Error messages */}
      {importError && (
        <div className="mb-4 p-3 bg-red-50 border border-red-200 rounded-md text-red-700 text-sm">
          <strong>Import error:</strong> {importError}
        </div>
      )}
      {generateError && (
        <div className="mb-4 p-3 bg-red-50 border border-red-200 rounded-md text-red-700 text-sm">
          <strong>Generate error:</strong> {generateError}
        </div>
      )}
      {loadError && (
        <div className="mb-4 p-3 bg-red-50 border border-red-200 rounded-md text-red-700 text-sm">
          <strong>Load error:</strong> {loadError}
        </div>
      )}

      {/* Generation progress bar */}
      {generating && progress && (
        <div className="mb-4">
          {progress.solver === 'cpsat' ? (
            <>
              <div className="flex justify-between text-xs text-slate-500 mb-1">
                <span className="font-medium text-sky-700">
                  {countdown === 0 ? 'Finishing up…' : 'CP-SAT solver running…'}
                </span>
                {countdown !== null && countdown > 0 && (
                  <span className={`font-mono font-bold ${countdown <= 30 ? 'text-amber-600' : 'text-sky-700'}`}>
                    {Math.floor(countdown / 60)}:{String(countdown % 60).padStart(2, '0')}
                  </span>
                )}
              </div>
              {/* Time-elapsed bar: fills left-to-right as solve progresses */}
              <div className="w-full bg-slate-200 rounded-full h-2 overflow-hidden">
                <div
                  className="h-2 rounded-full bg-sky-500 transition-all duration-1000"
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
                  Importing…
                </>
              ) : (
                <>
                  <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M4 16v2a2 2 0 002 2h12a2 2 0 002-2v-2M7 10l5 5 5-5M12 15V3" />
                  </svg>
                  Import & Validate
                </>
              )}
            </button>

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

            {importResult && (
              <span className={`ml-auto text-sm font-medium ${validCount === totalCount ? 'text-emerald-600' : 'text-amber-600'}`}>
                {validCount}/{totalCount} physicians valid
              </span>
            )}
          </>
        )}
      </div>
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
