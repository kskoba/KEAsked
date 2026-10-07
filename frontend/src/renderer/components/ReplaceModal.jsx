import React, { useState, useEffect, useCallback, useMemo } from 'react'
import { assignPhysician, getSchedule, getCandidates } from '../api'

// Colour palette for soft-violation warning badges (mirrors ConflictModal)
const VIOLATION_COLORS = {
  weekend_limit: 'bg-pink-100 text-pink-700 border-pink-200',
  anchor_limit: 'bg-purple-100 text-purple-700 border-purple-200',
  forbidden_time_pair: 'bg-orange-100 text-orange-700 border-orange-200',
  timed_separation: 'bg-orange-100 text-orange-700 border-orange-200',
  no_shared_weekend: 'bg-yellow-100 text-yellow-700 border-yellow-200',
  cowork_condition: 'bg-yellow-100 text-yellow-700 border-yellow-200',
  same_shift_consecutive: 'bg-blue-100 text-blue-700 border-blue-200',
  group_mix: 'bg-blue-100 text-blue-700 border-blue-200',
  singleton: 'bg-indigo-100 text-indigo-700 border-indigo-200',
  night_q: 'bg-slate-100 text-slate-700 border-slate-200',
}

function badgeClass(rule) {
  const key = Object.keys(VIOLATION_COLORS).find(k => rule && rule.toLowerCase().includes(k))
  return key ? VIOLATION_COLORS[key] : 'bg-slate-100 text-slate-700 border-slate-200'
}

function formatDate(dateStr) {
  if (!dateStr) return dateStr
  const d = new Date(dateStr + 'T00:00:00')
  return d.toLocaleDateString('en-CA', { weekday: 'long', year: 'numeric', month: 'long', day: 'numeric' })
}

export default function ReplaceModal({ slot, scheduleData, importResult, onAssigned, onClose }) {
  const [search, setSearch] = useState('')
  const [assigning, setAssigning] = useState(null)  // physicianId being assigned
  const [error, setError] = useState(null)

  // Live list of EVERY physician with a submission this month, fetched fresh
  // on mount. The server temporarily unassigns the current occupant so each
  // check reflects the slot being genuinely open, then restores it. Each
  // entry carries its full violation list; hard-rule failures (unavailable,
  // already working that day, consecutive limit, spacing, forbidden site...)
  // mark it is_hard_blocked. Blocked physicians are still listed and
  // searchable -- someone who marked a day unavailable may have agreed to a
  // trade since -- but assigning one is an explicit override.
  const [liveCandidates, setLiveCandidates] = useState(null)   // null = loading
  const [fetchError, setFetchError] = useState(null)

  useEffect(() => {
    function onKey(e) { if (e.key === 'Escape' && assigning === null) onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose, assigning])

  useEffect(() => {
    let cancelled = false
    setFetchError(null)
    setLiveCandidates(null)

    getCandidates(slot.date, slot.shift.code, { includeAll: true })
      .then(data => {
        if (!cancelled) setLiveCandidates(data.candidates || [])
      })
      .catch(err => {
        if (!cancelled) {
          setFetchError(err.message)
          setLiveCandidates([])
        }
      })

    return () => { cancelled = true }
  }, [slot.date, slot.shift.code])

  const currentName = slot.physician_name || slot.physician_id

  const filtered = useMemo(() => {
    const list = liveCandidates || []
    const q = search.toLowerCase().trim()
    if (!q) return list
    return list.filter(c => (c.physician_name || '').toLowerCase().includes(q) || (c.physician_id || '').toLowerCase().includes(q))
  }, [liveCandidates, search])
  const eligible = useMemo(() => filtered.filter(c => !c.is_hard_blocked), [filtered])
  const blocked = useMemo(() => filtered.filter(c => c.is_hard_blocked), [filtered])
  const [showBlocked, setShowBlocked] = useState(false)

  const handleAssign = useCallback(async (candidate) => {
    setAssigning(candidate.physician_id)
    setError(null)
    try {
      const result = await assignPhysician(slot.date, slot.shift.code, candidate.physician_id)
      if (result.success === false) {
        setError(result.message || 'Assignment failed.')
        setAssigning(null)
        return
      }
      const updated = await getSchedule()
      onAssigned(updated, {
        physicianName: candidate.physician_name,
        violations: candidate.violations || [],
        date: slot.date,
        shiftCode: slot.shift.code,
      })
    } catch (err) {
      setError(err.message)
      setAssigning(null)
    }
  }, [slot, onAssigned])

  const isLoading = liveCandidates === null

  return (
    <>
      {/* Backdrop */}
      <div
        className="fixed inset-0 z-40 bg-black/50 backdrop-blur-sm"
        onClick={() => assigning === null && onClose()}
      />

      {/* Modal */}
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="replace-modal-title"
        className="fixed z-50 top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-full max-w-lg bg-white rounded-xl shadow-2xl overflow-hidden flex flex-col"
        style={{ maxHeight: '82vh' }}
        onClick={e => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-start justify-between px-6 py-4 bg-slate-50 border-b border-slate-200 flex-shrink-0">
          <div>
            <h2 id="replace-modal-title" className="text-base font-bold text-slate-800">
              Replace Physician
            </h2>
            <p className="text-sm text-slate-600">
              <span className="font-mono font-semibold text-slate-800">{slot.shift?.code}</span>
              {slot.shift?.site && <span className="text-slate-500"> · {slot.shift.site}</span>}
            </p>
            <p className="text-xs text-slate-500 mt-0.5">{formatDate(slot.date)}</p>
            {currentName && (
              <p className="text-xs text-slate-500 mt-0.5">
                Currently: <span className="font-medium text-slate-700">{currentName}</span>
              </p>
            )}
          </div>
          <button
            onClick={() => assigning === null && onClose()}
            className="text-slate-400 hover:text-slate-600 transition-colors p-1 rounded"
            aria-label="Close"
          >
            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        {/* Search */}
        <div className="px-4 py-3 border-b border-slate-100 flex-shrink-0">
          <input
            type="text"
            value={search}
            onChange={e => setSearch(e.target.value)}
            placeholder="Search any physician — blocked ones appear too…"
            className="w-full border border-slate-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-sky-400 placeholder-slate-300"
            autoFocus
            disabled={assigning !== null}
          />
        </div>

        {/* Candidate list */}
        <div className="flex-1 overflow-auto px-4 py-3">
          {error && (
            <div className="mb-3 p-3 bg-red-50 border border-red-200 rounded-md text-red-700 text-sm">
              {error}
            </div>
          )}

          {fetchError && (
            <div className="mb-3 p-3 bg-amber-50 border border-amber-200 rounded-md text-amber-700 text-xs">
              Could not refresh candidates from server ({fetchError}).
            </div>
          )}

          {isLoading ? (
            <div className="py-8 text-center text-slate-400 text-sm flex flex-col items-center gap-2">
              <svg className="w-6 h-6 animate-spin text-slate-300" fill="none" viewBox="0 0 24 24">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v4l3-3-3-3v4a8 8 0 00-8 8h4z" />
              </svg>
              Checking availability…
            </div>
          ) : filtered.length === 0 ? (
            <div className="py-8 text-center text-slate-400 text-sm">
              {search ? 'No physician matches that search.' : 'No physicians have a submission this month.'}
            </div>
          ) : (
            <div className="space-y-2">
              <p className="text-xs text-slate-500 mb-1">
                {eligible.length} eligible{blocked.length > 0 ? `, ${blocked.length} blocked by a hard rule` : ''}.
                {eligible.some(c => c.violations && c.violations.length > 0) && (
                  <span> Warnings indicate soft-rule conflicts — you may still assign.</span>
                )}
              </p>
              {eligible.length === 0 && !search && (
                <p className="text-xs text-slate-400">
                  Nobody is cleanly eligible; the blocked list below shows why for each physician.
                </p>
              )}
              {[...eligible, ...((showBlocked || search) ? blocked : [])].map(candidate => {
                const isAssigning = assigning === candidate.physician_id
                const hasWarnings = candidate.violations && candidate.violations.length > 0
                const isBlocked = Boolean(candidate.is_hard_blocked)
                const isCurrent = candidate.physician_name === currentName || candidate.physician_id === slot.physician_id

                return (
                  <div
                    key={candidate.physician_id}
                    className={`flex items-start gap-3 p-3 rounded-lg border ${
                      isCurrent
                        ? 'border-slate-200 bg-slate-50'
                        : isBlocked
                          ? 'border-red-200 bg-red-50/60'
                          : hasWarnings
                            ? 'border-amber-200 bg-amber-50'
                            : 'border-emerald-200 bg-emerald-50'
                    }`}
                  >
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 mb-1.5">
                        <span className="font-semibold text-slate-800 text-sm truncate">
                          {candidate.physician_name}
                        </span>
                        {isCurrent && (
                          <span className="flex-shrink-0 text-xs text-slate-400 italic">current</span>
                        )}
                        {!isCurrent && !hasWarnings && (
                          <span className="flex-shrink-0 text-xs bg-emerald-100 text-emerald-700 px-1.5 py-0.5 rounded-full font-medium">
                            No violations
                          </span>
                        )}
                      </div>

                      {hasWarnings && (
                        <div className="flex flex-wrap gap-1.5">
                          {candidate.violations.map((v, vi) => {
                            const rule = typeof v === 'string' ? v : (v.rule || '')
                            const desc = typeof v === 'string' ? v : (v.description || v.rule || '')
                            return (
                              <span
                                key={vi}
                                className={`inline-block px-2 py-0.5 rounded-full text-xs font-medium border cursor-help ${badgeClass(rule)}`}
                                title={desc}
                              >
                                {rule || desc}
                              </span>
                            )
                          })}
                        </div>
                      )}
                    </div>

                    {!isCurrent && (
                      <button
                        onClick={() => handleAssign(candidate)}
                        disabled={assigning !== null}
                        title={isBlocked ? 'Overrides a hard rule — only do this if the physician has agreed to take the shift' : undefined}
                        className={`flex-shrink-0 flex items-center gap-1.5 px-3 py-1.5 rounded-md text-sm font-medium transition-colors ${
                          isBlocked
                            ? 'bg-white hover:bg-red-50 text-red-700 border border-red-300 disabled:bg-slate-100 disabled:text-slate-400'
                            : hasWarnings
                              ? 'bg-amber-500 hover:bg-amber-400 text-white disabled:bg-slate-300'
                              : 'bg-emerald-500 hover:bg-emerald-400 text-white disabled:bg-slate-300'
                        } disabled:cursor-not-allowed`}
                      >
                        {isAssigning ? (
                          <>
                            <svg className="w-3.5 h-3.5 animate-spin" fill="none" viewBox="0 0 24 24">
                              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v4l3-3-3-3v4a8 8 0 00-8 8h4z" />
                            </svg>
                            Assigning…
                          </>
                        ) : (
                          isBlocked ? 'Assign anyway' : hasWarnings ? 'Assign (override)' : 'Assign'
                        )}
                      </button>
                    )}
                  </div>
                )
              })}
              {blocked.length > 0 && !search && (
                <button
                  type="button"
                  onClick={() => setShowBlocked(v => !v)}
                  className="w-full mt-1 py-2 text-xs font-medium text-slate-500 hover:text-slate-700 border border-dashed border-slate-300 rounded-lg"
                >
                  {showBlocked
                    ? 'Hide physicians blocked by a hard rule'
                    : `Show ${blocked.length} physician${blocked.length === 1 ? '' : 's'} blocked by a hard rule (unavailable, already working, rest/consecutive limits…)`}
                </button>
              )}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="px-6 py-3 bg-slate-50 border-t border-slate-200 flex justify-end flex-shrink-0">
          <button
            onClick={onClose}
            disabled={assigning !== null}
            className="px-4 py-2 text-sm font-medium text-slate-600 hover:text-slate-800 bg-white border border-slate-300 hover:border-slate-400 rounded-md transition-colors disabled:opacity-50"
          >
            Cancel
          </button>
        </div>
      </div>
    </>
  )
}
