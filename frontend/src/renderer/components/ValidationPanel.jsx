import React, { useState } from 'react'
import { overrideIssue, overrideClear, overrideAll, getValidationSummary, getByteBlocPreview, sendByteBlocRequests } from '../api'

export default function ValidationPanel({ importResult, onImportResultUpdate }) {
  const { physicians = [], total_physicians, valid_physicians } = importResult
  const [expanded, setExpanded] = useState({})
  const [busy, setBusy] = useState(null)     // key of the in-flight override action, or null
  const [report, setReport] = useState(null) // null | { loading, error, items }
  const [byteBloc, setByteBloc] = useState(null) // null | { loading, error, preview, sendResult }

  const validCount = physicians.filter(p => p.is_valid).length
  const totalCount = total_physicians ?? physicians.length

  function toggleRow(id) {
    setExpanded(prev => ({ ...prev, [id]: !prev[id] }))
  }

  async function runOverrideAction(key, fn) {
    setBusy(key)
    try {
      const fresh = await fn()
      onImportResultUpdate?.(fresh)
    } catch (err) {
      alert(`Override failed: ${err.message}`)
    } finally {
      setBusy(null)
    }
  }

  function handleToggleIssue(physicianId, rule, isOverridden) {
    const key = `${physicianId}:${rule}`
    runOverrideAction(key, () =>
      isOverridden ? overrideClear(physicianId, rule) : overrideIssue(physicianId, rule)
    )
  }

  function handleOverrideAll(physicianId) {
    runOverrideAction(`all:${physicianId}`, () => overrideAll(physicianId))
  }

  async function handleGenerateReport() {
    setReport({ loading: true, error: null, items: [] })
    try {
      const res = await getValidationSummary()
      setReport({ loading: false, error: null, items: res.items })
    } catch (err) {
      setReport({ loading: false, error: err.message, items: [] })
    }
  }

  async function handleOpenByteBloc() {
    setByteBloc({ loading: true, error: null, preview: null, sendResult: null })
    try {
      const preview = await getByteBlocPreview()
      setByteBloc({ loading: false, error: null, preview, sendResult: null })
    } catch (err) {
      setByteBloc({ loading: false, error: err.message, preview: null, sendResult: null })
    }
  }

  async function handleConfirmSendToByteBloc(confirmationText) {
    setByteBloc(prev => ({ ...prev, sending: true }))
    try {
      const sendResult = await sendByteBlocRequests(confirmationText)
      setByteBloc(prev => ({ ...prev, sending: false, sendResult }))
    } catch (err) {
      setByteBloc(prev => ({ ...prev, sending: false, sendResult: { ok: false, status: err.message } }))
    }
  }

  return (
    <div className="bg-white rounded-xl shadow-sm border border-slate-200 overflow-hidden">
      {/* Summary bar */}
      <div className="flex items-center justify-between px-6 py-3 border-b border-slate-200 bg-slate-50">
        <h2 className="text-base font-semibold text-slate-800 flex items-center gap-2">
          <svg className="w-5 h-5 text-sky-500" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M17 20h5v-1a4 4 0 00-4-4H6a4 4 0 00-4 4v1h5M12 11a4 4 0 100-8 4 4 0 000 8z" />
          </svg>
          Physician Validation
        </h2>
        <div className="flex items-center gap-3">
          <button
            onClick={handleGenerateReport}
            className="px-3 py-1.5 text-xs font-semibold rounded-md border border-slate-300 bg-white text-slate-600 hover:bg-slate-100 transition-colors"
            title="List every remaining (non-overridden) error, grouped by physician"
          >
            Generate Error Report
          </button>
          <button
            onClick={handleOpenByteBloc}
            className="px-3 py-1.5 text-xs font-semibold rounded-md border border-emerald-600 bg-emerald-600 text-white hover:bg-emerald-700 transition-colors"
            title="Review and, only after typing CONFIRM, submit valid physicians' shift requests to ByteBloc"
          >
            Send Requests to ByteBloc
          </button>
          <div className={`px-3 py-1 rounded-full text-sm font-semibold ${
            validCount === totalCount
              ? 'bg-emerald-100 text-emerald-700'
              : 'bg-amber-100 text-amber-700'
          }`}>
            {validCount} / {totalCount} valid
          </div>
        </div>
      </div>

      {/* Progress bar */}
      <div className="h-1.5 bg-slate-100">
        <div
          className={`h-full transition-all ${validCount === totalCount ? 'bg-emerald-500' : 'bg-amber-500'}`}
          style={{ width: totalCount > 0 ? `${(validCount / totalCount) * 100}%` : '0%' }}
        />
      </div>

      {/* Table */}
      <div className="overflow-auto max-h-[65vh]">
        <table className="w-full text-sm">
          <thead className="sticky top-0 bg-slate-50 border-b border-slate-200">
            <tr>
              <th className="text-left px-6 py-2.5 font-medium text-slate-600 w-8"></th>
              <th className="text-left px-3 py-2.5 font-medium text-slate-600">Physician</th>
              <th className="text-right px-3 py-2.5 font-medium text-slate-600">Shifts Requested</th>
              <th className="text-center px-3 py-2.5 font-medium text-slate-600">Status</th>
              <th className="text-right px-6 py-2.5 font-medium text-slate-600">Issues</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {physicians.length === 0 && (
              <tr>
                <td colSpan={5} className="text-center py-8 text-slate-400">
                  No physician records found.
                </td>
              </tr>
            )}
            {physicians.map((physician) => {
              const isOpen = expanded[physician.physician_id]
              const issueCount = physician.issues ? physician.issues.length : 0
              const hasOverridableError = (physician.issues || []).some(i => i.severity === 'error' && !i.overridden)

              return (
                <React.Fragment key={physician.physician_id}>
                  <tr
                    className={`hover:bg-slate-50 ${issueCount > 0 ? 'cursor-pointer' : ''}`}
                    onClick={() => issueCount > 0 && toggleRow(physician.physician_id)}
                  >
                    {/* Expand chevron */}
                    <td className="px-6 py-3 text-slate-400">
                      {issueCount > 0 && (
                        <svg
                          className={`w-4 h-4 transition-transform ${isOpen ? 'rotate-90' : ''}`}
                          fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}
                        >
                          <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
                        </svg>
                      )}
                    </td>

                    {/* Name */}
                    <td className="px-3 py-3 font-medium text-slate-800">
                      {physician.physician_name}
                    </td>

                    {/* Shifts requested */}
                    <td className="px-3 py-3 text-right text-slate-700">
                      {physician.shifts_requested ?? '—'}
                    </td>

                    {/* Valid badge */}
                    <td className="px-3 py-3 text-center">
                      {physician.is_valid ? (
                        <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-emerald-100 text-emerald-700 text-xs font-medium">
                          <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2.5}>
                            <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                          </svg>
                          Valid
                        </span>
                      ) : (
                        <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-red-100 text-red-700 text-xs font-medium">
                          <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2.5}>
                            <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                          </svg>
                          Invalid
                        </span>
                      )}
                    </td>

                    {/* Issue count */}
                    <td className="px-6 py-3 text-right">
                      {issueCount > 0 ? (
                        <span className="inline-flex items-center gap-1.5">
                          <span className="inline-flex items-center justify-center min-w-[1.5rem] px-1.5 py-0.5 rounded-full bg-red-100 text-red-700 text-xs font-bold">
                            {issueCount}
                          </span>
                          <span className="text-xs font-medium text-sky-600">
                            {isOpen ? 'hide' : 'view / override'}
                          </span>
                        </span>
                      ) : (
                        <span className="text-slate-300">—</span>
                      )}
                    </td>
                  </tr>

                  {/* Expanded issues */}
                  {isOpen && issueCount > 0 && (
                    <tr className="bg-red-50">
                      <td colSpan={5} className="px-12 py-3">
                        {hasOverridableError && (
                          <div className="mb-2">
                            <button
                              onClick={(e) => { e.stopPropagation(); handleOverrideAll(physician.physician_id) }}
                              disabled={busy === `all:${physician.physician_id}`}
                              className="px-2.5 py-1 text-xs font-semibold rounded border border-amber-300 bg-amber-50 text-amber-800 hover:bg-amber-100 disabled:opacity-50 transition-colors"
                            >
                              {busy === `all:${physician.physician_id}` ? 'Overriding…' : 'Override All Errors'}
                            </button>
                          </div>
                        )}
                        <ul className="space-y-1.5">
                          {physician.issues.map((issue, i) => {
                            const key = `${physician.physician_id}:${issue.rule}`
                            return (
                              <li key={i} className={`flex items-start gap-2 text-sm ${issue.overridden ? 'opacity-50' : ''}`}>
                                <div className="w-20 flex-shrink-0">
                                  {issue.severity === 'error' && (
                                    <button
                                      onClick={(e) => { e.stopPropagation(); handleToggleIssue(physician.physician_id, issue.rule, issue.overridden) }}
                                      disabled={busy === key}
                                      className={`w-full px-2 py-0.5 text-xs font-semibold rounded border transition-colors disabled:opacity-50 ${
                                        issue.overridden
                                          ? 'border-slate-300 bg-white text-slate-600 hover:bg-slate-50'
                                          : 'border-amber-300 bg-amber-50 text-amber-800 hover:bg-amber-100'
                                      }`}
                                    >
                                      {busy === key ? '…' : issue.overridden ? 'Un-override' : 'Override'}
                                    </button>
                                  )}
                                </div>
                                <IssueIcon severity={issue.severity} />
                                <div className="flex-1">
                                  <span className={`font-medium mr-1 ${
                                    issue.severity === 'error' ? 'text-red-700' : 'text-amber-700'
                                  }`}>
                                    [{issue.severity?.toUpperCase() ?? 'INFO'}]
                                  </span>
                                  <span className={`text-slate-700 ${issue.overridden ? 'line-through' : ''}`}>{issue.message}</span>
                                  {issue.rule && (
                                    <span className="ml-2 text-xs text-slate-400 font-mono">({issue.rule})</span>
                                  )}
                                  {issue.overridden && (
                                    <span className="ml-2 text-xs font-semibold text-amber-600">OVERRIDDEN</span>
                                  )}
                                </div>
                              </li>
                            )
                          })}
                        </ul>
                      </td>
                    </tr>
                  )}
                </React.Fragment>
              )
            })}
          </tbody>
        </table>
      </div>

      {report && (
        <ErrorReportModal report={report} onClose={() => setReport(null)} />
      )}

      {byteBloc && (
        <ByteBlocModal
          state={byteBloc}
          onConfirmSend={handleConfirmSendToByteBloc}
          onClose={() => setByteBloc(null)}
        />
      )}
    </div>
  )
}

function ByteBlocModal({ state, onConfirmSend, onClose }) {
  const { loading, error, preview, sendResult, sending } = state
  const [confirmText, setConfirmText] = useState('')
  const canSend = !sending && !sendResult && preview?.configured && preview.request_count > 0 && confirmText === 'CONFIRM'

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40" onClick={onClose}>
      <div
        className="bg-white rounded-xl shadow-xl border border-slate-200 w-full max-w-2xl mx-4 max-h-[80vh] flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-5 py-4 border-b border-slate-200">
          <h2 className="font-semibold text-slate-800 text-sm">Send Requests to ByteBloc</h2>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-600 text-lg leading-none">✕</button>
        </div>

        <div className="px-5 py-4 overflow-auto flex-1 space-y-4">
          {loading && <p className="text-sm text-slate-500">Loading preview…</p>}
          {error && <p className="text-sm text-red-600">{error}</p>}

          {!loading && !error && preview && !preview.configured && (
            <p className="text-sm text-amber-700 bg-amber-50 border border-amber-200 rounded-md px-3 py-2">
              ByteBloc is not configured yet. Copy{' '}
              <code className="font-mono text-xs">scheduler/config/bytebloc_template.yaml</code> to{' '}
              <code className="font-mono text-xs">bytebloc.yaml</code> and fill it in.
            </p>
          )}

          {!loading && !error && preview && preview.configured && !sendResult && (
            <>
              <div className="text-sm text-slate-700 bg-slate-50 border border-slate-200 rounded-md px-3 py-2">
                <div><span className="font-medium">Destination:</span> group {preview.group_code || '—'} / location {preview.location_code || '—'}</div>
                <div><span className="font-medium">Schedule period starting:</span> {preview.sked_start_date || '—'}</div>
                <div><span className="font-medium">Requests:</span> {preview.request_count} shift request(s) across {preview.physician_count} physician(s)</div>
              </div>

              {preview.warnings.length > 0 && (
                <div className="text-sm text-amber-800 bg-amber-50 border border-amber-200 rounded-md px-3 py-2">
                  <div className="font-medium mb-1">Excluded / warnings:</div>
                  <ul className="list-disc list-inside space-y-0.5">
                    {preview.warnings.map((w, i) => <li key={i}>{w}</li>)}
                  </ul>
                </div>
              )}

              {preview.request_count > 0 && (
                <div className="border border-slate-200 rounded-md overflow-hidden">
                  <table className="w-full text-xs">
                    <thead className="bg-slate-50 border-b border-slate-200">
                      <tr>
                        <th className="text-left px-3 py-1.5 font-medium text-slate-600">Physician</th>
                        <th className="text-left px-3 py-1.5 font-medium text-slate-600">Day</th>
                        <th className="text-left px-3 py-1.5 font-medium text-slate-600">Shift</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-100">
                      {preview.items.slice(0, 100).map((item, i) => (
                        <tr key={i}>
                          <td className="px-3 py-1 text-slate-700">{item.physician_name}</td>
                          <td className="px-3 py-1 text-slate-700">{item.day}</td>
                          <td className="px-3 py-1 text-slate-700">{item.shift_code}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  {preview.items.length > 100 && (
                    <div className="px-3 py-1.5 text-xs text-slate-400 bg-slate-50">
                      …and {preview.items.length - 100} more
                    </div>
                  )}
                </div>
              )}

              {preview.request_count === 0 && (
                <p className="text-sm text-slate-500">There is nothing to send.</p>
              )}

              {preview.request_count > 0 && (
                <div className="border-t border-slate-200 pt-4">
                  <p className="text-sm text-slate-700 mb-2">
                    This will submit the requests above to ByteBloc's live system. This cannot be
                    undone from here. Type <span className="font-mono font-semibold">CONFIRM</span> below to proceed.
                  </p>
                  <input
                    type="text"
                    value={confirmText}
                    onChange={(e) => setConfirmText(e.target.value)}
                    placeholder="Type CONFIRM"
                    className="w-full px-3 py-2 text-sm border border-slate-300 rounded-md focus:outline-none focus:ring-2 focus:ring-emerald-500"
                  />
                </div>
              )}
            </>
          )}

          {sendResult && (
            <div className={`text-sm rounded-md px-3 py-2 border ${
              sendResult.ok
                ? 'text-emerald-800 bg-emerald-50 border-emerald-200'
                : 'text-red-800 bg-red-50 border-red-200'
            }`}>
              <div className="font-medium mb-1">{sendResult.ok ? 'Sent successfully.' : 'Send failed.'}</div>
              <div className="font-mono text-xs">{sendResult.status}</div>
            </div>
          )}
        </div>

        <div className="flex justify-end gap-3 px-5 py-3 border-t border-slate-200">
          <button
            onClick={onClose}
            className="px-4 py-2 text-sm font-medium text-slate-700 bg-white border border-slate-300 rounded-md hover:bg-slate-50 transition-colors"
          >
            {sendResult ? 'Close' : 'Cancel'}
          </button>
          {!sendResult && (
            <button
              onClick={() => onConfirmSend(confirmText)}
              disabled={!canSend}
              className="px-4 py-2 text-sm font-medium text-white bg-emerald-600 rounded-md hover:bg-emerald-700 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
            >
              {sending ? 'Sending…' : 'Send to ByteBloc'}
            </button>
          )}
        </div>
      </div>
    </div>
  )
}

function ErrorReportModal({ report, onClose }) {
  const [sent, setSent] = useState({})   // physician_name -> true, once "sent" (mockup only)

  function handleSendReminder(physicianName) {
    // TODO: mockup only — no email is actually sent yet.
    setSent(prev => ({ ...prev, [physicianName]: true }))
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40" onClick={onClose}>
      <div
        className="bg-white rounded-xl shadow-xl border border-slate-200 w-full max-w-2xl mx-4 max-h-[80vh] flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-5 py-4 border-b border-slate-200">
          <h2 className="font-semibold text-slate-800 text-sm">Remaining Validation Errors</h2>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-600 text-lg leading-none">✕</button>
        </div>

        <div className="px-5 py-4 overflow-auto flex-1">
          {report.loading && <p className="text-sm text-slate-500">Loading…</p>}
          {report.error && <p className="text-sm text-red-600">{report.error}</p>}
          {!report.loading && !report.error && report.items.length === 0 && (
            <p className="text-sm text-emerald-600 font-medium">No remaining errors — everything is either valid or overridden.</p>
          )}
          {!report.loading && !report.error && report.items.length > 0 && (
            <ul className="space-y-4">
              {report.items.map((item, i) => (
                <li key={i}>
                  <div className="flex items-center gap-2 mb-1">
                    <div className="font-semibold text-slate-800 text-sm">{item.physician_name}</div>
                    <button
                      onClick={() => handleSendReminder(item.physician_name)}
                      disabled={!!sent[item.physician_name]}
                      className={`px-2 py-0.5 text-xs font-semibold rounded border transition-colors ${
                        sent[item.physician_name]
                          ? 'border-emerald-300 bg-emerald-50 text-emerald-700 cursor-default'
                          : 'border-sky-300 bg-sky-50 text-sky-700 hover:bg-sky-100'
                      }`}
                    >
                      {sent[item.physician_name] ? 'Reminder sent' : 'Send Reminder Email'}
                    </button>
                  </div>
                  <ul className="list-disc list-inside space-y-0.5">
                    {item.errors.map((err, j) => (
                      <li key={j} className="text-sm text-slate-600">{err}</li>
                    ))}
                  </ul>
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="flex justify-end gap-3 px-5 py-3 border-t border-slate-200">
          <button
            onClick={onClose}
            className="px-4 py-2 text-sm font-medium text-slate-700 bg-white border border-slate-300 rounded-md hover:bg-slate-50 transition-colors"
          >
            Close
          </button>
        </div>
      </div>
    </div>
  )
}

function IssueIcon({ severity }) {
  if (severity === 'error') {
    return (
      <svg className="w-4 h-4 text-red-500 flex-shrink-0 mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}>
        <circle cx="12" cy="12" r="10" />
        <line x1="12" y1="8" x2="12" y2="12" />
        <line x1="12" y1="16" x2="12.01" y2="16" />
      </svg>
    )
  }
  return (
    <svg className="w-4 h-4 text-amber-500 flex-shrink-0 mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}>
      <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v4m0 4h.01M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z" />
    </svg>
  )
}
