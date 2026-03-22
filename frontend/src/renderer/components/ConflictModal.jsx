import React, { useState, useEffect, useCallback } from 'react'
import { assignPhysician, getSchedule } from '../api'

// Colour palette for violation badges
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

export default function ConflictModal({ slot, scheduleData, onAssigned, onClose }) {
  const [assigning, setAssigning] = useState(null)  // physicianId being assigned
  const [error, setError] = useState(null)

  // Close on Escape key
  useEffect(() => {
    function onKey(e) { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const handleAssign = useCallback(async (physicianId) => {
    const candidate = candidates.find(c => c.physician_id === physicianId)
    setAssigning(physicianId)
    setError(null)
    try {
      const result = await assignPhysician(slot.date, slot.shift.code, physicianId)
      if (result.success === false) {
        setError(result.message || 'Assignment failed.')
        setAssigning(null)
        return
      }
      // Refresh the schedule from server
      const updated = await getSchedule()
      onAssigned(updated, {
        physicianName: candidate?.physician_name || physicianId,
        violations: result.violations || [],
        date: slot.date,
        shiftCode: slot.shift.code,
      })
    } catch (err) {
      setError(err.message)
      setAssigning(null)
    }
  }, [slot, onAssigned, candidates])

  const candidates = slot.candidates || []
  const shiftCode = slot.shift?.code || ''
  const shiftTime = slot.shift?.time || ''
  const shiftSite = slot.shift?.site || ''

  return (
    <>
      {/* Backdrop */}
      <div
        className="fixed inset-0 z-40 bg-black/50 backdrop-blur-sm"
        onClick={onClose}
      />

      {/* Modal */}
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="conflict-modal-title"
        className="fixed z-50 top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-full max-w-lg bg-white rounded-xl shadow-2xl overflow-hidden"
        onClick={e => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-start justify-between px-6 py-4 bg-red-50 border-b border-red-100">
          <div>
            <div className="flex items-center gap-2 mb-0.5">
              <svg className="w-5 h-5 text-red-500" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}>
                <circle cx="12" cy="12" r="10" />
                <line x1="12" y1="8" x2="12" y2="12" />
                <line x1="12" y1="16" x2="12.01" y2="16" />
              </svg>
              <h2 id="conflict-modal-title" className="text-base font-bold text-slate-800">
                Resolve Unfilled Slot
              </h2>
            </div>
            <p className="text-sm text-slate-600">
              <span className="font-mono font-semibold text-slate-800">{shiftCode}</span>
              {shiftTime && <span className="text-slate-500"> · {shiftTime}</span>}
              {shiftSite && <span className="text-slate-500"> · {shiftSite}</span>}
            </p>
            <p className="text-xs text-slate-500 mt-0.5">{formatDate(slot.date)}</p>
          </div>
          <button
            onClick={onClose}
            className="text-slate-400 hover:text-slate-600 transition-colors p-1 rounded"
            aria-label="Close"
          >
            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        {/* Body */}
        <div className="px-6 py-4 max-h-96 overflow-auto">
          {error && (
            <div className="mb-3 p-3 bg-red-50 border border-red-200 rounded-md text-red-700 text-sm">
              {error}
            </div>
          )}

          {candidates.length === 0 ? (
            <div className="py-8 text-center text-slate-400 text-sm">
              No candidate physicians available for this slot.
            </div>
          ) : (
            <div className="space-y-3">
              <p className="text-xs text-slate-500 mb-3">
                {candidates.length} candidate{candidates.length !== 1 ? 's' : ''} available.
                Violations indicate rule conflicts — you may still assign.
              </p>

              {candidates.map((candidate) => {
                const isAssigning = assigning === candidate.physician_id
                const hasViolations = candidate.violations && candidate.violations.length > 0

                return (
                  <div
                    key={candidate.physician_id}
                    className={`flex items-start gap-3 p-3 rounded-lg border ${
                      hasViolations
                        ? 'border-amber-200 bg-amber-50'
                        : 'border-emerald-200 bg-emerald-50'
                    }`}
                  >
                    {/* Physician info */}
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 mb-1.5">
                        <span className="font-semibold text-slate-800 text-sm truncate">
                          {candidate.physician_name}
                        </span>
                        <span className="text-xs text-slate-400 font-mono flex-shrink-0">
                          {candidate.physician_id}
                        </span>
                        {!hasViolations && (
                          <span className="flex-shrink-0 text-xs bg-emerald-100 text-emerald-700 px-1.5 py-0.5 rounded-full font-medium">
                            No violations
                          </span>
                        )}
                      </div>

                      {/* Violation badges */}
                      {hasViolations && (
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

                    {/* Assign button */}
                    <button
                      onClick={() => handleAssign(candidate.physician_id)}
                      disabled={assigning !== null}
                      className={`flex-shrink-0 flex items-center gap-1.5 px-3 py-1.5 rounded-md text-sm font-medium transition-colors ${
                        hasViolations
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
                        <>
                          <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}>
                            <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                          </svg>
                          Assign
                        </>
                      )}
                    </button>
                  </div>
                )
              })}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="px-6 py-3 bg-slate-50 border-t border-slate-200 flex justify-end">
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
