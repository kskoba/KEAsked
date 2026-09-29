import React, { useState, useEffect, useCallback } from 'react'
import { getSequencingRules, setApiBaseUrl } from './api'

const KIND_LABELS = {
  timed_separation: 'Timed separation',
  conditional_cowork: 'Conditional co-working',
  forbidden_precursor_shifts: 'Forbidden precursor shift',
  night_chain_ramp_in: 'Night-chain ramp-in',
  linked_rest_pairs: 'Linked rest pair',
}

export default function SchedulingRulesPanel() {
  const [apiBaseResolved, setApiBaseResolved] = useState(false)
  const [rules, setRules] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  // Separate BrowserWindow, separate renderer module state -- must
  // re-resolve the backend location here too (see RosterEditor.jsx).
  useEffect(() => {
    let cancelled = false
    window.electronAPI.getBackendConfig()
      .then(({ effectiveUrl }) => { if (!cancelled) setApiBaseUrl(effectiveUrl) })
      .finally(() => { if (!cancelled) setApiBaseResolved(true) })
    return () => { cancelled = true }
  }, [])

  const load = useCallback(() => {
    setLoading(true)
    setError(null)
    return getSequencingRules()
      .then((res) => setRules(res.rules))
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => {
    if (!apiBaseResolved) return
    load()
  }, [apiBaseResolved, load])

  const handleCloseClick = () => window.electronAPI.forceCloseSelf()

  return (
    <div className="h-screen flex flex-col bg-slate-50">
      <header
        className="flex items-center justify-between px-5 py-3 shadow-md flex-shrink-0"
        style={{ backgroundColor: '#1e293b' }}
      >
        <div>
          <h1 className="text-white font-bold text-base leading-tight">Scheduling Rules</h1>
          <p className="text-slate-400 text-xs">Person-specific pair &amp; sequencing rules from scheduler_config.yaml</p>
        </div>
        <button
          onClick={handleCloseClick}
          title="Close"
          className="w-7 h-7 flex items-center justify-center rounded-full bg-red-600 hover:bg-red-500 text-white text-sm font-bold transition-colors"
        >
          ✕
        </button>
      </header>

      <div className="flex-1 overflow-auto p-6 max-w-2xl mx-auto w-full space-y-4">
        <div className="rounded-md border border-sky-200 bg-sky-50 p-4 text-sm text-sky-900">
          <p className="font-medium mb-1">View only</p>
          <p>
            These rules are configured directly in a file the app doesn't edit through any screen —
            not here, not in the Roster Editor. If one of these needs to change, or a new one is
            needed, contact support rather than trying to edit it yourself.
          </p>
        </div>

        {error && (
          <div className="rounded-md border border-red-300 bg-red-50 p-4 text-sm text-red-800">{error}</div>
        )}

        {loading && !rules && (
          <p className="text-sm text-slate-500">Loading…</p>
        )}

        {rules && rules.length === 0 && (
          <div className="rounded-md border border-slate-200 bg-white p-4 text-sm text-slate-500">
            No person-specific rules are currently configured.
          </div>
        )}

        {rules && rules.length > 0 && (
          <div className="space-y-3">
            {rules.map((r, i) => (
              <div key={i} className="bg-white rounded-lg shadow-sm border border-slate-200 p-4 space-y-1.5">
                <div className="flex items-center justify-between">
                  <span className="font-semibold text-slate-800 text-sm">{r.physician_names.join(' & ')}</span>
                  <span className="text-xs font-medium px-2 py-0.5 rounded-full bg-slate-100 text-slate-600">
                    {KIND_LABELS[r.kind] || r.kind}
                  </span>
                </div>
                <p className="text-sm text-slate-600 leading-relaxed">{r.description}</p>
              </div>
            ))}
          </div>
        )}

        <button
          onClick={load}
          disabled={loading}
          className="text-xs px-2.5 py-1 rounded-md border border-slate-300 bg-white hover:bg-slate-50 text-slate-700 disabled:opacity-50"
        >
          {loading ? 'Refreshing…' : 'Refresh'}
        </button>
      </div>
    </div>
  )
}
