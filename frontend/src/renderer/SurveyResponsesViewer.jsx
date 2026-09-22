import React, { useState, useEffect, useCallback } from 'react'
import { getSurveys, getSurveyCompletion, resendSurveyLink, setApiBaseUrl } from './api'

const STATUS_STYLES = {
  submitted: 'bg-emerald-100 text-emerald-800',
  draft: 'bg-amber-100 text-amber-800',
  not_started: 'bg-slate-100 text-slate-500'
}
const STATUS_LABELS = { submitted: 'Submitted', draft: 'Draft', not_started: 'Not started' }

const SITE_PREFERENCE_LABELS = {
  nehc: 'NEHC',
  rah_minor: 'RAH Minor',
  no_preference: 'No preference'
}

const ITEM_LABELS = {
  rest_after_late_shift: 'Rest after late shift',
  max_consecutive_nights: 'Max midnights in a row',
  prefer_singleton_nights: 'Prefer singleton nights',
  max_consecutive_shifts: 'Max shifts in a row'
}

function formatDate(iso) {
  if (!iso) return '—'
  return new Date(iso).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' })
}

async function copyToClipboard(text) {
  try {
    await navigator.clipboard.writeText(text)
    return true
  } catch {
    return false
  }
}

function ResponseDetail({ data }) {
  if (!data) return <p className="text-sm text-slate-400 italic">No response data.</p>
  return (
    <div className="text-sm space-y-3">
      <div className="grid grid-cols-2 gap-3">
        <div>
          <div className="text-xs text-slate-500 font-medium">Shifts/month</div>
          <div>{data.shiftsPerMonth ?? '—'}</div>
        </div>
        <div>
          <div className="text-xs text-slate-500 font-medium">Paired with</div>
          <div>{data.pairedWith || '—'}</div>
        </div>
        <div>
          <div className="text-xs text-slate-500 font-medium">Non-acute site preference</div>
          <div>{SITE_PREFERENCE_LABELS[data.nonAcuteSitePreference] || '—'}</div>
        </div>
      </div>
      <div>
        <div className="text-xs text-slate-500 font-medium mb-1">Weighted items</div>
        <ul className="space-y-1">
          {Object.entries(data.items || {}).map(([id, it]) => {
            const value = 'wanted' in it ? (it.wanted ? 'Yes' : 'No') : it.value
            if (it.points === 0 && !(('wanted' in it) ? it.wanted : true)) return null
            return (
              <li key={id} className="flex justify-between border-b border-slate-100 py-1">
                <span>{ITEM_LABELS[id] || id}: <strong>{value}</strong></span>
                <span className="text-indigo-700 font-semibold">{it.points} pt{it.points === 1 ? '' : 's'}</span>
              </li>
            )
          })}
        </ul>
      </div>
      {(data.freeTextRequests || []).length > 0 && (
        <div>
          <div className="text-xs text-slate-500 font-medium mb-1">Free-text requests</div>
          <ul className="space-y-2">
            {data.freeTextRequests.map((r, i) => (
              <li key={i} className="border border-slate-200 rounded-md p-2">
                <div className="flex justify-between items-start gap-2">
                  <span className="whitespace-pre-wrap">{r.text || <em className="text-slate-400">(empty)</em>}</span>
                  <span className="text-indigo-700 font-semibold flex-shrink-0">{r.points} pt{r.points === 1 ? '' : 's'}</span>
                </div>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}

export default function SurveyResponsesViewer() {
  const [apiBaseResolved, setApiBaseResolved] = useState(false)
  const [surveys, setSurveys] = useState(null)
  const [surveyId, setSurveyId] = useState('')
  const [completion, setCompletion] = useState(null)
  const [loadError, setLoadError] = useState(null)
  const [expandedId, setExpandedId] = useState(null)
  const [copiedId, setCopiedId] = useState(null)
  const [copiedAll, setCopiedAll] = useState(false)
  const [resendState, setResendState] = useState({}) // physicianId -> 'sending' | 'sent' | error message

  useEffect(() => {
    let cancelled = false
    window.electronAPI.getBackendConfig()
      .then(({ effectiveUrl }) => { if (!cancelled) setApiBaseUrl(effectiveUrl) })
      .finally(() => { if (!cancelled) setApiBaseResolved(true) })
    return () => { cancelled = true }
  }, [])

  useEffect(() => {
    if (!apiBaseResolved) return
    getSurveys()
      .then((r) => {
        setSurveys(r.surveys)
        if (r.surveys.length > 0) setSurveyId(r.surveys[0].id)
      })
      .catch((err) => setLoadError(err.message))
  }, [apiBaseResolved])

  const loadCompletion = useCallback((id) => {
    if (!id) return
    setLoadError(null)
    getSurveyCompletion(id).then(setCompletion).catch((err) => setLoadError(err.message))
  }, [])

  useEffect(() => { loadCompletion(surveyId) }, [surveyId, loadCompletion])

  const handleCopyOne = async (row) => {
    const ok = await copyToClipboard(JSON.stringify(row, null, 2))
    if (ok) { setCopiedId(row.physician_id); setTimeout(() => setCopiedId(null), 1500) }
  }
  const handleCopyAll = async () => {
    if (!completion) return
    const ok = await copyToClipboard(JSON.stringify(completion.rows, null, 2))
    if (ok) { setCopiedAll(true); setTimeout(() => setCopiedAll(false), 1500) }
  }

  const handleResend = async (physicianId, event) => {
    event.stopPropagation() // don't toggle the row's expand/collapse
    setResendState((s) => ({ ...s, [physicianId]: 'sending' }))
    try {
      await resendSurveyLink(surveyId, physicianId)
      setResendState((s) => ({ ...s, [physicianId]: 'sent' }))
      setTimeout(() => setResendState((s) => ({ ...s, [physicianId]: undefined })), 2500)
    } catch (err) {
      setResendState((s) => ({ ...s, [physicianId]: err.message }))
    }
  }

  const handleCloseClick = () => window.electronAPI.forceCloseSelf()

  return (
    <div className="h-screen flex flex-col bg-slate-50">
      <header className="flex items-center justify-between px-5 py-3 shadow-md flex-shrink-0" style={{ backgroundColor: '#1e293b' }}>
        <div>
          <h1 className="text-white font-bold text-base leading-tight">Survey Responses</h1>
          <p className="text-slate-400 text-xs">Completion tracking — encoding into solver rules is done separately</p>
        </div>
        <button
          onClick={handleCloseClick}
          title="Close"
          className="w-7 h-7 flex items-center justify-center rounded-full bg-red-600 hover:bg-red-500 text-white text-sm font-bold transition-colors"
        >
          ✕
        </button>
      </header>

      <div className="flex-1 overflow-auto p-6 max-w-4xl mx-auto w-full">
        {loadError && (
          <div className="rounded-md border border-red-300 bg-red-50 p-4 text-sm text-red-800 mb-4">{loadError}</div>
        )}

        {surveys !== null && surveys.length === 0 && !loadError && (
          <p className="text-sm text-slate-500">No survey periods found on sked yet.</p>
        )}

        {surveys && surveys.length > 0 && (
          <div className="flex items-center justify-between mb-4 gap-4">
            <select
              value={surveyId}
              onChange={(e) => setSurveyId(e.target.value)}
              className="rounded-md border border-slate-300 px-3 py-2 text-sm bg-white"
            >
              {surveys.map((s) => (
                <option key={s.id} value={s.id}>{s.label}</option>
              ))}
            </select>
            {completion && (
              <div className="flex items-center gap-3">
                <span className="text-sm text-slate-600 font-medium">
                  {completion.submitted_count} / {completion.total_active} submitted
                </span>
                <button
                  onClick={handleCopyAll}
                  className="text-xs px-3 py-1.5 rounded-md border border-slate-300 bg-white hover:bg-slate-50 text-slate-700"
                >
                  {copiedAll ? 'Copied!' : 'Copy all as JSON'}
                </button>
              </div>
            )}
          </div>
        )}

        {completion && (
          <div className="bg-white rounded-lg border border-slate-200 divide-y divide-slate-100 overflow-hidden">
            {completion.rows.map((row) => (
              <div key={row.physician_id}>
                <div
                  role="button"
                  tabIndex={0}
                  onClick={() => setExpandedId(expandedId === row.physician_id ? null : row.physician_id)}
                  onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') setExpandedId(expandedId === row.physician_id ? null : row.physician_id) }}
                  className="w-full flex items-center justify-between px-4 py-3 text-left hover:bg-slate-50 transition-colors cursor-pointer"
                >
                  <div>
                    <div className="font-medium text-slate-800 text-sm">{row.physician_name}</div>
                    <div className="text-xs text-slate-400">{row.updated_at ? `Updated ${formatDate(row.updated_at)}` : 'No response'}</div>
                  </div>
                  <div className="flex items-center gap-2">
                    <button
                      onClick={(e) => handleResend(row.physician_id, e)}
                      disabled={resendState[row.physician_id] === 'sending'}
                      title={!['sending', 'sent', undefined].includes(resendState[row.physician_id]) ? resendState[row.physician_id] : undefined}
                      className="text-xs px-2.5 py-1 rounded-md border border-indigo-300 bg-indigo-50 hover:bg-indigo-100 text-indigo-700 disabled:opacity-50 disabled:cursor-not-allowed"
                    >
                      {resendState[row.physician_id] === 'sending'
                        ? 'Sending…'
                        : resendState[row.physician_id] === 'sent'
                          ? 'Sent!'
                          : resendState[row.physician_id]
                            ? 'Failed — retry'
                            : row.status === 'not_started'
                              ? 'Send survey link'
                              : 'Resend survey link'}
                    </button>
                    <span className={`text-xs px-2.5 py-1 rounded-full font-medium ${STATUS_STYLES[row.status]}`}>
                      {STATUS_LABELS[row.status]}
                    </span>
                  </div>
                </div>
                {expandedId === row.physician_id && (
                  <div className="px-4 pb-4 bg-slate-50 border-t border-slate-100">
                    <div className="flex justify-end pt-3">
                      <button
                        onClick={() => handleCopyOne(row)}
                        className="text-xs px-2.5 py-1 rounded-md border border-slate-300 bg-white hover:bg-slate-50 text-slate-700"
                      >
                        {copiedId === row.physician_id ? 'Copied!' : 'Copy as JSON'}
                      </button>
                    </div>
                    <div className="pt-2">
                      <ResponseDetail data={row.data} />
                    </div>
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
