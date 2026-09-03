import React, { useEffect } from 'react'

function formatDate(dateStr) {
  if (!dateStr) return dateStr
  const d = new Date(dateStr + 'T00:00:00')
  return d.toLocaleDateString('en-CA', { weekday: 'long', year: 'numeric', month: 'long', day: 'numeric' })
}

export default function AssignOrSwapModal({ assignment, onAssign, onSwap, onClose }) {
  useEffect(() => {
    function onKey(e) { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const name = assignment.physician_name || assignment.physician_id || '?'

  return (
    <>
      <div className="fixed inset-0 z-40 bg-black/50 backdrop-blur-sm" onClick={onClose} />
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="assign-or-swap-title"
        className="fixed z-50 top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-full max-w-sm bg-white rounded-xl shadow-2xl overflow-hidden"
        onClick={e => e.stopPropagation()}
      >
        <div className="px-5 py-4 bg-slate-50 border-b border-slate-200">
          <h2 id="assign-or-swap-title" className="text-base font-bold text-slate-800">{name}</h2>
          <p className="text-sm text-slate-600">
            <span className="font-mono font-semibold text-slate-800">{assignment.shift?.code}</span>
            {assignment.shift?.site && <span className="text-slate-500"> · {assignment.shift.site}</span>}
          </p>
          <p className="text-xs text-slate-500 mt-0.5">{formatDate(assignment.date)}</p>
        </div>

        <div className="px-5 py-4 space-y-2">
          <button
            onClick={onAssign}
            className="w-full flex items-center gap-3 px-4 py-3 rounded-lg border border-sky-200 bg-sky-50 hover:bg-sky-100 text-left transition-colors"
          >
            <svg className="w-5 h-5 text-sky-600 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z" />
            </svg>
            <div>
              <div className="text-sm font-semibold text-sky-900">Assign a different physician</div>
              <div className="text-xs text-sky-700">Pick someone else for this exact slot</div>
            </div>
          </button>

          <button
            onClick={onSwap}
            className="w-full flex items-center gap-3 px-4 py-3 rounded-lg border border-amber-200 bg-amber-50 hover:bg-amber-100 text-left transition-colors"
          >
            <svg className="w-5 h-5 text-amber-600 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M8 7h12m0 0l-4-4m4 4l-4 4m0 6H4m0 0l4 4m-4-4l4-4" />
            </svg>
            <div>
              <div className="text-sm font-semibold text-amber-900">Swap with another shift</div>
              <div className="text-xs text-amber-700">Trade this shift with someone else's</div>
            </div>
          </button>
        </div>

        <div className="px-5 py-3 bg-slate-50 border-t border-slate-200 flex justify-end">
          <button
            onClick={onClose}
            className="px-4 py-2 text-sm font-medium text-slate-600 hover:text-slate-800 bg-white border border-slate-300 hover:border-slate-400 rounded-md transition-colors"
          >
            Cancel
          </button>
        </div>
      </div>
    </>
  )
}
