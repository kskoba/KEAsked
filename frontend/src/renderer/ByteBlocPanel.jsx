import React, { useState, useEffect, useCallback } from 'react'
import { getByteBlocPreview, sendByteBlocRequests, setApiBaseUrl } from './api'

function formatSkedDate(iso) {
  if (!iso) return '—'
  // yyyy-MM-dd, avoid new Date() timezone shifting the displayed day.
  const [y, m, d] = iso.split('-').map(Number)
  if (!y || !m || !d) return iso
  return new Date(y, m - 1, d).toLocaleDateString([], { month: 'long', year: 'numeric' })
}

export default function ByteBlocPanel() {
  const [apiBaseResolved, setApiBaseResolved] = useState(false)
  const [preview, setPreview] = useState(null) // ByteBlocPreviewResponse | null
  const [loading, setLoading] = useState(false)
  const [loadError, setLoadError] = useState(null)
  const [useDelta, setUseDelta] = useState(true)

  const [confirmText, setConfirmText] = useState('')
  const [sending, setSending] = useState(false)
  const [sendResult, setSendResult] = useState(null)
  const [sendError, setSendError] = useState(null)

  // Separate BrowserWindow, separate renderer module state -- must
  // re-resolve the backend location here too (see RosterEditor.jsx).
  useEffect(() => {
    let cancelled = false
    window.electronAPI.getBackendConfig()
      .then(({ effectiveUrl }) => { if (!cancelled) setApiBaseUrl(effectiveUrl) })
      .finally(() => { if (!cancelled) setApiBaseResolved(true) })
    return () => { cancelled = true }
  }, [])

  const loadPreview = useCallback((delta) => {
    setLoading(true)
    setLoadError(null)
    setSendResult(null)
    return getByteBlocPreview(delta)
      .then(setPreview)
      .catch((err) => setLoadError(err.message))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => {
    if (!apiBaseResolved) return
    loadPreview(useDelta)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [apiBaseResolved])

  const handleToggleDelta = (checked) => {
    setUseDelta(checked)
    loadPreview(checked)
  }

  const handleCloseClick = () => window.electronAPI.forceCloseSelf()

  const canSend = preview?.configured && preview.request_count > 0 && confirmText === 'CONFIRM' && !sending

  const handleSend = useCallback(async () => {
    if (!canSend) return
    setSending(true)
    setSendError(null)
    setSendResult(null)
    try {
      const result = await sendByteBlocRequests(confirmText, useDelta)
      setSendResult(result)
      setConfirmText('')
      loadPreview(useDelta)
    } catch (err) {
      setSendError(err.message)
    } finally {
      setSending(false)
    }
  }, [canSend, confirmText, loadPreview, useDelta])

  return (
    <div className="h-screen flex flex-col bg-slate-50">
      <header
        className="flex items-center justify-between px-5 py-3 shadow-md flex-shrink-0"
        style={{ backgroundColor: '#1e293b' }}
      >
        <div>
          <h1 className="text-white font-bold text-base leading-tight">Send Availability to ByteBloc</h1>
          <p className="text-slate-400 text-xs">Full re-sync of every shift cell (NeedOff or Available), built from the currently-valid imported submissions</p>
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
        {loadError && (
          <div className="rounded-md border border-red-300 bg-red-50 p-4 text-sm text-red-800">{loadError}</div>
        )}

        {preview && !preview.configured && (
          <div className="rounded-md border border-amber-300 bg-amber-50 p-4 text-sm text-amber-900 space-y-1">
            {preview.warnings.map((w, i) => <p key={i}>{w}</p>)}
          </div>
        )}

        {preview && preview.configured && (
          <>
            <div className="bg-white rounded-lg shadow-sm border border-slate-200 p-5 space-y-3">
              <div className="flex items-center justify-between">
                <h2 className="font-semibold text-slate-800">
                  {formatSkedDate(preview.sked_start_date)}
                </h2>
                <button
                  onClick={() => loadPreview(useDelta)}
                  disabled={loading}
                  className="text-xs px-2.5 py-1 rounded-md border border-slate-300 bg-white hover:bg-slate-50 text-slate-700 disabled:opacity-50"
                >
                  {loading ? 'Refreshing…' : 'Refresh preview'}
                </button>
              </div>
              <div className="grid grid-cols-2 gap-3 text-sm">
                <div><span className="text-slate-500">Group / Location</span><div className="font-medium">{preview.group_code} / {preview.location_code}</div></div>
                <div><span className="text-slate-500">Requester ID</span><div className="font-mono text-xs">{preview.requester_id || '—'}</div></div>
                <div><span className="text-slate-500">Physicians</span><div className="font-medium">{preview.physician_count}</div></div>
                <div><span className="text-slate-500">Total requests</span><div className="font-medium">{preview.request_count}</div></div>
                <div><span className="text-slate-500">Marking unavailable</span><div className="font-medium">{preview.need_off_count}</div></div>
                <div><span className="text-slate-500">Marking available</span><div className="font-medium">{preview.available_count}</div></div>
              </div>
            </div>

            <div className="bg-white rounded-lg shadow-sm border border-slate-200 p-5 space-y-2">
              <label className="flex items-start gap-2.5 cursor-pointer">
                <input
                  type="checkbox"
                  checked={useDelta}
                  onChange={(e) => handleToggleDelta(e.target.checked)}
                  className="mt-0.5"
                />
                <span className="text-sm text-slate-700">
                  Only send changes since this period was last sent from <em>this computer</em>
                  <span className="block text-xs text-slate-400 mt-0.5">
                    This won't catch changes previously sent from a different computer, or made
                    directly in ByteBloc — turn it off to resend every cell regardless of history.
                  </span>
                </span>
              </label>
              {useDelta && (
                <p className="text-xs text-slate-500 pl-6">
                  {preview.used_delta
                    ? `${preview.skipped_unchanged_count} unchanged cell${preview.skipped_unchanged_count === 1 ? '' : 's'} skipped.`
                    : 'Nothing on record yet for this period from this computer — this is a full send.'}
                </p>
              )}
            </div>

            {preview.warnings.length > 0 && (
              <div className="rounded-md border border-amber-300 bg-amber-50 p-4 text-sm text-amber-900 space-y-1">
                {preview.warnings.map((w, i) => <p key={i}>{w}</p>)}
              </div>
            )}

            {preview.request_count === 0 ? (
              <div className="rounded-md border border-slate-200 bg-white p-4 text-sm text-slate-500">
                {useDelta && preview.used_delta
                  ? 'Nothing changed since the last send from this computer.'
                  : 'Nothing to send — import/pull in a period\'s submissions first.'}
              </div>
            ) : (
              <div className="bg-white rounded-lg shadow-sm border border-slate-200 p-5 space-y-3">
                <h2 className="font-semibold text-slate-800">By physician</h2>
                <div className="border border-slate-200 rounded-md divide-y divide-slate-100 max-h-64 overflow-auto">
                  {preview.by_physician.map((p) => (
                    <div key={p.physician_id} className="px-3 py-2 flex items-center justify-between text-sm">
                      <span className="text-slate-800">{p.physician_name}</span>
                      <span className="text-slate-500">{p.count} request{p.count === 1 ? '' : 's'}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}

            <div className="bg-white rounded-lg shadow-sm border border-red-200 p-5 space-y-3">
              <h2 className="font-semibold text-red-800 text-sm">Send to ByteBloc</h2>
              <p className="text-sm text-slate-700">
                This hits ByteBloc's live createShiftRequests endpoint — not a preview. Type{' '}
                <span className="font-mono font-semibold">CONFIRM</span> below to enable sending.
              </p>
              <input
                type="text"
                value={confirmText}
                onChange={(e) => setConfirmText(e.target.value)}
                placeholder="Type CONFIRM"
                disabled={sending}
                className="w-full px-3 py-2 text-sm border border-slate-300 rounded-md focus:outline-none focus:ring-2 focus:ring-red-500"
              />
              <button
                onClick={handleSend}
                disabled={!canSend}
                className="w-full py-2.5 rounded-md bg-red-600 hover:bg-red-500 disabled:bg-slate-300 disabled:cursor-not-allowed text-white text-sm font-medium transition-colors"
              >
                {sending ? 'Sending…' : `Send ${preview.request_count} request${preview.request_count === 1 ? '' : 's'} to ByteBloc`}
              </button>
              {sendError && <p className="text-sm text-red-600">{sendError}</p>}
              {sendResult && (
                <p className={`text-sm font-medium ${sendResult.ok ? 'text-emerald-700' : 'text-red-700'}`}>
                  {sendResult.ok ? '✓ Sent successfully' : `✗ ByteBloc status: ${sendResult.status}`}
                </p>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  )
}
