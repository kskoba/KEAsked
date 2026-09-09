import React, { useState, useEffect } from 'react'

export default function SettingsModal({ onClose }) {
  const [configDir, setConfigDir] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [changed, setChanged] = useState(false)

  useEffect(() => {
    let cancelled = false
    window.electronAPI.getConfigDir()
      .then(dir => { if (!cancelled) setConfigDir(dir) })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [])

  async function handleChooseFolder() {
    setError(null)
    const result = await window.electronAPI.chooseConfigDir()
    if (result.error) {
      setError(result.error)
    } else if (result.changed) {
      setConfigDir(result.path)
      setChanged(true)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40" onClick={onClose}>
      <div
        className="bg-white rounded-xl shadow-xl border border-slate-200 w-full max-w-lg mx-4"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-5 py-4 border-b border-slate-200">
          <h2 className="font-semibold text-slate-800 text-sm">Settings</h2>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-600 text-lg leading-none">✕</button>
        </div>

        <div className="px-5 py-4 space-y-3">
          <div>
            <div className="text-sm font-medium text-slate-700 mb-1">Physician Config Folder</div>
            <p className="text-xs text-slate-500 mb-2">
              Where <code className="font-mono">physicians.yaml</code>, <code className="font-mono">scheduler_config.yaml</code>,
              {' '}and <code className="font-mono">bytebloc.yaml</code> live. This is your organization&apos;s own data —
              it is never bundled with the app.
            </p>
            <div className="text-xs font-mono text-slate-700 bg-slate-50 border border-slate-200 rounded-md px-3 py-2 break-all">
              {loading ? 'Loading…' : (configDir || 'Not set')}
            </div>
          </div>

          {error && <p className="text-sm text-red-600">{error}</p>}
          {changed && !error && (
            <p className="text-sm text-emerald-700">
              Folder updated. Restart KEA Physician Scheduler to load data from the new location.
            </p>
          )}
        </div>

        <div className="flex justify-end gap-3 px-5 py-3 border-t border-slate-200">
          <button
            onClick={onClose}
            className="px-4 py-2 text-sm font-medium text-slate-700 bg-white border border-slate-300 rounded-md hover:bg-slate-50 transition-colors"
          >
            Close
          </button>
          <button
            onClick={handleChooseFolder}
            className="px-4 py-2 text-sm font-medium text-white bg-sky-600 rounded-md hover:bg-sky-700 transition-colors"
          >
            Change Folder…
          </button>
        </div>
      </div>
    </div>
  )
}
