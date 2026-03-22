import React, { useState, useCallback, useEffect } from 'react'
import Header from './components/Header'
import DirectoryPicker from './components/DirectoryPicker'
import ValidationPanel from './components/ValidationPanel'
import ScheduleGrid from './components/ScheduleGrid'
import Sidebar from './components/Sidebar'
import ConflictModal from './components/ConflictModal'
import ReplaceModal from './components/ReplaceModal'
import { assignPhysician, getSchedule } from './api'

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

  // Swap mode state
  const [swapMode, setSwapMode] = useState(false)
  const [swapFirst, setSwapFirst] = useState(null)         // AssignmentSchema | null

  // Per-physician violation tracking: { physicianName: [{rule, description, date, shiftCode}] }
  const [physicianViolations, setPhysicianViolations] = useState({})

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
    setView('schedule')
  }, [])

  const handleBackToSetup = useCallback(() => {
    setView('setup')
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

  const handleSwapClick = useCallback(async (assignment) => {
    if (!swapMode) return
    if (!swapFirst) {
      setSwapFirst(assignment)
      return
    }
    // Second click — perform swap
    const a = swapFirst
    const b = assignment
    setSwapMode(false)
    setSwapFirst(null)
    try {
      await assignPhysician(a.date, a.shift.code, b.physician_id)
      await assignPhysician(b.date, b.shift.code, a.physician_id)
      const updated = await getSchedule()
      setScheduleData(updated)
    } catch (err) {
      console.error('Swap failed:', err)
      alert(`Swap failed: ${err.message}`)
    }
  }, [swapMode, swapFirst])

  // Cancel swap mode on Escape key
  useEffect(() => {
    if (!swapMode) return
    const handler = (e) => { if (e.key === 'Escape') handleCancelSwap() }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [swapMode, handleCancelSwap])

  return (
    <div className="flex flex-col h-screen bg-slate-50 overflow-hidden">
      <Header view={view} onBack={handleBackToSetup} />

      {view === 'setup' && (
        <div className="flex-1 overflow-auto p-6 space-y-6">
          <DirectoryPicker
            onImportDone={handleImportDone}
            onScheduleGenerated={handleScheduleGenerated}
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
