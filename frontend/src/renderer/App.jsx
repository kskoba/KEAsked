import React, { useState, useCallback, useEffect } from 'react'
import Header from './components/Header'
import DirectoryPicker from './components/DirectoryPicker'
import ValidationPanel from './components/ValidationPanel'
import ScheduleGrid from './components/ScheduleGrid'
import Sidebar from './components/Sidebar'
import ConflictModal from './components/ConflictModal'
import ReplaceModal from './components/ReplaceModal'
import { assignPhysician, getSchedule, checkViolations } from './api'

export default function App() {
  // 'setup' | 'schedule'
  const [view, setView] = useState('setup')

  // Import state
  const [importResult, setImportResult] = useState(null)   // ImportDirectoryResponse
  const [scheduleData, setScheduleData] = useState(null)   // ScheduleResponse

  // Conflict modal state (unfilled slots)
  const [conflictSlot, setConflictSlot] = useState(null)   // UnfilledSlotSchema | null

  // Replace modal state (filled slots)
  const [replaceSlot, setReplaceSlot] = useState(null)     // AssignmentSchema | null

  // True when schedule was loaded from a file (not freshly generated)
  const [scheduleLoadedFromFile, setScheduleLoadedFromFile] = useState(false)

  // Swap mode state
  const [swapMode, setSwapMode] = useState(false)
  const [swapFirst, setSwapFirst] = useState(null)         // AssignmentSchema | null

  // Per-physician violation tracking: { physicianName: [{rule, description, date, shiftCode}] }
  const [physicianViolations, setPhysicianViolations] = useState({})

  // Swap violation confirmation: null | { items: [{physician, description}], onConfirm: fn }
  const [swapViolationConfirm, setSwapViolationConfirm] = useState(null)

  // When schedule refreshes, prune violations for assignments that no longer exist
  useEffect(() => {
    if (!scheduleData) return
    setPhysicianViolations(prev => {
      const result = {}
      let changed = false
      for (const [name, violations] of Object.entries(prev)) {
        const active = violations.filter(v =>
          scheduleData.assignments.some(a =>
            a.physician_name === name && a.date === v.date && a.shift?.code === v.shiftCode
          )
        )
        if (active.length > 0) result[name] = active
        if (active.length !== violations.length) changed = true
      }
      return changed ? result : prev
    })
  }, [scheduleData])

  const recordViolations = useCallback((updatedSchedule, violationInfo) => {
    setScheduleData(updatedSchedule)
    if (violationInfo?.violations?.length > 0 && violationInfo.physicianName) {
      const { physicianName, violations, date, shiftCode } = violationInfo
      setPhysicianViolations(prev => {
        const existing = prev[physicianName] || []
        // Replace any prior violation record for this exact slot
        const filtered = existing.filter(v => !(v.date === date && v.shiftCode === shiftCode))
        return {
          ...prev,
          [physicianName]: [
            ...filtered,
            ...violations.map(v => ({ ...v, date, shiftCode }))
          ]
        }
      })
    }
  }, [])

  const handleImportDone = useCallback((result) => {
    setImportResult(result)
  }, [])

  const handleScheduleGenerated = useCallback((schedule) => {
    setScheduleData(schedule)
    setPhysicianViolations({})
    setScheduleLoadedFromFile(false)
    setView('schedule')
  }, [])

  const handleScheduleLoaded = useCallback((schedule) => {
    setScheduleData(schedule)
    setPhysicianViolations({})
    setScheduleLoadedFromFile(true)
    setView('schedule')
  }, [])

  const handleBackToSetup = useCallback(() => {
    setView('setup')
  }, [])

  const handleViewSchedule = useCallback(() => {
    setView('schedule')
  }, [])

  const handleOpenConflict = useCallback((slot) => {
    setConflictSlot(slot)
  }, [])

  const handleConflictAssigned = useCallback((updatedSchedule, violationInfo) => {
    recordViolations(updatedSchedule, violationInfo)
    setConflictSlot(null)
  }, [recordViolations])

  const handleConflictClose = useCallback(() => {
    setConflictSlot(null)
  }, [])

  const handleOpenReplace = useCallback((assignment) => {
    setReplaceSlot(assignment)
  }, [])

  const handleReplaceAssigned = useCallback((updatedSchedule, violationInfo) => {
    recordViolations(updatedSchedule, violationInfo)
    setReplaceSlot(null)
  }, [recordViolations])

  const handleReplaceClose = useCallback(() => {
    setReplaceSlot(null)
  }, [])

  // Swap mode handlers
  const handleToggleSwap = useCallback(() => {
    setSwapMode(prev => !prev)
    setSwapFirst(null)
  }, [])

  const handleCancelSwap = useCallback(() => {
    setSwapMode(false)
    setSwapFirst(null)
  }, [])

  const doSwap = useCallback(async (a, b) => {
    try {
      await assignPhysician(a.date, a.shift.code, b.physician_id)
      await assignPhysician(b.date, b.shift.code, a.physician_id)
      const updated = await getSchedule()
      setScheduleData(updated)
    } catch (err) {
      console.error('Swap failed:', err)
      alert(`Swap failed: ${err.message}`)
    }
  }, [])

  const handleSwapClick = useCallback(async (assignment) => {
    if (!swapMode) return
    if (!swapFirst) {
      setSwapFirst(assignment)
      return
    }
    const a = swapFirst
    const b = assignment
    setSwapMode(false)
    setSwapFirst(null)

    // Pre-check violations for both directions
    try {
      const [resAtoB, resBtoA] = await Promise.all([
        checkViolations(b.date, b.shift.code, a.physician_id),  // A goes to B's slot
        checkViolations(a.date, a.shift.code, b.physician_id),  // B goes to A's slot
      ])

      const hardItems = []
      for (const v of resAtoB.violations ?? []) {
        if (v.is_hard && v.rule !== 'max_shifts') hardItems.push({ physician: a.physician_name || a.physician_id, description: v.description })
      }
      for (const v of resBtoA.violations ?? []) {
        if (v.is_hard && v.rule !== 'max_shifts') hardItems.push({ physician: b.physician_name || b.physician_id, description: v.description })
      }

      if (hardItems.length > 0) {
        setSwapViolationConfirm({ items: hardItems, onConfirm: () => doSwap(a, b) })
        return
      }
    } catch {
      // If violation check fails, proceed with swap anyway
    }

    await doSwap(a, b)
  }, [swapMode, swapFirst, doSwap])

  // Cancel swap mode on Escape key
  useEffect(() => {
    if (!swapMode) return
    const handler = (e) => { if (e.key === 'Escape') handleCancelSwap() }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [swapMode, handleCancelSwap])

  return (
    <div className="flex flex-col h-screen bg-slate-50 overflow-hidden">
      <Header view={view} onBack={handleBackToSetup} hasSchedule={!!scheduleData} onViewSchedule={handleViewSchedule} />

      {view === 'setup' && (
        <div className="flex-1 overflow-auto p-6 space-y-6">
          <DirectoryPicker
            onImportDone={handleImportDone}
            onScheduleGenerated={handleScheduleGenerated}
            onScheduleLoaded={handleScheduleLoaded}
            importResult={importResult}
          />
          {importResult && (
            <ValidationPanel importResult={importResult} />
          )}
        </div>
      )}

      {view === 'schedule' && scheduleData && (
        <div className="flex flex-1 overflow-hidden">
          <div className="flex-1 overflow-auto flex flex-col">
            {/* File-loaded banner */}
            {scheduleLoadedFromFile && (
              <div className="mx-4 mt-3 px-3 py-2 rounded bg-sky-50 border border-sky-200 text-sky-800 text-xs flex-shrink-0">
                Schedule loaded from file — manual assignment and swaps require re-importing preferences first.
              </div>
            )}
            {/* Toolbar */}
            <div className="flex items-center gap-2 px-4 pt-3 pb-1 flex-shrink-0">
              <button
                onClick={handleToggleSwap}
                className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded text-xs font-semibold border transition-colors
                  ${swapMode
                    ? 'bg-amber-400 border-amber-500 text-amber-900 hover:bg-amber-300'
                    : 'bg-white border-slate-300 text-slate-700 hover:bg-slate-100'
                  }`}
                title="Toggle shift swap mode"
              >
                ⇄ Shift Swap
              </button>
            </div>
            {/* Swap mode banner */}
            {swapMode && (
              <div className="mx-4 mb-1 px-3 py-2 rounded bg-amber-100 border border-amber-400 text-amber-900 text-xs font-medium flex-shrink-0">
                {swapFirst
                  ? `Now click the second physician to swap with "${swapFirst.physician_name || swapFirst.physician_id}" (or press Esc to cancel)`
                  : 'Select the first physician to swap…'
                }
              </div>
            )}
            <ScheduleGrid
              scheduleData={scheduleData}
              onOpenConflict={handleOpenConflict}
              onReplaceAssignment={handleOpenReplace}
              swapMode={swapMode}
              swapFirst={swapFirst}
              onSwapClick={handleSwapClick}
            />
          </div>
          <Sidebar
            scheduleData={scheduleData}
            importResult={importResult}
            physicianViolations={physicianViolations}
          />
        </div>
      )}

      {swapViolationConfirm && (
        <SwapViolationModal
          items={swapViolationConfirm.items}
          onConfirm={() => {
            const { onConfirm } = swapViolationConfirm
            setSwapViolationConfirm(null)
            onConfirm()
          }}
          onCancel={() => setSwapViolationConfirm(null)}
        />
      )}

      {conflictSlot && (
        <ConflictModal
          slot={conflictSlot}
          scheduleData={scheduleData}
          onAssigned={handleConflictAssigned}
          onClose={handleConflictClose}
        />
      )}

      {replaceSlot && (
        <ReplaceModal
          slot={replaceSlot}
          scheduleData={scheduleData}
          importResult={importResult}
          onAssigned={handleReplaceAssigned}
          onClose={handleReplaceClose}
        />
      )}
    </div>
  )
}

function SwapViolationModal({ items, onConfirm, onCancel }) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div className="bg-white rounded-xl shadow-xl border border-slate-200 w-full max-w-md mx-4 overflow-hidden">
        <div className="flex items-center gap-3 px-5 py-4 bg-amber-50 border-b border-amber-200">
          <svg className="w-5 h-5 text-amber-600 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v4m0 4h.01M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z" />
          </svg>
          <h2 className="font-semibold text-amber-900 text-sm">Swap violates scheduling rules</h2>
        </div>

        <div className="px-5 py-4">
          <p className="text-sm text-slate-600 mb-3">This swap would break the following hard rules:</p>
          <ul className="space-y-2 mb-4 max-h-48 overflow-auto">
            {items.map((item, i) => (
              <li key={i} className="flex items-start gap-2 p-2 bg-red-50 border border-red-200 rounded-md">
                <svg className="w-4 h-4 text-red-500 flex-shrink-0 mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}>
                  <circle cx="12" cy="12" r="10" /><line x1="12" y1="8" x2="12" y2="12" /><line x1="12" y1="16" x2="12.01" y2="16" />
                </svg>
                <div className="text-xs">
                  <span className="font-semibold text-red-800">{item.physician}: </span>
                  <span className="text-red-700">{item.description}</span>
                </div>
              </li>
            ))}
          </ul>
          <p className="text-xs text-slate-500 mb-4">Click <strong>OK to Override</strong> to proceed anyway, or <strong>Cancel</strong> to abort the swap.</p>
        </div>

        <div className="flex justify-end gap-3 px-5 pb-4">
          <button
            onClick={onCancel}
            className="px-4 py-2 text-sm font-medium text-slate-700 bg-white border border-slate-300 rounded-md hover:bg-slate-50 transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={onConfirm}
            className="px-4 py-2 text-sm font-medium text-white bg-red-600 hover:bg-red-500 rounded-md transition-colors"
          >
            OK to Override
          </button>
        </div>
      </div>
    </div>
  )
}
