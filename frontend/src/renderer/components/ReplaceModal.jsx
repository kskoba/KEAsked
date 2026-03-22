import React, { useState, useEffect, useCallback, useMemo } from 'react'
import { checkViolations, assignPhysician, getSchedule } from '../api'

const VIOLATION_COLORS = {
  spacing_22h: 'bg-orange-100 text-orange-700 border-orange-200',
  weekend_limit: 'bg-pink-100 text-pink-700 border-pink-200',
  anchor_limit: 'bg-purple-100 text-purple-700 border-purple-200',
  consecutive_limit: 'bg-red-100 text-red-700 border-red-200',
  forbidden_sites: 'bg-gray-100 text-gray-700 border-gray-200',
  paired_exclusions: 'bg-yellow-100 text-yellow-700 border-yellow-200',
  group_mix: 'bg-blue-100 text-blue-700 border-blue-200',
  singleton: 'bg-indigo-100 text-indigo-700 border-indigo-200',
  night_q: 'bg-slate-100 text-slate-700 border-slate-200'
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
  const [selectedId, setSelectedId] = useState(null)
  const [pendingViolations, setPendingViolations] = useState(null)
  const [checking, setChecking] = useState(false)
  const [confirming, setConfirming] = useState(false)
  const [assigning, setAssigning] = useState(false)
  const [error, setError] = useState(null)

  // Build physician list from importResult, falling back to scheduleData
  const physicians = useMemo(() => {
    if (importResult?.physicians?.length > 0) {
      return importResult.physicians
        .map(p => ({ id: p.physician_id, name: p.physician_name }))
        .sort((a, b) => a.name.localeCompare(b.name))
    }
    const seen = new Set()
    const list = []
    scheduleData.assignments.forEach(a => {
      if (!seen.has(a.physician_id)) {
        seen.add(a.physician_id)
        list.push({ id: a.physician_id, name: a.physician_name })
      }
    })
    return list.sort((a, b) => a.name.localeCompare(b.name))
  }, [importResult, scheduleData])

  const filtered = useMemo(() => {
    const q = search.toLowerCase().trim()
    if (!q) return physicians
    return physicians.filter(p => p.name.toLowerCase().includes(q))
  }, [physicians, search])

  const selectedPhysician = useMemo(
    () => physicians.find(p => p.id === selectedId) || null,
    [physicians, selectedId]
  )

  // Close on Escape (unless showing violation confirmation)
  useEffect(() => {
    function onKey(e) {
      if (e.key === 'Escape' && !confirming && !assigning) onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose, confirming, assigning])

  const doAssign = useCallback(async (physicianId, physicianName, knownViolations) => {
    setAssigning(true)
    setError(null)
    try {
      const result = await assignPhysician(slot.date, slot.shift.code, physicianId)
      if (result.success === false) {
        setError(result.message || 'Assignment failed.')
        setAssigning(false)
        return
      }
      const updated = await getSchedule()
      onAssigned(updated, {
        physicianName,
        violations: knownViolations,
        date: slot.date,
        shiftCode: slot.shift.code,
      })
    } catch (err) {
      setError(err.message)
      setAssigning(false)
    }
  }, [slot, onAssigned])

  const handleSelect = useCallback(async (physician) => {
    if (assigning || checking) return
    setSelectedId(physician.id)
    setPendingViolations(null)
    setConfirming(false)
    setError(null)
    setChecking(true)
    try {
      const result = await checkViolations(slot.date, slot.shift.code, physician.id)
      const viols = result.violations || []
      setPendingViolations(viols)
      if (viols.length > 0) {
        setConfirming(true)
      } else {
        // No violations — assign immediately
        await doAssign(physician.id, physician.name, [])
      }
    } catch (err) {
      setError(err.message)
    } finally {
      setChecking(false)
    }
  }, [slot, assigning, checking, doAssign])

  const currentName = slot.physician_name || slot.physician_id

  return (
    <>
      {/* Backdrop */}
      <div
        className="fixed inset-0 z-40 bg-black/50 backdrop-blur-sm"
        onClick={() => !assigning && onClose()}
      />

      {/* Modal */}
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="replace-modal-title"
        className="fixed z-50 top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-full max-w-md bg-white rounded-xl shadow-2xl overflow-hidden flex flex-col"
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
            onClick={() => !assigning && onClose()}
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
            onChange={e => {
              setSearch(e.target.value)
              setConfirming(false)
              setSelectedId(null)
              setPendingViolations(null)
            }}
            placeholder="Search physician name…"
            className="w-full border border-slate-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-sky-400 placeholder-slate-300"
            autoFocus
            disabled={assigning}
          />
        </div>

        {/* Physician list */}
        <div className="flex-1 overflow-auto px-3 py-2">
          {error && (
            <div className="mb-2 p-2 bg-red-50 border border-red-200 rounded text-red-700 text-xs">
              {error}
            </div>
          )}
          {filtered.length === 0 ? (
            <p className="text-center text-slate-400 text-sm py-8">No physicians found.</p>
          ) : (
            <div className="space-y-0.5">
              {filtered.map(physician => {
                const isSelected = physician.id === selectedId
                const isCurrent = physician.name === currentName
                return (
                  <button
                    key={physician.id}
                    onClick={() => !isCurrent && handleSelect(physician)}
                    disabled={isCurrent || assigning}
                    className={`w-full text-left px-3 py-2 rounded-lg text-sm transition-colors flex items-center justify-between gap-2
                      ${isSelected
                        ? 'bg-sky-50 border border-sky-300 text-sky-900'
                        : isCurrent
                          ? 'bg-slate-50 border border-transparent text-slate-400 cursor-not-allowed'
                          : 'hover:bg-slate-50 border border-transparent text-slate-800 cursor-pointer'
                      }`}
                  >
                    <span className="font-medium truncate">{physician.name}</span>
                    <div className="flex items-center gap-1.5 flex-shrink-0">
                      {isCurrent && (
                        <span className="text-xs text-slate-400 italic">current</span>
                      )}
                      {isSelected && checking && (
                        <svg className="w-3.5 h-3.5 animate-spin text-sky-500" fill="none" viewBox="0 0 24 24">
                          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v4l3-3-3-3v4a8 8 0 00-8 8h4z" />
                        </svg>
                      )}
                    </div>
                  </button>
                )
              })}
            </div>
          )}
        </div>

        {/* Violation confirmation panel */}
        {confirming && selectedPhysician && pendingViolations?.length > 0 && (
          <div className="border-t border-amber-200 bg-amber-50 px-4 py-4 flex-shrink-0">
            <p className="text-sm font-semibold text-amber-800 mb-2 flex items-center gap-1.5">
              <svg className="w-4 h-4 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v4m0 4h.01M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z" />
              </svg>
              Rule violations for {selectedPhysician.name}
            </p>
            <div className="flex flex-wrap gap-1.5 mb-3">
              {pendingViolations.map((v, i) => {
                const rule = typeof v === 'string' ? v : (v.rule || '')
                const desc = typeof v === 'string' ? v : (v.description || v.rule || '')
                return (
                  <span
                    key={i}
                    className={`inline-block px-2 py-0.5 rounded-full text-xs font-medium border cursor-help ${badgeClass(rule)}`}
                    title={desc}
                  >
                    {rule || desc}
                  </span>
                )
              })}
            </div>
            <p className="text-xs text-amber-700 mb-3">
              Place this physician anyway, overriding the rule{pendingViolations.length !== 1 ? 's' : ''}?
            </p>
            <div className="flex gap-2">
              <button
                onClick={() => doAssign(selectedPhysician.id, selectedPhysician.name, pendingViolations)}
                disabled={assigning}
                className="flex-1 py-2 text-sm font-semibold rounded-lg bg-amber-500 text-white hover:bg-amber-400 disabled:opacity-50 disabled:cursor-not-allowed transition-colors flex items-center justify-center gap-1.5"
              >
                {assigning ? (
                  <>
                    <svg className="w-3.5 h-3.5 animate-spin" fill="none" viewBox="0 0 24 24">
                      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v4l3-3-3-3v4a8 8 0 00-8 8h4z" />
                    </svg>
                    Placing…
                  </>
                ) : 'Place Anyway'}
              </button>
              <button
                onClick={() => { setConfirming(false); setSelectedId(null); setPendingViolations(null) }}
                disabled={assigning}
                className="flex-1 py-2 text-sm font-semibold rounded-lg bg-white border border-slate-300 text-slate-700 hover:bg-slate-50 disabled:opacity-50 transition-colors"
              >
                Pick Different
              </button>
            </div>
          </div>
        )}

        {/* Footer (only shown when not in violation confirmation) */}
        {!confirming && (
          <div className="px-6 py-3 bg-slate-50 border-t border-slate-200 flex justify-end flex-shrink-0">
            <button
              onClick={onClose}
              disabled={assigning}
              className="px-4 py-2 text-sm font-medium text-slate-600 hover:text-slate-800 bg-white border border-slate-300 hover:border-slate-400 rounded-md transition-colors disabled:opacity-50"
            >
              Cancel
            </button>
          </div>
        )}
      </div>
    </>
  )
}
