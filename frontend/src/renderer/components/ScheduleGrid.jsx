import React, { useMemo } from 'react'

// Canonical shift order from Shifts.md.  null = block separator row.
// '__DOC__' and '__NOC__' are on-call rows rendered from scheduleData.on_calls.
const SHIFT_ORDER = [
  '__DOC__',
  '0600h RAH A side',
  '0600h RAH B side',
  '0600h NEHC',
  '0600h RAH I side',
  null,
  '0900h NEHC',
  '1000h RAH I side',
  '1200h RAH A side',
  '1200h RAH B side',
  '1200h NEHC',
  null,
  '1400h RAH I side',
  '1500h NEHC',
  '__NOC__',
  '1600h RAH F side',
  '1700h NEHC',
  null,
  '1800h RAH A side',
  '1800h RAH B side',
  '1800h RAH I side',
  '2000h NEHC',
  null,
  '2400h RAH A side',
  '2400h RAH B side',
  '2400h NEHC',
  '2400h RAH I side',
]

const DAY_NAMES = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']

// Cell background by group
function shiftBg(code, alpha = 'ff') {
  if (!code) return '#f8fafc'
  if (code.includes('RAH A') || code.includes('RAH B')) return `#dbeafe${alpha === 'ff' ? '' : alpha}`
  return `#dcfce7${alpha === 'ff' ? '' : alpha}`
}

function isoToLocal(str) {
  const [y, m, d] = str.split('-').map(Number)
  return new Date(y, m - 1, d)
}

// Group an array of sorted ISO date strings into weeks (Sunday–Saturday)
function groupIntoWeeks(sortedDates) {
  if (!sortedDates.length) return []
  const dateSet = new Set(sortedDates)

  // Start from the Sunday on or before the first date
  const first = isoToLocal(sortedDates[0])
  const startSun = new Date(first)
  startSun.setDate(startSun.getDate() - startSun.getDay())

  const last = isoToLocal(sortedDates[sortedDates.length - 1])
  const weeks = []
  const cur = new Date(startSun)

  while (cur <= last) {
    const week = []
    for (let i = 0; i < 7; i++) {
      const d = new Date(cur)
      d.setDate(d.getDate() + i)
      week.push(d.toISOString().slice(0, 10))
    }
    // Only include weeks that have at least one date in the schedule
    if (week.some(d => dateSet.has(d))) weeks.push(week)
    cur.setDate(cur.getDate() + 7)
  }
  return weeks
}

function weekLabel(weekDates) {
  const fmt = str => {
    const d = isoToLocal(str)
    return d.toLocaleDateString('default', { month: 'short', day: 'numeric' })
  }
  return `${fmt(weekDates[0])} – ${fmt(weekDates[6])}`
}

function truncateName(name, max = 13) {
  if (!name) return ''
  if (name.length <= max) return name
  const parts = name.trim().split(/\s+/)
  if (parts.length > 1) {
    const last = parts[parts.length - 1]
    return last.length <= max ? last : last.slice(0, max - 1) + '…'
  }
  return name.slice(0, max - 1) + '…'
}

export default function ScheduleGrid({ scheduleData, onOpenConflict, onReplaceAssignment, swapMode, swapFirst, onSwapClick }) {
  const { assignments = [], unfilled = [], on_calls = [], year, month } = scheduleData

  const dates = useMemo(() => {
    const s = new Set()
    assignments.forEach(a => s.add(a.date))
    unfilled.forEach(u => s.add(u.date))
    return Array.from(s).sort()
  }, [assignments, unfilled])

  const assignmentMap = useMemo(() => {
    const m = {}
    assignments.forEach(a => { m[`${a.date}||${a.shift.code}`] = a })
    return m
  }, [assignments])

  const unfilledMap = useMemo(() => {
    const m = {}
    unfilled.forEach(u => { m[`${u.date}||${u.shift.code}`] = u })
    return m
  }, [unfilled])

  const weeks = useMemo(() => groupIntoWeeks(dates), [dates])
  const dateSet = useMemo(() => new Set(dates), [dates])

  // Index on-calls by "date||call_type" -> physician_name
  const onCallsMap = useMemo(() => {
    const m = {}
    on_calls.forEach(oc => { m[`${oc.date}||${oc.call_type}`] = oc.physician_name })
    return m
  }, [on_calls])

  const monthName = new Date(year, month - 1, 1).toLocaleString('default', { month: 'long' })

  if (!dates.length) {
    return (
      <div className="flex items-center justify-center h-64 text-slate-400">
        No schedule data available.
      </div>
    )
  }

  return (
    <div className="p-4">
      {/* Legend */}
      <div className="mb-3 flex items-center gap-4">
        <h2 className="text-lg font-bold text-slate-800">{monthName} {year} Schedule</h2>
        <div className="flex items-center gap-2 text-xs">
          <span className="inline-block w-3 h-3 rounded-sm bg-blue-100" />
          <span className="text-slate-600">Group A (RAH A/B)</span>
          <span className="inline-block w-3 h-3 rounded-sm bg-emerald-100 ml-2" />
          <span className="text-slate-600">Group B</span>
          <span className="inline-block w-3 h-3 rounded-sm bg-red-200 ml-2" />
          <span className="text-slate-600">Unfilled</span>
          <span className="inline-block w-3 h-3 rounded-full bg-amber-400 ml-2" />
          <span className="text-slate-600">Manual</span>
        </div>
      </div>

      <div className="space-y-6">
        {weeks.map((weekDates) => (
          <WeekBlock
            key={weekDates[0]}
            weekDates={weekDates}
            dateSet={dateSet}
            assignmentMap={assignmentMap}
            unfilledMap={unfilledMap}
            onCallsMap={onCallsMap}
            onOpenConflict={onOpenConflict}
            onReplaceAssignment={onReplaceAssignment}
            swapMode={swapMode}
            swapFirst={swapFirst}
            onSwapClick={onSwapClick}
          />
        ))}
      </div>
    </div>
  )
}

function WeekBlock({ weekDates, dateSet, assignmentMap, unfilledMap, onCallsMap, onOpenConflict, onReplaceAssignment, swapMode, swapFirst, onSwapClick }) {
  return (
    <div className="overflow-auto rounded-lg border border-slate-200 shadow-sm">
      <table className="border-collapse text-xs whitespace-nowrap w-full">
        <thead>
          {/* Week label */}
          <tr>
            <th
              colSpan={8}
              className="bg-slate-700 text-white text-xs font-semibold px-3 py-1.5 text-left"
            >
              Week of {weekLabel(weekDates)}
            </th>
          </tr>
          {/* Day headers */}
          <tr className="bg-slate-100">
            <th
              className="sticky left-0 z-10 bg-slate-200 text-slate-600 text-xs font-semibold px-3 py-2 border-b border-r border-slate-300 text-left"
              style={{ minWidth: 160 }}
            >
              Shift
            </th>
            {weekDates.map(dateStr => {
              const d = isoToLocal(dateStr)
              const dow = DAY_NAMES[d.getDay()]
              const inSchedule = dateSet.has(dateStr)
              const isWeekend = d.getDay() === 0 || d.getDay() === 6
              return (
                <th
                  key={dateStr}
                  className={`text-center px-1 py-1.5 border-b border-l border-slate-300 text-xs font-semibold
                    ${isWeekend ? 'text-blue-700 bg-blue-50' : 'text-slate-700'}
                    ${!inSchedule ? 'opacity-30' : ''}`}
                  style={{ minWidth: 110 }}
                >
                  <div>{dow}</div>
                  <div className="font-normal text-slate-500 text-xs">
                    {d.toLocaleDateString('default', { month: 'short', day: 'numeric' })}
                  </div>
                </th>
              )
            })}
          </tr>
        </thead>
        <tbody>
          {SHIFT_ORDER.map((shiftCode, idx) => {
            if (shiftCode === null) {
              return (
                <tr key={`sep-${idx}`}>
                  <td colSpan={8} className="bg-slate-300 p-0" style={{ height: 3 }} />
                </tr>
              )
            }
            // On-call rows (DOC / NOC)
            if (shiftCode === '__DOC__' || shiftCode === '__NOC__') {
              const callType = shiftCode === '__DOC__' ? 'DOC' : 'NOC'
              const label = shiftCode === '__DOC__' ? 'Day On Call' : 'Night On Call'
              return (
                <tr key={shiftCode} className="bg-sky-50 hover:brightness-95">
                  <td
                    className="sticky left-0 z-10 bg-sky-100 border-b border-r border-sky-200 px-2 py-1 font-mono text-xs text-sky-700 font-semibold"
                    style={{ minWidth: 160 }}
                  >
                    {label}
                  </td>
                  {weekDates.map(dateStr => {
                    const inSchedule = dateSet.has(dateStr)
                    const name = inSchedule ? (onCallsMap[`${dateStr}||${callType}`] || '') : ''
                    return (
                      <td
                        key={dateStr}
                        style={{ minWidth: 110, height: 34, borderLeft: '1px solid #bae6fd', borderBottom: '1px solid #bae6fd', background: name ? '#e0f2fe' : '#f0f9ff' }}
                      >
                        {name && (
                          <div className="flex items-center h-full px-1.5">
                            <span className="truncate text-xs font-medium text-sky-800">{truncateName(name)}</span>
                          </div>
                        )}
                      </td>
                    )
                  })}
                </tr>
              )
            }
            return (
              <tr key={shiftCode} className="hover:brightness-95">
                {/* Shift label */}
                <td
                  className="sticky left-0 z-10 bg-white border-b border-r border-slate-200 px-2 py-1 font-mono text-xs text-slate-700"
                  style={{ minWidth: 160 }}
                >
                  {shiftCode}
                </td>
                {/* One cell per day */}
                {weekDates.map(dateStr => {
                  const inSchedule = dateSet.has(dateStr)
                  if (!inSchedule) {
                    return (
                      <td
                        key={dateStr}
                        className="border-b border-l border-slate-100"
                        style={{ minWidth: 110, background: '#f8fafc' }}
                      />
                    )
                  }
                  const key = `${dateStr}||${shiftCode}`
                  const assignment = assignmentMap[key]
                  const slot = unfilledMap[key]
                  return (
                    <ShiftCell
                      key={dateStr}
                      shiftCode={shiftCode}
                      assignment={assignment}
                      unfilledSlot={slot}
                      onOpenConflict={onOpenConflict}
                      onReplaceAssignment={onReplaceAssignment}
                      swapMode={swapMode}
                      swapFirst={swapFirst}
                      onSwapClick={onSwapClick}
                    />
                  )
                })}
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

function ShiftCell({ shiftCode, assignment, unfilledSlot, onOpenConflict, onReplaceAssignment, swapMode, swapFirst, onSwapClick }) {
  const base = { minWidth: 110, height: 34, borderLeft: '1px solid #e2e8f0', borderBottom: '1px solid #e2e8f0' }

  if (unfilledSlot) {
    return (
      <td
        style={{ ...base, background: '#fecaca', cursor: 'pointer' }}
        onClick={() => onOpenConflict(unfilledSlot)}
        title={`Click to resolve — ${unfilledSlot.candidates?.length ?? 0} options`}
      >
        <div className="flex items-center justify-center h-full">
          <span className="font-semibold text-red-700 text-xs">UNFILLED</span>
        </div>
      </td>
    )
  }

  if (assignment) {
    const name = assignment.physician_name || assignment.physician_id || '?'
    const badge = assignment.is_manual ? 'M' : null
    const badgeBg = 'bg-amber-400'

    // Check if this cell is the first selected in swap mode
    const isSwapFirst = swapFirst &&
      swapFirst.date === assignment.date &&
      swapFirst.shift?.code === assignment.shift?.code

    const cellBg = isSwapFirst ? '#fcd34d' : shiftBg(shiftCode)
    const cellTitle = swapMode
      ? (isSwapFirst ? `${name} — selected (click another to swap)` : `${name} — click to swap with selected`)
      : `${name}${assignment.is_manual ? ' (manual)' : ''} — click to replace`

    const handleClick = () => {
      if (swapMode) {
        onSwapClick && onSwapClick(assignment)
      } else {
        onReplaceAssignment && onReplaceAssignment(assignment)
      }
    }

    return (
      <td
        style={{ ...base, background: cellBg, cursor: 'pointer', outline: isSwapFirst ? '2px solid #f59e0b' : undefined }}
        title={cellTitle}
        onClick={handleClick}
      >
        <div className="flex items-center h-full px-1.5 gap-1">
          <span className="truncate text-xs font-medium text-slate-800 flex-1">
            {truncateName(name)}
          </span>
          {badge && (
            <span className={`flex-shrink-0 w-3.5 h-3.5 rounded-full ${badgeBg} text-white flex items-center justify-center font-bold leading-none`}
              style={{ fontSize: 9 }}>
              {badge}
            </span>
          )}
        </div>
      </td>
    )
  }

  // No assignment record — empty cell
  return (
    <td style={{ ...base, background: shiftBg(shiftCode, '28') }} />
  )
}
