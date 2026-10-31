import React, { useState, useEffect, useCallback, useMemo } from 'react'
import {
  getSkedStatus, getEmailStatus, getPhysiciansFull, getSkedPeriods, sendMonthlyRequests,
  planMonthlyRequests, previewMonthlyRequestTemplate, setApiBaseUrl, getApiBaseUrl
} from './api'

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

const MONTH_NAMES = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December']
const DOW_HEADERS = ['M', 'T', 'W', 'R', 'F', 'S', 'SU']   // Monday-first, the template's own letters

function defaultYearMonth() {
  // Default to next month -- the common case when running this.
  const now = new Date()
  const next = new Date(now.getFullYear(), now.getMonth() + 1, 1)
  return { year: next.getFullYear(), month: next.getMonth() + 1 }
}

function addDays(iso, n) {
  const [y, m, d] = iso.split('-').map(Number)
  const dt = new Date(Date.UTC(y, m - 1, d + n))
  return dt.toISOString().slice(0, 10)
}

function mondayIndex(iso) {
  const [y, m, d] = iso.split('-').map(Number)
  return (new Date(Date.UTC(y, m - 1, d)).getUTCDay() + 6) % 7
}

function shortDate(iso) {
  const [y, m, d] = iso.split('-').map(Number)
  return `${MONTH_NAMES[m - 1].slice(0, 3)} ${d}`
}

// Consecutive marked days sharing a label form one run -- the unit the
// template labels (row 2 above the first day) and the email lists.
function runsOf(marks) {
  const dates = Object.keys(marks).sort()
  const runs = []
  for (const d of dates) {
    const label = (marks[d] || '').trim()
    const last = runs[runs.length - 1]
    if (last && last.label === label && addDays(last.end, 1) === d) {
      last.end = d
      last.dates.push(d)
    } else {
      runs.push({ start: d, end: d, label, dates: [d] })
    }
  }
  return runs
}

export default function MonthlyRequestsPanel() {
  const [apiBaseResolved, setApiBaseResolved] = useState(false)
  const [skedConfigured, setSkedConfigured] = useState(null) // null while checking
  const [emailConfigured, setEmailConfigured] = useState(null)

  const initial = defaultYearMonth()
  const [year, setYear] = useState(initial.year)
  const [month, setMonth] = useState(initial.month)
  const [plan, setPlan] = useState(null)          // PeriodPlanResponse for year/month
  const [planError, setPlanError] = useState(null)
  const [marks, setMarks] = useState({})          // isoDate -> label (yellow days)

  const [periodId, setPeriodId] = useState('')
  const [label, setLabel] = useState('')
  const [opensAt, setOpensAt] = useState('')
  const [closesAt, setClosesAt] = useState('')
  const [templateMode, setTemplateMode] = useState('generate') // 'generate' | 'file'
  const [templatePath, setTemplatePath] = useState('')
  const [replaceExisting, setReplaceExisting] = useState(false)
  const [extraMessage, setExtraMessage] = useState('')

  const [previewing, setPreviewing] = useState(false)
  const [previewNote, setPreviewNote] = useState(null)

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

  // The month drives everything: period id, label, window defaults and the
  // suggested yellow days. Re-fetched whenever the scheduler picks a month.
  useEffect(() => {
    if (!apiBaseResolved) return
    let cancelled = false
    setPlan(null)
    setPlanError(null)
    planMonthlyRequests(year, month)
      .then((p) => {
        if (cancelled) return
        setPlan(p)
        const suggested = {}
        for (const m of p.suggested_marks) suggested[m.date] = m.label
        setMarks(suggested)
        if (!selectedExistingPeriodId) {
          setPeriodId(p.period_id)
          setLabel(p.label)
          setOpensAt(p.default_opens_at)
          setClosesAt(p.default_closes_at)
        }
      })
      .catch((err) => { if (!cancelled) setPlanError(err.message) })
    return () => { cancelled = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [apiBaseResolved, year, month])

  const handleLoadExistingPeriod = useCallback((id) => {
    setSelectedExistingPeriodId(id)
    setReplaceExisting(false)
    setTemplatePath('')
    if (!id) {
      if (plan) {
        setPeriodId(plan.period_id)
        setLabel(plan.label)
        setOpensAt(plan.default_opens_at)
        setClosesAt(plan.default_closes_at)
      }
      return
    }
    const period = (existingPeriods || []).find((p) => p.id === id)
    if (!period) return
    setPeriodId(period.id)
    setLabel(period.label)
    setOpensAt(period.opens_at.slice(0, 10))
    setClosesAt(period.closes_at.slice(0, 10))
    // A period id like 2027-03 tells us which month's calendar to show.
    const m = /^(\d{4})-(\d{2})$/.exec(period.id)
    if (m) {
      setYear(Number(m[1]))
      setMonth(Number(m[2]))
    }
  }, [existingPeriods, plan])

  const suggestedLabel = useMemo(() => {
    const out = {}
    if (!plan) return out
    for (const m of plan.suggested_marks) out[m.date] = m.label
    for (const m of plan.optional_holidays) if (!out[m.date]) out[m.date] = m.label
    return out
  }, [plan])

  const optionalByDate = useMemo(() => {
    const out = {}
    if (plan) for (const m of plan.optional_holidays) out[m.date] = m.label
    return out
  }, [plan])

  const toggleDay = useCallback((iso) => {
    setMarks((prev) => {
      const next = { ...prev }
      if (iso in next) {
        delete next[iso]
        return next
      }
      // A newly marked day takes the holiday's name, or extends the run
      // next to it (click along a spring-break week and the label follows).
      next[iso] = suggestedLabel[iso] ?? prev[addDays(iso, -1)] ?? prev[addDays(iso, 1)] ?? ''
      return next
    })
  }, [suggestedLabel])

  const setRunLabel = useCallback((run, value) => {
    setMarks((prev) => {
      const next = { ...prev }
      for (const d of run.dates) next[d] = value
      return next
    })
  }, [])

  const resetMarks = useCallback(() => {
    if (!plan) return
    const suggested = {}
    for (const m of plan.suggested_marks) suggested[m.date] = m.label
    setMarks(suggested)
  }, [plan])

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

  // Which template goes to sked with this send (see SendMonthlyRequestsRequest).
  const templateSource = selectedExistingPeriodId && !replaceExisting
    ? 'reuse'
    : templateMode === 'file' ? 'file' : 'generate'
  const templateReady =
    templateSource === 'reuse' ||
    (templateSource === 'file' && !!templatePath) ||
    (templateSource === 'generate' && !!plan && plan.master_ok)

  const formReady = skedConfigured && emailConfigured && periodId.trim() && label.trim() &&
    opensAt && closesAt && templateReady
  const canSend = formReady && !sending

  const marksPayload = useCallback(
    () => Object.entries(marks).sort(([a], [b]) => a.localeCompare(b)).map(([date, lbl]) => ({ date, label: lbl || '' })),
    [marks]
  )

  const buildPayload = useCallback((physicianIds) => {
    const payload = {
      period_id: periodId.trim(),
      label: label.trim(),
      opens_at: new Date(opensAt).toISOString(),
      closes_at: new Date(closesAt).toISOString(),
      extra_message: extraMessage.trim()
    }
    if (templateSource === 'file') payload.template_path = templatePath
    if (templateSource === 'generate') {
      payload.generate_template = true
      payload.year = year
      payload.month = month
      payload.marks = marksPayload()
    }
    if (physicianIds) payload.physician_ids = physicianIds
    return payload
  }, [periodId, label, opensAt, closesAt, extraMessage, templateSource, templatePath, year, month, marksPayload])

  const handlePreview = useCallback(async () => {
    if (!plan || previewing) return
    setPreviewing(true)
    setPreviewNote(null)
    try {
      const res = await previewMonthlyRequestTemplate({ year, month, marks: marksPayload(), closes_at: closesAt || null })
      let opened = false
      if (!remote && window.electronAPI?.openPath) {
        const err = await window.electronAPI.openPath(res.path)
        opened = !err
      }
      setPreviewNote(opened
        ? `Opened ${res.filename}.`
        : `Saved as ${res.path}${remote ? ' (on the backend machine).' : '.'}`)
    } catch (err) {
      setPreviewNote(`Preview failed: ${err.message}`)
    } finally {
      setPreviewing(false)
    }
  }, [plan, previewing, year, month, marksPayload, closesAt, remote])

  const handleSend = useCallback(async () => {
    if (!canSend) return
    const how = templateSource === 'reuse'
      ? 'The template already on sked is kept.'
      : templateSource === 'file'
        ? 'Your chosen .xlsx is uploaded as the template.'
        : `KEAsked builds the ${label} template from the calendar above and uploads it.`
    const ok = window.confirm(
      `Send shift-preference links for "${label}" to every active physician with an email on file?\n\n` +
      `${how}\n\nPhysicians missing an email will be listed afterward instead of being emailed.`
    )
    if (!ok) return

    setSending(true)
    setSendError(null)
    setResult(null)
    try {
      const res = await sendMonthlyRequests(buildPayload(null))
      setResult(res)
      // The period now exists on sked; a later re-send should reuse its template.
      getSkedPeriods('shift_request').then((r) => setExistingPeriods(r.periods)).catch(() => {})
    } catch (err) {
      setSendError(err.message)
    } finally {
      setSending(false)
    }
  }, [canSend, templateSource, label, buildPayload])

  const handleSendOne = useCallback(async (cfg) => {
    if (!formReady || !cfg.email || perSend[cfg.id] === 'sending') return
    setPerSend((s) => ({ ...s, [cfg.id]: 'sending' }))
    try {
      const res = await sendMonthlyRequests(buildPayload([cfg.id]))
      const failed = res.needs_attention[0]
      setPerSend((s) => ({ ...s, [cfg.id]: failed ? failed.detail || 'Send failed' : 'sent' }))
      if (!failed) setTimeout(() => setPerSend((s) => ({ ...s, [cfg.id]: undefined })), 2500)
    } catch (err) {
      setPerSend((s) => ({ ...s, [cfg.id]: err.message }))
    }
  }, [formReady, perSend, buildPayload])

  // ---- calendar cells -------------------------------------------------
  const calendarRows = useMemo(() => {
    if (!plan) return []
    const cells = []
    const lead = mondayIndex(plan.days[0].date)
    for (let i = 0; i < lead; i++) cells.push(null)
    for (const d of plan.days) cells.push(d)
    while (cells.length % 7) cells.push(null)
    const rows = []
    for (let i = 0; i < cells.length; i += 7) rows.push(cells.slice(i, i + 7))
    return rows
  }, [plan])

  const runs = useMemo(() => runsOf(marks), [marks])
  const calendarEditable = templateSource === 'generate'
  const yearOptions = useMemo(() => {
    const y = new Date().getFullYear()
    return [y - 1, y, y + 1, y + 2]
  }, [])

  return (
    <div className="h-screen flex flex-col bg-slate-50">
      <header
        className="flex items-center justify-between px-5 py-3 shadow-md flex-shrink-0"
        style={{ backgroundColor: '#1e293b' }}
      >
        <div>
          <h1 className="text-white font-bold text-base leading-tight">Send Monthly Shift Requests</h1>
          <p className="text-slate-400 text-xs">Pick the month, mark the holiday days, then email each physician's link</p>
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
                  ? 'Reusing this period\'s template on sked — tick "Replace the template" below to regenerate it.'
                  : 'Pick a period already sent to sked to resend it or target specific people.'}
            </span>
          </label>

          <div className="grid grid-cols-2 gap-4">
            <label className="block text-sm">
              <span className="text-slate-600 font-medium">Month</span>
              <select
                value={month}
                onChange={(e) => setMonth(Number(e.target.value))}
                disabled={!!selectedExistingPeriodId || sending}
                className="mt-1 w-full rounded border border-slate-300 bg-white px-3 py-1.5 text-sm"
              >
                {MONTH_NAMES.map((name, i) => <option key={name} value={i + 1}>{name}</option>)}
              </select>
            </label>
            <label className="block text-sm">
              <span className="text-slate-600 font-medium">Year</span>
              <select
                value={year}
                onChange={(e) => setYear(Number(e.target.value))}
                disabled={!!selectedExistingPeriodId || sending}
                className="mt-1 w-full rounded border border-slate-300 bg-white px-3 py-1.5 text-sm"
              >
                {(yearOptions.includes(year) ? yearOptions : [...yearOptions, year].sort()).map((y) => (
                  <option key={y} value={y}>{y}</option>
                ))}
              </select>
            </label>
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
              <span className="mt-1 block text-xs text-slate-400">Also written into the template's "Return by MIDNIGHT …" line.</span>
            </label>
          </div>

          {/* ---- Template section ------------------------------------- */}
          <div className="rounded-md border border-slate-200 p-4 space-y-3">
            <div className="flex items-center justify-between">
              <span className="text-sm text-slate-700 font-medium">Request template</span>
              {plan && (
                <span className="text-xs text-slate-500">
                  {shortDate(plan.days[0].date)} – {shortDate(plan.days[plan.days.length - 1].date)} · {plan.days.length} days
                  {plan.spillover_day ? ` (includes ${shortDate(plan.spillover_day)})` : ''}
                </span>
              )}
            </div>

            {selectedExistingPeriodId && (
              <label className="flex items-center gap-2 text-sm text-slate-700">
                <input type="checkbox" checked={replaceExisting} onChange={(e) => setReplaceExisting(e.target.checked)} />
                Replace the template already on sked with the one chosen below
              </label>
            )}

            {(!selectedExistingPeriodId || replaceExisting) && (
              <div className="flex flex-wrap gap-4 text-sm text-slate-700">
                <label className="flex items-center gap-2">
                  <input type="radio" name="templateMode" checked={templateMode === 'generate'} onChange={() => setTemplateMode('generate')} />
                  Build it from the calendar below
                </label>
                <label className="flex items-center gap-2">
                  <input type="radio" name="templateMode" checked={templateMode === 'file'} onChange={() => setTemplateMode('file')} />
                  Use my own .xlsx file
                </label>
              </div>
            )}

            {planError && <p className="text-xs text-red-700">Could not describe this month: {planError}</p>}
            {plan && !plan.master_ok && (
              <p className="text-xs text-red-700">The committed master template cannot be used: {plan.master_error}</p>
            )}

            {templateSource === 'file' && (
              <div className="flex items-center gap-2">
                <input
                  type="text"
                  readOnly={!remote}
                  value={templatePath}
                  onChange={remote ? (e) => setTemplatePath(e.target.value) : undefined}
                  placeholder={remote ? 'Type the path as it exists on the remote backend, e.g. /config/february_2027.xlsx' : 'No file selected'}
                  className={`flex-1 rounded border border-slate-300 px-3 py-1.5 text-sm ${remote ? 'bg-white focus:outline-none focus:ring-2 focus:ring-sky-400' : 'bg-slate-50 text-slate-600'}`}
                />
                <button
                  onClick={handleChooseTemplate}
                  title={remote ? 'Browses this computer, not the remote backend — usually you want to type the path instead' : undefined}
                  className="px-3 py-1.5 rounded bg-slate-700 hover:bg-slate-600 text-white text-sm transition-colors flex-shrink-0"
                >
                  Choose file…
                </button>
              </div>
            )}

            {plan && templateSource !== 'file' && (
              <div className="space-y-3">
                <p className="text-xs text-slate-500">
                  {calendarEditable
                    ? 'Click a day to mark it yellow (holiday / bonus window). Statutory holidays start marked; add spring break or anything else by hand.'
                    : 'Yellow days as suggested for this month — shown for reference while the template on sked is reused.'}
                </p>
                <table className="w-full text-center text-sm select-none">
                  <thead>
                    <tr className="text-xs text-slate-500">
                      {DOW_HEADERS.map((h) => <th key={h} className="py-1 font-medium">{h}</th>)}
                    </tr>
                  </thead>
                  <tbody>
                    {calendarRows.map((row, ri) => (
                      <tr key={ri}>
                        {row.map((d, ci) => {
                          if (!d) return <td key={ci} className="p-0.5"><div className="h-10" /></td>
                          const marked = d.date in marks
                          const optional = optionalByDate[d.date]
                          const title = marked
                            ? (marks[d.date] || 'Marked (no label)')
                            : optional ? `Optional holiday: ${optional} (click to mark)` : ''
                          return (
                            <td key={ci} className="p-0.5">
                              <button
                                type="button"
                                disabled={!calendarEditable}
                                onClick={() => toggleDay(d.date)}
                                title={title}
                                className={
                                  'w-full h-10 rounded border text-sm transition-colors ' +
                                  (marked
                                    ? 'bg-yellow-300 border-yellow-500 text-slate-900 font-semibold'
                                    : 'bg-white border-slate-200 text-slate-700 ' + (calendarEditable ? 'hover:bg-slate-100' : '')) +
                                  (optional && !marked ? ' border-dashed border-amber-400' : '') +
                                  (!d.in_month ? ' italic text-slate-400' : '') +
                                  (!calendarEditable ? ' cursor-default' : '')
                                }
                              >
                                {!d.in_month ? shortDate(d.date) : Number(d.date.slice(8, 10))}
                              </button>
                            </td>
                          )
                        })}
                      </tr>
                    ))}
                  </tbody>
                </table>

                {runs.length > 0 ? (
                  <div className="space-y-1.5">
                    {runs.map((run) => (
                      <div key={run.start} className="flex items-center gap-2 text-sm">
                        <span className="inline-block w-3 h-3 rounded-sm bg-yellow-300 border border-yellow-500 flex-shrink-0" />
                        <span className="text-slate-600 w-32 flex-shrink-0">
                          {run.start === run.end ? shortDate(run.start) : `${shortDate(run.start)} – ${shortDate(run.end)}`}
                        </span>
                        <input
                          type="text"
                          value={run.label}
                          disabled={!calendarEditable}
                          onChange={(e) => setRunLabel(run, e.target.value)}
                          placeholder="Label shown above these days (e.g. EPSB Spring Break)"
                          className="flex-1 rounded border border-slate-300 px-2 py-1 text-sm disabled:bg-slate-50"
                        />
                      </div>
                    ))}
                  </div>
                ) : (
                  <p className="text-xs text-slate-400">No yellow days this month.</p>
                )}

                {calendarEditable && (
                  <div className="flex items-center gap-3 text-xs">
                    <button type="button" onClick={resetMarks} className="text-sky-700 hover:text-sky-900 underline">
                      Reset to suggested
                    </button>
                    <button
                      type="button"
                      onClick={handlePreview}
                      disabled={previewing || !plan.master_ok}
                      className="px-2.5 py-1 rounded border border-slate-300 bg-white hover:bg-slate-100 text-slate-700 disabled:opacity-50"
                    >
                      {previewing ? 'Building…' : 'Preview the .xlsx'}
                    </button>
                    {previewNote && <span className="text-slate-500">{previewNote}</span>}
                  </div>
                )}
              </div>
            )}
          </div>

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
            <p className="text-xs text-amber-700">Fill in the period details above to enable sending.</p>
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
