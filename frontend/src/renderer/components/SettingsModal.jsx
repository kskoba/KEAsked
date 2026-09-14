import React, { useState, useEffect } from 'react'

export default function SettingsModal({ onClose }) {
  const [configDir, setConfigDir] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [changed, setChanged] = useState(false)

  const [backendMode, setBackendMode] = useState('local')
  const [remoteUrl, setRemoteUrl] = useState('')
  const [effectiveBackendUrl, setEffectiveBackendUrl] = useState('')
  const [backendLoading, setBackendLoading] = useState(true)
  const [backendError, setBackendError] = useState(null)
  const [backendChanged, setBackendChanged] = useState(false)

  useEffect(() => {
    let cancelled = false
    window.electronAPI.getConfigDir()
      .then(dir => { if (!cancelled) setConfigDir(dir) })
      .finally(() => { if (!cancelled) setLoading(false) })
    window.electronAPI.getBackendConfig()
      .then(cfg => {
        if (cancelled) return
        setBackendMode(cfg.mode)
        setRemoteUrl(cfg.remoteUrl)
        setEffectiveBackendUrl(cfg.effectiveUrl)
      })
      .finally(() => { if (!cancelled) setBackendLoading(false) })
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

  async function handleSaveBackend() {
    setBackendError(null)
    const result = await window.electronAPI.setBackendConfig({ mode: backendMode, remoteUrl })
    if (result.error) {
      setBackendError(result.error)
    } else if (result.changed) {
      setBackendChanged(true)
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

          <div className="pt-2 border-t border-slate-200">
            <div className="text-sm font-medium text-slate-700 mb-1">Backend</div>
            <p className="text-xs text-slate-500 mb-2">
              KEA always starts its own local backend on launch. Point the app at a remote one instead
              — e.g. a Docker container on another machine — to run long solves there without tying up
              this computer.
            </p>

            {backendLoading ? (
              <div className="text-xs text-slate-500">Loading…</div>
            ) : (
              <>
                <div className="flex gap-2 mb-2">
                  <button
                    onClick={() => setBackendMode('local')}
                    className={`px-3 py-1.5 text-sm font-medium rounded-md border transition-colors ${
                      backendMode === 'local'
                        ? 'bg-sky-600 border-sky-600 text-white'
                        : 'bg-white border-slate-300 text-slate-600 hover:border-sky-400 hover:text-sky-600'
                    }`}
                  >
                    Local
                  </button>
                  <button
                    onClick={() => setBackendMode('remote')}
                    className={`px-3 py-1.5 text-sm font-medium rounded-md border transition-colors ${
                      backendMode === 'remote'
                        ? 'bg-sky-600 border-sky-600 text-white'
                        : 'bg-white border-slate-300 text-slate-600 hover:border-sky-400 hover:text-sky-600'
                    }`}
                  >
                    Remote
                  </button>
                </div>

                {backendMode === 'remote' && (
                  <input
                    type="text"
                    value={remoteUrl}
                    onChange={(e) => setRemoteUrl(e.target.value)}
                    placeholder="http://192.168.0.5:5000"
                    className="w-full text-sm font-mono px-3 py-2 border border-slate-300 rounded-md mb-2 focus:outline-none focus:ring-2 focus:ring-sky-500"
                  />
                )}

                <div className="text-xs font-mono text-slate-700 bg-slate-50 border border-slate-200 rounded-md px-3 py-2 break-all mb-2">
                  Currently connected to: {effectiveBackendUrl}
                </div>

                {backendError && <p className="text-sm text-red-600 mb-2">{backendError}</p>}
                {backendChanged && !backendError && (
                  <p className="text-sm text-emerald-700 mb-2">
                    Backend updated. Restart KEA Physician Scheduler to connect to it.
                  </p>
                )}

                <button
                  onClick={handleSaveBackend}
                  className="px-4 py-2 text-sm font-medium text-white bg-sky-600 rounded-md hover:bg-sky-700 transition-colors"
                >
                  Save Backend Setting
                </button>
              </>
            )}
          </div>
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
