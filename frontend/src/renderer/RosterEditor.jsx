import React, { useState, useEffect, useMemo, useCallback, useRef } from 'react'
import { getPhysiciansFull, updatePhysician, createPhysician, removePhysician, setApiBaseUrl, getSkedPeriods, getPhysicianLink } from './api'

// Mirrors scheduler/backend/config.py's VALID_SITES / GROUP_B_PREFS /
// VALID_RULE_OVERRIDES, and the non-call shift start times from
// scheduler/backend/shifts.py's _START_HOURS.
const VALID_SITES = ['NEHC', 'RAH A side', 'RAH B side', 'RAH I side', 'RAH F side']
const GROUP_B_PREFS = [
  { value: '', label: 'No preference' },
  { value: 'nehc', label: 'NEHC' },
  { value: 'rah', label: 'RAH (I or F side)' },
  { value: 'rah_f', label: 'RAH F side specifically' }
]
const SHIFT_TIMES = ['0600h', '0900h', '1000h', '1200h', '1400h', '1500h', '1600h', '1700h', '1800h', '2000h', '2400h']

// Plain-English labels for the raw rule_overrides keys validator.py
// checks against (scheduler/backend/config.py's VALID_RULE_OVERRIDES).
const RULE_OVERRIDE_LABELS = {
  min_valid_days: 'Minimum number of valid days offered',
  min_valid_blocks: 'Minimum number of valid blocks offered',
  min_weekend_days: 'Minimum number of valid weekend days offered',
  min_anchored_days: 'Minimum number of anchored days offered',
  min_blocks_per_day: 'Minimum blocks per day to count as a valid day'
}
const RULE_OVERRIDE_KEYS = Object.keys(RULE_OVERRIDE_LABELS)

// Prefer first_name + last_name over the raw `name` field: many roster
// entries have `name` set to just the last name (a historical artifact),
// even when first_name IS on file -- combining them gives the fuller,
// more correct display without touching the underlying yaml `name` value
// (which has real significance elsewhere: it must match cell A1 of the
// physician's Excel submission, so it's never silently rewritten here).
function displayName(p) {
  const full = `${p.first_name || ''} ${p.last_name || ''}`.trim()
  return full || p.name || p.id
}

export default function RosterEditor() {
  const [physicians, setPhysicians] = useState(null)   // null while loading
  const [loadError, setLoadError] = useState(null)
  const [selectedId, setSelectedId] = useState(null)
  const [form, setForm] = useState(null)
  const [search, setSearch] = useState('')
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState(null)
  const [saveOk, setSaveOk] = useState(false)
  const [addModalOpen, setAddModalOpen] = useState(false)
  const [removeModalOpen, setRemoveModalOpen] = useState(false)
  const [apiBaseResolved, setApiBaseResolved] = useState(false)

  // "View preference sheet" (sked magic link) -- period list is roster-wide,
  // not per-physician, so it's loaded once here rather than as part of `form`.
  const [periods, setPeriods] = useState(null)      // null while loading, [] if sked isn't configured
  const [periodsError, setPeriodsError] = useState(null)
  const [selectedPeriodId, setSelectedPeriodId] = useState('')
  const [linkBusy, setLinkBusy] = useState(false)
  const [linkError, setLinkError] = useState(null)

  const loadPhysicians = useCallback(() => {
    setLoadError(null)
    getPhysiciansFull()
      .then(data => {
        setPhysicians(data.physicians)
        setSelectedId(prev => prev || (data.physicians[0] && data.physicians[0].id) || null)
      })
      .catch(err => setLoadError(err.message))
  }, [])

  // This is a separate BrowserWindow from the main app window (see
  // main/index.js's rosterWindow) — a fresh renderer with its own JS module
  // state, so api.js's BASE_URL here starts back at its hardcoded default
  // (127.0.0.1:5000) regardless of what the main window resolved. Must
  // re-resolve the backend location here too, or this window silently talks
  // to the local backend even when the app is configured for a remote one.
  useEffect(() => {
    let cancelled = false
    window.electronAPI.getBackendConfig()
      .then(({ effectiveUrl }) => { if (!cancelled) setApiBaseUrl(effectiveUrl) })
      .finally(() => { if (!cancelled) setApiBaseResolved(true) })
    return () => { cancelled = true }
  }, [])

  useEffect(() => {
    if (!apiBaseResolved) return
    loadPhysicians()
  }, [apiBaseResolved, loadPhysicians])

  useEffect(() => {
    if (!apiBaseResolved) return
    getSkedPeriods('shift_request')
      .then(data => {
        setPeriods(data.periods)
        setSelectedPeriodId(prev => prev || (data.periods[0] && data.periods[0].id) || '')
      })
      .catch(err => { setPeriods([]); setPeriodsError(err.message) })
  }, [apiBaseResolved])

  const handleViewPreferences = useCallback(() => {
    if (!form || !selectedPeriodId) return
    setLinkBusy(true)
    setLinkError(null)
    getPhysicianLink(form.id, selectedPeriodId)
      .then(({ url }) => window.electronAPI.openExternal(url))
      .catch(err => setLinkError(err.message))
      .finally(() => setLinkBusy(false))
  }, [form, selectedPeriodId])

  const selected = useMemo(
    () => (physicians || []).find(p => p.id === selectedId) || null,
    [physicians, selectedId]
  )

  // Keyed on selectedId (not `selected`) so this only fires when the user
  // navigates to a different physician — not when handleSave's own
  // setPhysicians() update below changes `selected`'s object identity,
  // which would otherwise immediately wipe out the "Saved." confirmation.
  useEffect(() => {
    const p = (physicians || []).find(x => x.id === selectedId)
    if (p) {
      setForm(JSON.parse(JSON.stringify(p)))
      setSaveError(null)
      setSaveOk(false)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedId])

  const dirty = selected && form && JSON.stringify(selected) !== JSON.stringify(form)

  // Guards against silently discarding an unsaved edit, covering both the
  // in-header "✕ Close" button (handleCloseClick, below) and the native
  // OS titlebar close button, which only the beforeunload listener can
  // intercept. Either path ends the same way: confirm via a plain
  // window.confirm(), then ask the main process to destroy this window
  // directly (see forceCloseSelf in main/index.js) rather than calling
  // window.close() again ourselves — a second window.close() issued from
  // deep inside a confirm() callback is unreliable in Electron (it's far
  // enough removed from the original click's user activation that
  // Chromium can silently ignore it).
  const dirtyRef = useRef(dirty)
  dirtyRef.current = dirty

  useEffect(() => {
    function handleBeforeUnload(e) {
      if (dirtyRef.current) {
        e.preventDefault()
        e.returnValue = false
        setTimeout(() => {
          if (window.confirm('Discard unsaved changes and close the Physician Roster window?')) {
            window.electronAPI.forceCloseSelf()
          }
        }, 0)
      }
    }
    window.addEventListener('beforeunload', handleBeforeUnload)
    return () => window.removeEventListener('beforeunload', handleBeforeUnload)
  }, [])

  function handleCloseClick() {
    if (dirtyRef.current) {
      if (window.confirm('Discard unsaved changes and close the Physician Roster window?')) {
        window.electronAPI.forceCloseSelf()
      }
    } else {
      window.electronAPI.forceCloseSelf()
    }
  }

  function selectPhysician(id) {
    if (dirty && !window.confirm('Discard unsaved changes to this physician?')) return
    setSelectedId(id)
  }

  function setField(key, value) {
    setForm(prev => ({ ...prev, [key]: value }))
  }

  function toggleListValue(key, value) {
    setForm(prev => {
      const list = prev[key] || []
      const next = list.includes(value) ? list.filter(v => v !== value) : [...list, value]
      return { ...prev, [key]: next }
    })
  }

  async function handleSave() {
    setSaving(true)
    setSaveError(null)
    setSaveOk(false)
    try {
      const saved = await updatePhysician(form.id, form)
      setPhysicians(prev => prev.map(p => (p.id === saved.id ? saved : p)))
      setForm(saved)
      setSaveOk(true)
    } catch (err) {
      setSaveError(err.message)
    } finally {
      setSaving(false)
    }
  }

  async function handleCreatePhysician({ id, first_name, last_name }) {
    const created = await createPhysician({ id, first_name, last_name })
    setPhysicians(prev => [...prev, created].sort(
      (a, b) => (a.last_name || a.name).localeCompare(b.last_name || b.name)
    ))
    setAddModalOpen(false)
    setSelectedId(created.id)
  }

  async function handleRemovePhysician(confirmation) {
    const result = await removePhysician(form.id, confirmation)
    if (result.ok) {
      const removedId = form.id
      setPhysicians(prev => prev.filter(p => p.id !== removedId))
      setSelectedId(null)
      setForm(null)
      setRemoveModalOpen(false)
    }
    return result
  }

  const filtered = useMemo(() => {
    if (!physicians) return []
    const q = search.trim().toLowerCase()
    const base = q
      ? physicians.filter(p => displayName(p).toLowerCase().includes(q) || p.id.toLowerCase().includes(q))
      : physicians
    // Sorted by last name regardless of display-name fixes above -- the
    // list should read alphabetically by surname even though the label
    // itself now leads with the first name.
    return [...base].sort((a, b) =>
      (a.last_name || displayName(a)).localeCompare(b.last_name || displayName(b))
    )
  }, [physicians, search])

  if (loadError) {
    return (
      <div className="h-screen flex items-center justify-center bg-slate-50">
        <div className="text-center max-w-md">
          <p className="text-red-600 font-medium mb-2">Couldn't load the physician roster</p>
          <p className="text-sm text-slate-500 mb-4">{loadError}</p>
          <button
            onClick={loadPhysicians}
            className="px-4 py-2 text-sm font-medium text-white bg-sky-600 rounded-md hover:bg-sky-700"
          >
            Retry
          </button>
        </div>
      </div>
    )
  }

  if (!physicians) {
    return <div className="h-screen flex items-center justify-center bg-slate-50 text-slate-400 text-sm">Loading roster…</div>
  }

  return (
    <div className="h-screen flex flex-col bg-slate-50">
      <header className="flex items-center justify-between px-5 py-3 shadow-md flex-shrink-0" style={{ backgroundColor: '#1e293b' }}>
        <div>
          <h1 className="text-white font-bold text-base leading-tight">Physician Roster</h1>
          <p className="text-slate-400 text-xs">physicians.yaml — {physicians.length} physicians</p>
        </div>
        <button
          onClick={handleCloseClick}
          title="Close"
          className="w-7 h-7 flex items-center justify-center rounded-full bg-red-600 hover:bg-red-500 text-white text-sm font-bold transition-colors"
        >
          ✕
        </button>
      </header>

      <div className="flex flex-1 overflow-hidden">
        {/* List pane */}
        <div className="w-72 flex-shrink-0 border-r border-slate-200 bg-white flex flex-col">
          <div className="p-3 border-b border-slate-200 space-y-2">
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search…"
              className="w-full px-3 py-1.5 text-sm border border-slate-300 rounded-md focus:outline-none focus:ring-2 focus:ring-sky-500"
            />
            <button
              onClick={() => setAddModalOpen(true)}
              className="w-full px-3 py-1.5 text-sm font-medium text-sky-700 bg-sky-50 border border-sky-200 rounded-md hover:bg-sky-100 transition-colors"
            >
              + Add Physician
            </button>
          </div>
          <div className="flex-1 overflow-auto">
            {filtered.map(p => (
              <button
                key={p.id}
                onClick={() => selectPhysician(p.id)}
                className={`w-full text-left px-3 py-2 text-sm border-b border-slate-100 transition-colors ${
                  p.id === selectedId ? 'bg-sky-50 text-sky-800 font-medium' : 'text-slate-700 hover:bg-slate-50'
                } ${!p.active ? 'opacity-50' : ''}`}
              >
                {displayName(p)}
                <span className="block text-xs text-slate-400 font-mono">{p.id}</span>
              </button>
            ))}
            {filtered.length === 0 && (
              <p className="text-sm text-slate-400 px-3 py-4">No physicians match &quot;{search}&quot;.</p>
            )}
          </div>
        </div>

        {/* Detail pane */}
        <div className="flex-1 overflow-auto">
          {form ? (
            <div className="max-w-2xl mx-auto p-6 space-y-6">
              <div className="flex items-start justify-between">
                <div>
                  <h2 className="text-lg font-semibold text-slate-800">{displayName(form)}</h2>
                  <p className="text-xs text-slate-400 font-mono">{form.id}</p>
                </div>
                <label className="flex items-center gap-2 text-sm text-slate-600">
                  <input type="checkbox" checked={form.active} onChange={(e) => setField('active', e.target.checked)} />
                  Active
                </label>
              </div>

              <Section title="Identity">
                <TextField label="Name" value={form.name} onChange={(v) => setField('name', v)} />
                <div className="grid grid-cols-2 gap-3">
                  <TextField label="First name" value={form.first_name} onChange={(v) => setField('first_name', v)} />
                  <TextField label="Last name" value={form.last_name} onChange={(v) => setField('last_name', v)} />
                </div>
                <TagListEditor
                  label="Aliases"
                  hint="Other names this physician appears under in imported submission files."
                  values={form.aliases}
                  onChange={(v) => setField('aliases', v)}
                />
              </Section>

              <Section title="Status">
                <CheckboxField
                  label="Special Provisions (excluded from request validation)"
                  checked={form.special_provisions}
                  onChange={(v) => setField('special_provisions', v)}
                />
                <p className="text-xs text-slate-400 -mt-1.5 pl-6">
                  A standing scheduling limitation serious enough that this physician's monthly
                  submission errors are expected — they're auto-overridden at import instead of
                  needing a manual click every month. Still viewable in the Validate page's expandable
                  detail, just already marked resolved.
                </p>
                <CheckboxField
                  label="Casual"
                  checked={form.casual}
                  onChange={(v) => setField('casual', v)}
                />
                <p className="text-xs text-slate-400 -mt-1.5 pl-6">
                  Also auto-overridden like Special Provisions, and deprioritized by the scheduler —
                  only assigned shifts once every non-casual physician has their own requested count,
                  and only into slots still open after that.
                </p>
              </Section>

              <Section title="Scheduling rules">
                <div className="grid grid-cols-2 gap-3">
                  <IntegerField
                    label="Max consecutive shifts"
                    value={form.max_consecutive_shifts}
                    onChange={(v) => setField('max_consecutive_shifts', v)}
                  />
                  <IntegerField
                    label="Max consecutive nights (2400h)"
                    value={form.max_consecutive_nights}
                    onChange={(v) => setField('max_consecutive_nights', v)}
                  />
                </div>
                <div className="grid grid-cols-2 gap-3">
                  <IntegerField
                    label="Max consecutive 1800h"
                    value={form.max_consecutive_1800h}
                    onChange={(v) => setField('max_consecutive_1800h', v)}
                  />
                  <IntegerField
                    label="Max weekends (blank = no cap)"
                    value={form.max_weekends}
                    onChange={(v) => setField('max_weekends', v)}
                    nullable
                  />
                </div>
                <SelectField
                  label="Non-acute site preference"
                  value={form.group_b_site_preference || ''}
                  options={GROUP_B_PREFS}
                  onChange={(v) => setField('group_b_site_preference', v || null)}
                />
                <CheckboxGroup
                  label="Forbidden sites"
                  options={VALID_SITES}
                  values={form.forbidden_sites}
                  onToggle={(v) => toggleListValue('forbidden_sites', v)}
                />
                <CheckboxGroup
                  label="Forbidden shift times"
                  options={SHIFT_TIMES}
                  values={form.forbidden_shift_times}
                  onToggle={(v) => toggleListValue('forbidden_shift_times', v)}
                />
              </Section>

              <Section title="Annual survey baseline">
                <p className="text-xs text-slate-400 -mt-1.5">
                  What this physician says to typically expect, from the annual preferences
                  survey — reference only, not fed into the solver or any monthly submission.
                </p>
                <div className="grid grid-cols-3 gap-3">
                  <IntegerField
                    label="Typical shifts / month"
                    value={form.typical_shifts_per_month}
                    onChange={(v) => setField('typical_shifts_per_month', v)}
                    nullable
                  />
                  <IntegerField
                    label="Typical 0600h / month"
                    value={form.typical_0600h_per_month}
                    onChange={(v) => setField('typical_0600h_per_month', v)}
                    nullable
                  />
                  <IntegerField
                    label="Typical 2400h / month"
                    value={form.typical_2400h_per_month}
                    onChange={(v) => setField('typical_2400h_per_month', v)}
                    nullable
                  />
                </div>
              </Section>

              <Section title="Notes &amp; contact">
                <TextField
                  label="Email"
                  value={form.email}
                  onChange={(v) => setField('email', v)}
                  hint="Used to email this physician about shift-request deficiencies."
                />
                <TextAreaField
                  label="Notes"
                  value={form.notes}
                  onChange={(v) => setField('notes', v)}
                  hint="Free text — standing restrictions, context for whoever reviews this physician's requests, etc. Not used by the scheduler."
                />
              </Section>

              <Section title="Preferences">
                <CheckboxField
                  label="Only 2400h shifts"
                  checked={form.only_2400h}
                  onChange={(v) => setField('only_2400h', v)}
                  hint="Hard restriction — the solver will never assign this physician any shift except 2400h."
                />
                <CheckboxField
                  label="Only 0600h shifts"
                  checked={form.only_0600h}
                  onChange={(v) => setField('only_0600h', v)}
                  hint="Hard restriction — the solver will never assign this physician any shift except 0600h."
                />
                <CheckboxField
                  label="Prefer weekends"
                  checked={form.prefer_weekends}
                  onChange={(v) => setField('prefer_weekends', v)}
                  hint="Soft bonus — favors weekend (Fri/Sat/Sun) shifts over weekday ones, waives any weekend cap, and avoids a fragmented Fri+Sun-without-Saturday pattern."
                />
                <CheckboxField
                  label="Honor all requests"
                  checked={form.honor_all_requests}
                  onChange={(v) => setField('honor_all_requests', v)}
                  hint="Soft, high priority — the solver strongly prefers the exact days/shifts they marked on their submission, not just hitting their requested total."
                />
                <CheckboxField
                  label="Prefer singleton nights"
                  checked={form.prefer_singleton_nights}
                  onChange={(v) => setField('prefer_singleton_nights', v)}
                  hint="Relaxes a hard rule — normally every 2400h night must be part of a 2+ day run; this lets a single isolated night stand alone for them."
                />
                <CheckboxField
                  label="No call (DOC/NOC)"
                  checked={form.no_call}
                  onChange={(v) => setField('no_call', v)}
                  hint="Hard exclusion — the solver never assigns them any on-call (DOC/NOC) shift."
                />
                <CheckboxField
                  label="Avoid Mondays"
                  checked={form.avoid_mondays}
                  onChange={(v) => setField('avoid_mondays', v)}
                  hint="Soft penalty — the solver avoids giving them Monday shifts when possible (e.g. a protected admin day)."
                />
                <CheckboxField
                  label="Rest after late shift"
                  checked={form.rest_after_late_shift}
                  onChange={(v) => setField('rest_after_late_shift', v)}
                  hint="Hard rule — guarantees a day off immediately after any 1600h/1800h/2000h shift, and blocks a late shift the day before an already-scheduled one."
                />
                <CheckboxField
                  label="Cap at requested shifts"
                  checked={form.cap_at_requested}
                  onChange={(v) => setField('cap_at_requested', v)}
                  hint="Hard cap — the solver will never assign them more than their requested shift count, even to fill otherwise-open slots."
                />
              </Section>

              <Section title="Preference sheet">
                {periods === null ? (
                  <p className="text-sm text-slate-400">Loading periods…</p>
                ) : periods.length === 0 ? (
                  <p className="text-sm text-slate-500">
                    {periodsError
                      ? `Couldn't load periods: ${periodsError}`
                      : 'No shift-request periods on sked yet — use "Send Monthly Shift Requests" first.'}
                  </p>
                ) : (
                  <div className="flex items-end gap-2">
                    <div className="flex-1">
                      <SelectField
                        label="Period"
                        value={selectedPeriodId}
                        onChange={setSelectedPeriodId}
                        options={periods.map(p => ({ value: p.id, label: p.label }))}
                      />
                    </div>
                    <button
                      onClick={handleViewPreferences}
                      disabled={linkBusy || !selectedPeriodId}
                      className="px-3 py-1.5 text-sm font-medium text-white bg-sky-600 rounded-md hover:bg-sky-700 disabled:opacity-40 disabled:cursor-not-allowed transition-colors whitespace-nowrap"
                    >
                      {linkBusy ? 'Opening…' : 'View in browser'}
                    </button>
                  </div>
                )}
                {linkError && <p className="text-xs text-red-600 mt-1">{linkError}</p>}
              </Section>

              <Section title="Validation rule overrides">
                <RuleOverridesEditor
                  value={form.rule_overrides}
                  onChange={(v) => setField('rule_overrides', v)}
                />
              </Section>

              <div className="sticky bottom-0 bg-slate-50 border-t border-slate-200 pt-4 pb-2 flex items-center gap-3">
                <button
                  onClick={handleSave}
                  disabled={saving || !dirty}
                  className="px-4 py-2 text-sm font-medium text-white bg-sky-600 rounded-md hover:bg-sky-700 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
                >
                  {saving ? 'Saving…' : 'Save'}
                </button>
                {dirty && !saving && <span className="text-xs text-amber-600">Unsaved changes</span>}
                {saveOk && <span className="text-xs text-emerald-600">Saved.</span>}
                {saveError && <span className="text-xs text-red-600">{saveError}</span>}
                <button
                  onClick={() => setRemoveModalOpen(true)}
                  className="ml-auto px-3 py-1.5 text-xs font-medium text-red-600 border border-red-200 rounded-md hover:bg-red-50 transition-colors"
                >
                  Remove Physician…
                </button>
              </div>
            </div>
          ) : (
            <div className="h-full flex items-center justify-center text-slate-400 text-sm">
              Select a physician to view their rules.
            </div>
          )}
        </div>
      </div>

      {addModalOpen && (
        <AddPhysicianModal
          existingIds={physicians.map(p => p.id)}
          onCreate={handleCreatePhysician}
          onClose={() => setAddModalOpen(false)}
        />
      )}

      {removeModalOpen && form && (
        <RemovePhysicianModal
          physicianName={displayName(form)}
          onRemove={handleRemovePhysician}
          onClose={() => setRemoveModalOpen(false)}
        />
      )}
    </div>
  )
}

function AddPhysicianModal({ existingIds, onCreate, onClose }) {
  const [firstName, setFirstName] = useState('')
  const [lastName, setLastName] = useState('')
  const [id, setId] = useState('')
  const [idTouched, setIdTouched] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState(null)

  function handleLastNameChange(v) {
    setLastName(v)
    if (!idTouched) setId(v.replace(/[^A-Za-z0-9]/g, ''))
  }

  const trimmedId = id.trim()
  const idValid = /^[A-Za-z0-9]+$/.test(trimmedId)
  const idTaken = existingIds.includes(trimmedId)
  const canSubmit = firstName.trim() && lastName.trim() && idValid && !idTaken && !submitting

  async function handleSubmit() {
    setSubmitting(true)
    setError(null)
    try {
      await onCreate({ id: trimmedId, first_name: firstName.trim(), last_name: lastName.trim() })
    } catch (err) {
      setError(err.message)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40" onClick={onClose}>
      <div
        className="bg-white rounded-xl shadow-xl border border-slate-200 w-full max-w-md mx-4"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-5 py-4 border-b border-slate-200">
          <h2 className="font-semibold text-slate-800 text-sm">Add Physician</h2>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-600 text-lg leading-none">✕</button>
        </div>

        <div className="px-5 py-4 space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <TextField label="First name" value={firstName} onChange={setFirstName} />
            <TextField label="Last name" value={lastName} onChange={handleLastNameChange} />
          </div>
          <label className="block">
            <span className="block text-xs text-slate-500 mb-1">
              Id — a single word used internally to match this physician's submissions
            </span>
            <input
              type="text"
              value={id}
              onChange={(e) => { setIdTouched(true); setId(e.target.value.replace(/\s/g, '')) }}
              className="w-full px-3 py-1.5 text-sm font-mono border border-slate-300 rounded-md focus:outline-none focus:ring-2 focus:ring-sky-500"
            />
          </label>
          {trimmedId !== '' && !idValid && (
            <p className="text-xs text-red-600">Letters and numbers only, no spaces or punctuation.</p>
          )}
          {idValid && idTaken && <p className="text-xs text-red-600">That id is already in use.</p>}
          <p className="text-xs text-slate-400">
            Everything else (scheduling rules, preferences) starts at plain defaults — active, 3 max
            consecutive shifts, no site preference or overrides. Edit further after adding.
          </p>
          {error && <p className="text-sm text-red-600">{error}</p>}
        </div>

        <div className="flex justify-end gap-3 px-5 py-3 border-t border-slate-200">
          <button
            onClick={onClose}
            className="px-4 py-2 text-sm font-medium text-slate-700 bg-white border border-slate-300 rounded-md hover:bg-slate-50 transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={handleSubmit}
            disabled={!canSubmit}
            className="px-4 py-2 text-sm font-medium text-white bg-sky-600 rounded-md hover:bg-sky-700 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          >
            {submitting ? 'Adding…' : 'Add'}
          </button>
        </div>
      </div>
    </div>
  )
}

function RemovePhysicianModal({ physicianName, onRemove, onClose }) {
  const [confirmText, setConfirmText] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState(null)
  const canRemove = confirmText === 'REMOVE' && !submitting

  async function handleSubmit() {
    setSubmitting(true)
    setError(null)
    try {
      const result = await onRemove(confirmText)
      if (!result.ok) setError(result.status || 'Could not remove physician.')
    } catch (err) {
      setError(err.message)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40" onClick={onClose}>
      <div
        className="bg-white rounded-xl shadow-xl border border-slate-200 w-full max-w-md mx-4"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-5 py-4 border-b border-red-200 bg-red-50">
          <h2 className="font-semibold text-red-800 text-sm">Remove {physicianName}?</h2>
          <button onClick={onClose} className="text-red-400 hover:text-red-600 text-lg leading-none">✕</button>
        </div>

        <div className="px-5 py-4 space-y-3">
          <p className="text-sm text-slate-700">
            This permanently deletes <span className="font-semibold">{physicianName}</span> from
            physicians.yaml. This cannot be undone from here. Type{' '}
            <span className="font-mono font-semibold">REMOVE</span> below to confirm.
          </p>
          <input
            type="text"
            value={confirmText}
            onChange={(e) => setConfirmText(e.target.value)}
            placeholder="Type REMOVE"
            className="w-full px-3 py-2 text-sm border border-slate-300 rounded-md focus:outline-none focus:ring-2 focus:ring-red-500"
          />
          {error && <p className="text-sm text-red-600">{error}</p>}
        </div>

        <div className="flex justify-end gap-3 px-5 py-3 border-t border-slate-200">
          <button
            onClick={onClose}
            className="px-4 py-2 text-sm font-medium text-slate-700 bg-white border border-slate-300 rounded-md hover:bg-slate-50 transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={handleSubmit}
            disabled={!canRemove}
            className="px-4 py-2 text-sm font-medium text-white bg-red-600 rounded-md hover:bg-red-700 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          >
            {submitting ? 'Removing…' : 'Remove'}
          </button>
        </div>
      </div>
    </div>
  )
}

function Section({ title, children }) {
  return (
    <div className="bg-white rounded-xl border border-slate-200 p-4 space-y-3">
      <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">{title}</h3>
      {children}
    </div>
  )
}

function TextField({ label, value, onChange, hint }) {
  return (
    <label className="block">
      <span className="block text-xs text-slate-500 mb-1">{label}</span>
      {hint && <p className="text-xs text-slate-400 mb-1">{hint}</p>}
      <input
        type="text"
        value={value || ''}
        onChange={(e) => onChange(e.target.value)}
        className="w-full px-3 py-1.5 text-sm border border-slate-300 rounded-md focus:outline-none focus:ring-2 focus:ring-sky-500"
      />
    </label>
  )
}

function TextAreaField({ label, value, onChange, hint }) {
  return (
    <label className="block">
      <span className="block text-xs text-slate-500 mb-1">{label}</span>
      {hint && <p className="text-xs text-slate-400 mb-1">{hint}</p>}
      <textarea
        value={value || ''}
        onChange={(e) => onChange(e.target.value)}
        rows={4}
        className="w-full px-3 py-1.5 text-sm border border-slate-300 rounded-md focus:outline-none focus:ring-2 focus:ring-sky-500 resize-y"
      />
    </label>
  )
}

// Strict single-integer input: only digits, never a range, decimal, or
// arbitrary text. Rendered as text (not type="number") specifically to
// avoid the native number input's own loopholes — scientific notation
// ("1e5"), a leading "+"/"-", scroll-wheel increment — by never letting
// non-digit characters into the field at all rather than trying to
// validate them after the fact.
function IntegerField({ label, value, onChange, nullable = false, hint }) {
  const display = value === null || value === undefined ? '' : String(value)

  function handleChange(e) {
    const digitsOnly = e.target.value.replace(/[^\d]/g, '')
    if (digitsOnly === '') { onChange(nullable ? null : 0); return }
    onChange(parseInt(digitsOnly, 10))
  }

  function blockNonDigitKeys(e) {
    // Backspace/Delete/Tab/arrows/etc. have no e.key of a printable
    // character length 1, so this only ever blocks actual character keys.
    if (e.key.length === 1 && !/\d/.test(e.key)) e.preventDefault()
  }

  return (
    <label className="block">
      <span className="block text-xs text-slate-500 mb-1">{label}</span>
      {hint && <p className="text-xs text-slate-400 mb-1">{hint}</p>}
      <input
        type="text"
        inputMode="numeric"
        pattern="[0-9]*"
        value={display}
        onKeyDown={blockNonDigitKeys}
        onChange={handleChange}
        onPaste={(e) => {
          e.preventDefault()
          const digitsOnly = (e.clipboardData.getData('text') || '').replace(/[^\d]/g, '')
          if (digitsOnly) onChange(parseInt(digitsOnly, 10))
        }}
        className="w-full px-3 py-1.5 text-sm border border-slate-300 rounded-md focus:outline-none focus:ring-2 focus:ring-sky-500"
      />
    </label>
  )
}

function SelectField({ label, value, options, onChange }) {
  return (
    <label className="block">
      <span className="block text-xs text-slate-500 mb-1">{label}</span>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="w-full px-3 py-1.5 text-sm border border-slate-300 rounded-md focus:outline-none focus:ring-2 focus:ring-sky-500 bg-white"
      >
        {options.map(opt => <option key={opt.value} value={opt.value}>{opt.label}</option>)}
      </select>
    </label>
  )
}

function CheckboxField({ label, checked, onChange, hint }) {
  return (
    <label className="flex items-center gap-2 text-sm text-slate-700">
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      <span>
        {label}
        {hint && <em className="ml-1 text-xs text-slate-400">{hint}</em>}
      </span>
    </label>
  )
}

function CheckboxGroup({ label, options, values, onToggle }) {
  return (
    <div>
      <span className="block text-xs text-slate-500 mb-1">{label}</span>
      <div className="flex flex-wrap gap-x-4 gap-y-1">
        {options.map(opt => (
          <label key={opt} className="flex items-center gap-1.5 text-sm text-slate-700">
            <input type="checkbox" checked={values.includes(opt)} onChange={() => onToggle(opt)} />
            {opt}
          </label>
        ))}
      </div>
    </div>
  )
}

function TagListEditor({ label, hint, values, onChange }) {
  const [draft, setDraft] = useState('')

  function add() {
    const v = draft.trim()
    if (v && !values.includes(v)) onChange([...values, v])
    setDraft('')
  }

  return (
    <div>
      <span className="block text-xs text-slate-500 mb-1">{label}</span>
      {hint && <p className="text-xs text-slate-400 mb-1">{hint}</p>}
      <div className="flex flex-wrap gap-1.5 mb-1.5">
        {values.map(v => (
          <span key={v} className="inline-flex items-center gap-1 px-2 py-0.5 text-xs bg-slate-100 border border-slate-200 rounded-full text-slate-700">
            {v}
            <button onClick={() => onChange(values.filter(x => x !== v))} className="text-slate-400 hover:text-slate-700">✕</button>
          </span>
        ))}
      </div>
      <div className="flex gap-2">
        <input
          type="text"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); add() } }}
          placeholder="Add alias…"
          className="flex-1 px-3 py-1.5 text-sm border border-slate-300 rounded-md focus:outline-none focus:ring-2 focus:ring-sky-500"
        />
        <button onClick={add} className="px-3 py-1.5 text-sm border border-slate-300 rounded-md text-slate-600 hover:bg-slate-50">Add</button>
      </div>
    </div>
  )
}

// Compact digit-only integer input for one rule override's value — same
// no-ranges/no-text/no-decimals guarantee as IntegerField, just sized for
// inline use next to a rule's label rather than a full labeled field.
function RuleOverrideValueInput({ value, onChange }) {
  return (
    <input
      type="text"
      inputMode="numeric"
      pattern="[0-9]*"
      value={value === null || value === undefined ? '' : String(value)}
      onKeyDown={(e) => { if (e.key.length === 1 && !/\d/.test(e.key)) e.preventDefault() }}
      onChange={(e) => {
        const digitsOnly = e.target.value.replace(/[^\d]/g, '')
        onChange(digitsOnly === '' ? 0 : parseInt(digitsOnly, 10))
      }}
      onPaste={(e) => {
        e.preventDefault()
        const digitsOnly = (e.clipboardData.getData('text') || '').replace(/[^\d]/g, '')
        if (digitsOnly) onChange(parseInt(digitsOnly, 10))
      }}
      className="w-16 px-2 py-1 text-sm border border-slate-300 rounded-md"
    />
  )
}

function RuleOverridesEditor({ value, onChange }) {
  const activeKeys = Object.keys(value)
  const unusedKeys = RULE_OVERRIDE_KEYS.filter(k => !activeKeys.includes(k))

  function setOverride(key, val) {
    onChange({ ...value, [key]: val })
  }
  function removeOverride(key) {
    const next = { ...value }
    delete next[key]
    onChange(next)
  }

  return (
    <div className="space-y-2">
      {activeKeys.length === 0 && <p className="text-sm text-slate-400">Standard rules — no overrides.</p>}
      {activeKeys.map(key => {
        const disabled = value[key] === null
        return (
          <div key={key} className="flex items-center gap-2">
            <span className="text-sm text-slate-700 flex-1">{RULE_OVERRIDE_LABELS[key] || key}</span>
            <label className="flex items-center gap-1.5 text-xs text-slate-500">
              <input
                type="checkbox"
                checked={disabled}
                onChange={(e) => setOverride(key, e.target.checked ? null : 1)}
              />
              disabled
            </label>
            {!disabled && (
              <RuleOverrideValueInput value={value[key]} onChange={(v) => setOverride(key, v)} />
            )}
            <button onClick={() => removeOverride(key)} className="text-slate-400 hover:text-red-600 text-sm">✕</button>
          </div>
        )
      })}
      {unusedKeys.length > 0 && (
        <select
          value=""
          onChange={(e) => { if (e.target.value) setOverride(e.target.value, 1) }}
          className="mt-1 px-3 py-1.5 text-sm border border-slate-300 rounded-md bg-white text-slate-600"
        >
          <option value="">+ Add rule override…</option>
          {unusedKeys.map(k => <option key={k} value={k}>{RULE_OVERRIDE_LABELS[k]}</option>)}
        </select>
      )}
    </div>
  )
}
