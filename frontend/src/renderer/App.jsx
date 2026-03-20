import React, { useState, useCallback } from 'react'
import Header from './components/Header'
import DirectoryPicker from './components/DirectoryPicker'
import ValidationPanel from './components/ValidationPanel'
import ScheduleGrid from './components/ScheduleGrid'
import Sidebar from './components/Sidebar'
import ConflictModal from './components/ConflictModal'
import { adjustSchedule } from './api'

export default function App() {
  // 'setup' | 'schedule'
  const [view, setView] = useState('setup')

  // Import state
  const [importResult, setImportResult] = useState(null)   // ImportDirectoryResponse
  const [scheduleData, setScheduleData] = useState(null)   // ScheduleResponse

  // Conflict modal state
  const [conflictSlot, setConflictSlot] = useState(null)   // UnfilledSlotSchema | null

  const handleImportDone = useCallback((result) => {
    setImportResult(result)
  }, [])

  const handleScheduleGenerated = useCallback((schedule) => {
    setScheduleData(schedule)
    setView('schedule')
  }, [])

  const handleBackToSetup = useCallback(() => {
    setView('setup')
  }, [])

  const handleOpenConflict = useCallback((slot) => {
    setConflictSlot(slot)
  }, [])

  const handleConflictAssigned = useCallback((updatedSchedule) => {
    setScheduleData(updatedSchedule)
    setConflictSlot(null)
  }, [])

  const handleConflictClose = useCallback(() => {
    setConflictSlot(null)
  }, [])

  const handleAdjust = useCallback(async (instruction) => {
    if (!scheduleData) throw new Error('No schedule loaded')
    const result = await adjustSchedule(instruction, scheduleData.year, scheduleData.month)
    setScheduleData(result.schedule)
    return result
  }, [scheduleData])

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
          <div className="flex-1 overflow-auto">
            <ScheduleGrid
              scheduleData={scheduleData}
              onOpenConflict={handleOpenConflict}
            />
          </div>
          <Sidebar scheduleData={scheduleData} onAdjust={handleAdjust} />
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
    </div>
  )
}
