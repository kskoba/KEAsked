import React, { useState, useEffect, useCallback } from 'react'
import { getOnCallCandidates, assignOnCall } from '../api'

const MONTH_NAMES = [
  'Jan','Feb','Mar','Apr','May','Jun',
  'Jul','Aug','Sep','Oct','Nov','Dec',
]

function formatDate(dateStr) {
  const [, m, d] = dateStr.split('-')
  return `${MONTH_NAMES[parseInt(m, 10) - 1]} ${parseInt(d, 10)}`
}

export default function OnCallModal({ slot, onAssigned, onClose }) {
  const { date, callType } = slot

  const [candidates, setCandidates] = useState(null)
  const [loadError, setLoadError] = useState(null)
  const [assigning, setAssigning] = useState(null)   // physician_id being assigned
  const [assignError, setAssignError] = useState(null)

  // Load candidates on mount
  useEffect(() => {
    let cancelled = false
    getOnCallCandidates(date, callType)
      .then(data => { if (!cancelled) setCandidates(data) })
      .catch(err => { if (!cancelled) setLoadError(err.message) })
    return () => { cancelled = true }
  }, [date, callType])

  const handleAssign = useCallback(async (physicianId) => {
    setAssigning(physicianId)
    setAssignError(null)
    try {
      const updated = await assignOnCall(date, callType, physicianId)
      onAssigned(updated)
    } catch (err) {
      setAssignError(err.message)
      setAssigning(null)
    }
  }, [date, callType, onAssigned])

  const handleRemove = useCallback(async () => {
    setAssigning('__remove__')
    setAssignError(null)
    try {
      const updated = await assignOnCall(date, callType, '')
      onAssigned(updated)
    } catch (err) {
      setAssignError(err.message)
      setAssigning(null)
    }
  }, [date, callType, onAssigned])

  const currentId = candidates?.current_physician_id ?? null
  const title = `${callType === 'DOC' ? 'Day On Call' : 'Night On Call'} — ${formatDate(date)}`
  const constraintLabel = callType === 'DOC'
    ? 'No shift the day before'
    : 'No shift starting after 1200h'

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div className="bg-white rounded-xl shadow-2xl w-full max-w-md mx-4 flex flex-col max-h-[80vh]">

        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-slate-200 flex-shrink-0">
          <div>
            <h2 className="text-base font-semibold text-slate-800">{title}</h2>
            <p className="text-xs text-slate-500 mt-0.5">Constraint: {constraintLabel}</p>
          </div>
          <button
            onClick={onClose}
            className="text-slate-400 hover:text-slate-600 text-xl leading-none"
          >
            ✕
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto px-5 py-4">
          {loadError && (
            <div className="mb-3 p-2 bg-red-50 border border-red-200 rounded text-red-700 text-xs">
              {loadError}
            </div>
          )}
          {assignError && (
            <div className="mb-3 p-2 bg-red-50 border border-red-200 rounded text-red-700 text-xs">
              {assignError}
            </div>
          )}

          {candidates === null && !loadError && (
            <p className="text-sm text-slate-500 text-center py-6">Loading…</p>
          )}

          {candidates && (
            <>
              {/* Current assignment + remove */}
              {currentId && (
                <div className="mb-4 p-3 bg-sky-50 border border-sky-200 rounded-lg flex items-center justify-between">
                  <div>
                    <span className="text-xs font-medium text-sky-700 uppercase tracking-wide">Currently assigned</span>
                    <p className="text-sm font-semibold text-slate-800 mt-0.5">
                      {candidates.candidates.find(c => c.physician_id === currentId)?.physician_name ?? currentId}
                    </p>
                  </div>
                  <button
                    onClick={handleRemove}
                    disabled={!!assigning}
                    className="px-3 py-1.5 text-xs font-medium rounded border border-red-300 text-red-600 hover:bg-red-50 disabled:opacity-50 transition-colors"
                  >
                    {assigning === '__remove__' ? 'Removing…' : 'Remove'}
                  </button>
                </div>
              )}

              {/* Candidate list */}
              <p className="text-xs font-medium text-slate-500 uppercase tracking-wide mb-2">
                {currentId ? 'Change to' : 'Assign'}
              </p>
              <div className="space-y-1">
                {candidates.candidates.length === 0 && (
                  <p className="text-sm text-slate-500 text-center py-4">No physicians available.</p>
                )}
                {candidates.candidates.map(c => {
                  const isCurrent = c.physician_id === currentId
                  const hasViolation = c.violations.length > 0
                  const isAssigning = assigning === c.physician_id
                  return (
                    <button
                      key={c.physician_id}
                      onClick={() => !isCurrent && handleAssign(c.physician_id)}
                      disabled={!!assigning || isCurrent}
                      className={`w-full text-left px-3 py-2.5 rounded-lg border transition-colors
                        ${isCurrent
                          ? 'bg-sky-50 border-sky-300 cursor-default'
                          : hasViolation
                            ? 'border-amber-200 bg-amber-50 hover:bg-amber-100 disabled:opacity-50'
                            : 'border-slate-200 bg-white hover:bg-slate-50 disabled:opacity-50'
                        }`}
                    >
                      <div className="flex items-center justify-between">
                        <span className={`text-sm font-medium ${isCurrent ? 'text-sky-700' : 'text-slate-800'}`}>
                          {c.physician_name}
                          {isCurrent && <span className="ml-2 text-xs font-normal text-sky-500">current</span>}
                        </span>
                        {isAssigning && <span className="text-xs text-slate-400">Assigning…</span>}
                      </div>
                      {hasViolation && (
                        <div className="mt-1 space-y-0.5">
                          {c.violations.map((v, i) => (
                            <p key={i} className="text-xs text-amber-700 flex items-center gap-1">
                              <span>⚠</span> {v}
                            </p>
                          ))}
                        </div>
                      )}
                    </button>
                  )
                })}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
