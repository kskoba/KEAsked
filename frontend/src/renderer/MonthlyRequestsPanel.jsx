import React, { useState, useEffect, useCallback, useMemo } from 'react'
import { getSkedStatus, getEmailStatus, getPhysiciansFull, getSkedPeriods, sendMonthlyRequests, setApiBaseUrl, getApiBaseUrl } from './api'

// The native file picker only browses this computer's filesystem, so on a
// remote backend (e.g. the Unraid container) the template path field needs
// to accept a typed path instead -- one that exists on the *backend's* side
// (e.g. inside its mounted /config volume). Same pattern as DirectoryPicker.jsx.
function isRemoteBackend() {
  return !/^https?:\/\/(127\.0\.0\.1|localhost)(:|\/|$)/.test(getApiBaseUrl())
}

function displayName(p) {
  const full = `${p.first_name || ''} ${p.last_name || ''}`.trim()
  return full || p.name || p.id
}

function defaultPeriod() {
  // Default to next month -- the common case when running this.
  const now = new Date()
  const next = new Date(now.getFullYear(), now.getMonth() + 1, 1)
  const year = next.getFullYear()
  const month = next.getMonth() + 1 // 1-based
  const monthName = next.toLocaleString('en-US', { month: 'long' })
  const pad = (n) => String(n).padStart(2, '0')
  const daysInMonth = new Date(year, month, 0).getDate()
  return {
    periodId: `${year}-${pad(month)}`,
    label: `${monthName} ${year}`,
    opensAt: `${year}-${pad(month)}-01`,
    closesAt: `${year}-${pad(month)}-${pad(Math.min(15, daysInMonth))}`
  }
}

export default function MonthlyRequestsPanel() {
  const [apiBaseResolved, setApiBaseResolved] = useState(false)
  const [skedConfigured, setSkedConfigured] = useState(null) // null while checking
  const [emailConfigured, setEmailConfigured] = useState(null)

  const initial = defaultPeriod()
  const [periodId, setPeriodId] = useState(initial.periodId)
  const [label, setLabel] = useState(initial.label)
  const [opensAt, setOpensAt] = useState(initial.opensAt)
  const [closesAt, setClosesAt] = useState(initial.closesAt)
  const [templatePath, setTemplatePath] = useState('')
  const [extraMessage, setExtraMessage] = useState('')

  const [sending, setSending] = useState(false)
  const [sendError, setSendError] = useState(null)
  const [result, setResult] = useState(null) // SendMonthlyRequestsResponse | null

  const [physicians, setPhysicians] = useState(null)
  const [physicianFilter, setPhysicianFilter] = useState('')
  const [perSend, setPerSend] = useState({}) // physicianId -> 'sending' | 'sent' | error message

  const [existingPeriods, setExistingPeriods] = useState(null)
  const [selectedExistingPeriodId, setSelectedExistingPeriodId] = useState('') // '' = new period

  const remote = isRemoteBackend()

  // Separate BrowserWindow, separate renderer module state -- must
  // re-resolve the backend location here too (see RosterEditor.jsx).
  useEffect(() => {
    let cancelled = false
    window.electronAPI.getBackendConfig()
      .then(({ effectiveUrl }) => { if (!cancelled) setApiBaseUrl(effectiveUrl) })
      .finally(() => { if (!cancelled) setApiBaseResolved(true) })
    return () => { cancelled = true }
  }, [])

  useEffect(() => {
    if (!apiBaseResolved) return
    getSkedStatus().then((r) => setSkedConfigured(r.configured)).catch(() => setSkedConfigured(false))
    getEmailStatus().then((r) => setEmailConfigured(r.configured)).catch(() => setEmailConfigured(false))
    getPhysiciansFull().then((r) => setPhysicians(r.physicians)).catch(() => setPhysicians([]))
    getSkedPeriods('shift_request').then((r) => setExistingPeriods(r.periods)).catch(() => setExistingPeriods([]))
  }, [apiBaseResolved])

  const handleLoadExistingPeriod = useCallback((id) => {
    setSelectedExistingPeriodId(id)
    if (!id) {
      const fresh = defaultPeriod()
      setPeriodId(fresh.periodId)
      setLabel(fresh.label)
      setOpensAt(fresh.opensAt)
      setClosesAt(fresh.closesAt)
      setTemplatePath('')
      return
    }
    const period = (existingPeriods || []).find((p) => p.id === id)
    if (!period) return
    setPeriodId(period.id)
    setLabel(period.label)
    setOpensAt(period.opens_at.slice(0, 10))
    setClosesAt(period.closes_at.slice(0, 10))
    setTemplatePath('') // reuse whatever's already uploaded to this period on sked
  }, [existingPeriods])

  const activePhysicians = useMemo(() => {
    const list = (physicians || []).filter((p) => p.active)
    list.sort((a, b) => (a.last_name || a.name).localeCompare(b.last_name || b.name))
    const q = physicianFilter.trim().toLowerCase()
    if (!q) return list
    return list.filter((p) => displayName(p).toLowerCase().includes(q))
  }, [physicians, physicianFilter])

  const handleChooseTemplate = useCallback(async () => {
    const path = await window.electronAPI.openFile([{ name: 'Excel Files', extensions: ['xlsx', 'xls'] }])
    if (path) setTemplatePath(path)
  }, [])

  const handleCloseClick = () => window.electronAPI.forceCloseSelf()

  // Template is only required for a brand-new period -- loading an existing
  // one (selectedExistingPeriodId set) reuses whatever's already uploaded
  // there unless the scheduler explicitly picks a new file to replace it.
  const formReady = skedConfigured && emailConfigured && periodId.trim() && label.trim() &&
    opensAt && closesAt && (templatePath || selectedExistingPeriodId)
  const canSend = formReady && !sending

  const handleSend = useCallback(async () => {
    if (!canSend) return
    const ok = window.confirm(
      `Send shift-preference links for "${label}" to every active physician with an email on file?\n\n` +
      `Physicians missing an email will be listed afterward instead of being emailed.`
    )
    if (!ok) return

    setSending(true)
    setSendError(null)
    setResult(null)
    try {
      const res = await sendMonthlyRequests({
        period_id: periodId.trim(),
        label: label.trim(),
        opens_at: new Date(opensAt).toISOString(),
        closes_at: new Date(closesAt).toISOString(),
        template_path: templatePath,
        extra_message: extraMessage.trim()
      })
      setResult(res)
    } catch (err) {
      setSendError(err.message)
    } finally {
      setSending(false)
    }
  }, [canSend, periodId, label, opensAt, closesAt, templatePath, extraMessage])

  const handleSendOne = useCallback(async (cfg) => {
    if (!formReady || !cfg.email || perSend[cfg.id] === 'sending') return
    setPerSend((s) => ({ ...s, [cfg.id]: 'sending' }))
    try {
      const res = await sendMonthlyRequests({
        period_id: periodId.trim(),
        label: label.trim(),
        opens_at: new Date(opensAt).toISOString(),
        closes_at: new Date(closesAt).toISOString(),
        template_path: templatePath,
        extra_message: extraMessage.trim(),
        physician_ids: [cfg.id]
      })
      const failed = res.needs_attention[0]
      setPerSend((s) => ({ ...s, [cfg.id]: failed ? failed.detail || 'Send failed' : 'sent' }))
      if (!failed) setTimeout(() => setPerSend((s) => ({ ...s, [cfg.id]: undefined })), 2500)
    } catch (err) {
      setPerSend((s) => ({ ...s, [cfg.id]: err.message }))
    }
  }, [formReady, perSend, periodId, label, opensAt, closesAt, templatePath, extraMessage])

  return (
    <div className="h-screen flex flex-col bg-slate-50">
      <header
        className="flex items-center justify-between px-5 py-3 shadow-md flex-shrink-0"
        style={{ backgroundColor: '#1e293b' }}
      >
        <div>
          <h1 className="text-white font-bold text-base leading-tight">Send Monthly Shift Requests</h1>
          <p className="text-slate-400 text-xs">Push roster + template to sked, then email each physician's link</p>
        </div>
        <button
          onClick={handleCloseClick}
          title="Close"
          className="w-7 h-7 flex items-center justify-center rounded-full bg-red-600 hover:bg-red-500 text-white text-sm font-bold transition-colors"
        >
          ✕
        </button>
      </header>

      <div className="flex-1 overflow-auto p-6 max-w-2xl mx-auto w-full space-y-6">
        {(skedConfigured === false || emailConfigured === false) && (
          <div className="rounded-md border border-amber-300 bg-amber-50 p-4 text-sm text-amber-900 space-y-1">
            {skedConfigured === false && (
              <p>
                sked is not configured. Copy <code>scheduler/config/sked_template.yaml</code> to{' '}
                <code>sked.yaml</code> in the physician config folder and fill it in.
              </p>
            )}
            {emailConfigured === false && (
              <p>
                Email sending is not configured. Copy <code>scheduler/config/email_template.yaml</code> to{' '}
                <code>email.yaml</code> in the physician config folder and fill it in.
              </p>
            )}
          </div>
        )}

        <div className="bg-white rounded-lg shadow-sm border border-slate-200 p-5 space-y-4">
          <label className="block text-sm">
            <span className="text-slate-600 font-medium">Load existing period</span>
            <select
              value={selectedExistingPeriodId}
              onChange={(e) => handleLoadExistingPeriod(e.target.value)}
              disabled={sending || !existingPeriods}
              className="mt-1 w-full rounded border border-slate-300 bg-white px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-sky-400"
            >
              <option value="">New period…</option>
              {existingPeriods?.map((p) => (
                <option key={p.id} value={p.id}>{p.label} ({p.id})</option>
              ))}
            </select>
            <span className="mt-1 block text-xs text-slate-400">
              {existingPeriods === null
                ? 'Loading periods from sked…'
                : selectedExistingPeriodId
                  ? 'Reusing this period\'s existing template — pick a new file below only to replace it.'
                  : 'Pick a period already sent to sked to resend it or target specific people, without re-choosing the template file.'}
            </span>
          </label>

          <div className="grid grid-cols-2 gap-4">
            <label className="block text-sm">
              <span className="text-slate-600 font-medium">Period ID</span>
              <input
                type="text"
                value={periodId}
                onChange={(e) => setPeriodId(e.target.value)}
                readOnly={!!selectedExistingPeriodId}
                title={selectedExistingPeriodId ? 'Locked while reusing an existing period — pick "New period…" above to change it' : undefined}
                className={`mt-1 w-full rounded border border-slate-300 px-3 py-1.5 text-sm ${selectedExistingPeriodId ? 'bg-slate-50 text-slate-500 cursor-default' : ''}`}
                placeholder="e.g. 2027-02"
              />
            </label>
            <label className="block text-sm">
              <span className="text-slate-600 font-medium">Label (shown to physicians)</span>
              <input
                type="text"
                value={label}
                onChange={(e) => setLabel(e.target.value)}
                className="mt-1 w-full rounded border border-slate-300 px-3 py-1.5 text-sm"
                placeholder="e.g. February 2027"
              />
            </label>
            <label className="block text-sm">
              <span className="text-slate-600 font-medium">Submission window opens</span>
              <input
                type="date"
                value={opensAt}
                onChange={(e) => setOpensAt(e.target.value)}
                className="mt-1 w-full rounded border border-slate-300 px-3 py-1.5 text-sm"
              />
            </label>
            <label className="block text-sm">
              <span className="text-slate-600 font-medium">Submission window closes</span>
              <input
                type="date"
                value={closesAt}
                onChange={(e) => setClosesAt(e.target.value)}
                className="mt-1 w-full rounded border border-slate-300 px-3 py-1.5 text-sm"
              />
            </label>
          </div>

          <label className="block text-sm">
            <span className="text-slate-600 font-medium">
              Master schedule template (.xlsx)
              {selectedExistingPeriodId && <span className="text-slate-400 font-normal"> — optional, reusing what's already on sked</span>}
            </span>
            <div className="mt-1 flex items-center gap-2">
              <input
                type="text"
                readOnly={!remote}
                value={templatePath}
                onChange={remote ? (e) => setTemplatePath(e.target.value) : undefined}
                placeholder={
                  remote ? 'Type the path as it exists on the remote backend, e.g. /config/february_2027.xlsx' :
                  selectedExistingPeriodId ? 'Using the template already uploaded for this period' :
                  'No file selected'
                }
                className={`flex-1 rounded border border-slate-300 px-3 py-1.5 text-sm ${remote ? 'bg-white focus:outline-none focus:ring-2 focus:ring-sky-400' : 'bg-slate-50 text-slate-600'}`}
              />
              <button
                onClick={handleChooseTemplate}
                title={remote ? 'Browses this computer, not the remote backend — usually you want to type the path instead' : undefined}
                className="px-3 py-1.5 rounded bg-slate-700 hover:bg-slate-600 text-white text-sm transition-colors flex-shrink-0"
              >
                {selectedExistingPeriodId ? 'Replace file…' : 'Choose file…'}
              </button>
            </div>
            {remote && (
              <p className="mt-1.5 text-xs text-amber-600">
                Backend is remote — paths are resolved on the backend's filesystem, not this computer.
              </p>
            )}
          </label>

          <label className="block text-sm">
            <span className="text-slate-600 font-medium">Additional message (included in the email)</span>
            <textarea
              value={extraMessage}
              onChange={(e) => setExtraMessage(e.target.value)}
              rows={3}
              placeholder="e.g. Reminder: block your holiday requests before submitting."
              className="mt-1 w-full rounded border border-slate-300 px-3 py-1.5 text-sm"
            />
          </label>

          <button
            onClick={handleSend}
            disabled={!canSend}
            className="w-full py-2.5 rounded-md bg-sky-700 hover:bg-sky-600 disabled:bg-slate-300 disabled:cursor-not-allowed text-white text-sm font-medium transition-colors"
          >
            {sending ? 'Sending…' : 'Send Monthly Shift Requests'}
          </button>
        </div>

        {sendError && (
          <div className="rounded-md border border-red-300 bg-red-50 p-4 text-sm text-red-800">{sendError}</div>
        )}

        <div className="bg-white rounded-lg shadow-sm border border-slate-200 p-5 space-y-3">
          <div className="flex items-center justify-between">
            <h2 className="font-semibold text-slate-800">Send to one physician</h2>
            <span className="text-xs text-slate-400">Uses the period/template/message above</span>
          </div>
          <input
            type="text"
            value={physicianFilter}
            onChange={(e) => setPhysicianFilter(e.target.value)}
            placeholder="Filter by name…"
            className="w-full rounded border border-slate-300 px-3 py-1.5 text-sm"
          />
          {!formReady && (
            <p className="text-xs text-amber-700">Fill in the period details above to enable sending (a template is only required for a new period).</p>
          )}
          <div className="border border-slate-200 rounded-md divide-y divide-slate-100 max-h-80 overflow-auto">
            {activePhysicians.length === 0 && (
              <p className="px-3 py-3 text-sm text-slate-400">
                {physicians === null ? 'Loading physicians…' : 'No matching active physicians.'}
              </p>
            )}
            {activePhysicians.map((p) => {
              const state = perSend[p.id]
              const isSending = state === 'sending'
              const isSent = state === 'sent'
              const isError = state && !isSending && !isSent
              return (
                <div key={p.id} className="px-3 py-2 flex items-center justify-between gap-3 text-sm">
                  <div className="min-w-0">
                    <div className="font-medium text-slate-800 truncate">{displayName(p)}</div>
                    <div className={`text-xs truncate ${p.email ? 'text-slate-400' : 'text-amber-700'}`}>
                      {p.email || 'No email on file'}
                    </div>
                  </div>
                  <button
                    onClick={() => handleSendOne(p)}
                    disabled={!formReady || !p.email || isSending}
                    title={isError ? state : undefined}
                    className={
                      'flex-shrink-0 text-xs px-2.5 py-1 rounded-md border font-medium disabled:opacity-50 disabled:cursor-not-allowed transition-colors ' +
                      (isError
                        ? 'border-red-300 bg-red-50 text-red-700 hover:bg-red-100'
                        : 'border-sky-300 bg-sky-50 text-sky-700 hover:bg-sky-100')
                    }
                  >
                    {isSending ? 'Sending…' : isSent ? 'Sent!' : isError ? 'Failed — retry' : 'Send shift request'}
                  </button>
                </div>
              )
            })}
          </div>
        </div>

        {result && (
          <div className="bg-white rounded-lg shadow-sm border border-slate-200 p-5 space-y-4">
            <div className="flex items-center justify-between">
              <h2 className="font-semibold text-slate-800">Results</h2>
              <span className="text-sm text-slate-500">
                {result.sent_count} sent · {result.needs_attention.length} need attention
              </span>
            </div>

            {result.needs_attention.length > 0 && (
              <div>
                <div className="flex items-center justify-between mb-2">
                  <h3 className="text-sm font-medium text-slate-700">Needs attention</h3>
                  <button
                    onClick={() => window.electronAPI.openRosterWindow()}
                    className="text-xs text-sky-700 hover:text-sky-900 underline"
                  >
                    Open Physician Roster to fix →
                  </button>
                </div>
                <div className="border border-slate-200 rounded-md divide-y divide-slate-100 max-h-72 overflow-auto">
                  {result.needs_attention.map((r) => (
                    <div key={r.physician_id} className="px-3 py-2 text-sm flex items-start justify-between gap-3">
                      <div>
                        <div className="font-medium text-slate-800">{r.physician_name}</div>
                        <div className="text-xs text-slate-500">{r.detail}</div>
                      </div>
                      <span
                        className={
                          'flex-shrink-0 text-xs px-2 py-0.5 rounded-full font-medium ' +
                          (r.status === 'no_email' ? 'bg-amber-100 text-amber-800' : 'bg-red-100 text-red-800')
                        }
                      >
                        {r.status === 'no_email' ? 'no email' : 'send failed'}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
