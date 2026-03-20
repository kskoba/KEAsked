import React, { useState, useMemo } from 'react'

const SCHEDULING_RULES = [
  { id: '22h_spacing', label: '22-hour spacing', desc: 'Minimum 22 hours between any two shifts for the same physician.' },
  { id: 'weekend_limit', label: 'Weekend limit', desc: 'Physicians are capped on the number of weekend shifts per schedule period.' },
  { id: 'anchor_limit', label: 'Anchor shift limit', desc: 'Limits the number of anchor (overnight/long) shifts per physician.' },
  { id: 'consecutive_limit', label: 'Consecutive shift limit', desc: 'No physician may work more than a defined number of consecutive days.' },
  { id: 'forbidden_sites', label: 'Forbidden sites', desc: 'Some physicians cannot be assigned to specific sites (e.g., no paediatric experience).' },
  { id: 'paired_exclusions', label: 'Paired exclusions', desc: 'Certain physician pairs cannot be scheduled on the same shift.' },
  { id: 'group_mix', label: 'Group A/B mix', desc: 'The schedule must maintain the required ratio of Group A vs Group B physicians.' },
  { id: 'singleton_rules', label: 'Singleton rules', desc: 'Specific shifts require a designated lead physician (singleton).' },
  { id: 'night_q_limit', label: 'Night call limit', desc: 'Physicians have stated maximum night/overnight calls per period.' },
  { id: 'pref_respected', label: 'Preference blocks', desc: 'Physician-submitted preference blocks (want/avoid/unavailable) are respected.' },
  { id: 'site_competency', label: 'Site competency', desc: 'Assignments must match physician site competencies.' }
]

function SectionHeader({ title, open, onToggle, icon }) {
  return (
    <button
      className="flex items-center justify-between w-full px-4 py-3 text-left hover:bg-slate-50 transition-colors"
      onClick={onToggle}
    >
      <div className="flex items-center gap-2 font-semibold text-slate-700 text-sm">
        {icon}
        {title}
      </div>
      <svg
        className={`w-4 h-4 text-slate-400 transition-transform ${open ? 'rotate-180' : ''}`}
        fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}
      >
        <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
      </svg>
    </button>
  )
}

export default function Sidebar({ scheduleData, onAdjust }) {
  const [statsOpen, setStatsOpen] = useState(true)
  const [issuesOpen, setIssuesOpen] = useState(true)
  const [rulesOpen, setRulesOpen] = useState(false)
  const [instruction, setInstruction] = useState('')
  const [adjusting, setAdjusting] = useState(false)
  const [adjustResult, setAdjustResult] = useState(null)

  const { stats = {}, issues = [], assignments = [] } = scheduleData

  // Per-physician Group A/B breakdown computed from assignments
  const physicianGroupCounts = useMemo(() => {
    const counts = {}
    assignments.forEach(a => {
      const name = a.physician_name || a.physician_id
      if (!counts[name]) counts[name] = { a: 0, b: 0 }
      if (a.shift?.site_group === 'A') counts[name].a++
      else counts[name].b++
    })
    return counts
  }, [assignments])

  async function handleAdjust() {
    if (!instruction.trim() || !onAdjust) return
    setAdjusting(true)
    setAdjustResult(null)
    try {
      const result = await onAdjust(instruction.trim())
      setAdjustResult(result)
      setInstruction('')
    } catch (err) {
      setAdjustResult({ error: err.message })
    } finally {
      setAdjusting(false)
    }
  }

  const {
    total_slots = 0,
    filled_slots = 0,
    unfilled_slots = 0,
    group_a_count = 0,
    group_b_count = 0,
    group_a_pct = 0,
    group_b_pct = 0,
    physician_counts = {},
    physician_singletons = {}
  } = stats

  const fillPct = total_slots > 0 ? Math.round((filled_slots / total_slots) * 100) : 0

  const errorIssues = issues.filter(i =>
    (typeof i === 'string' ? i : i.message || '').toLowerCase().includes('error') ||
    (typeof i === 'object' && i.severity === 'error')
  )
  const warnIssues = issues.filter(i =>
    (typeof i === 'string' ? i : i.message || '').toLowerCase().includes('warn') ||
    (typeof i === 'object' && i.severity === 'warning')
  )

  return (
    <aside className="w-80 flex-shrink-0 bg-white border-l border-slate-200 flex flex-col overflow-hidden">
      <div className="px-4 py-3 bg-slate-800 text-white flex-shrink-0">
        <h2 className="font-bold text-sm tracking-wide">Schedule Details</h2>
      </div>

      <div className="flex-1 overflow-auto divide-y divide-slate-200">

        {/* ── Stats Section ── */}
        <div>
          <SectionHeader
            title="Statistics"
            open={statsOpen}
            onToggle={() => setStatsOpen(v => !v)}
            icon={
              <svg className="w-4 h-4 text-sky-500" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
              </svg>
            }
          />
          {statsOpen && (
            <div className="px-4 pb-4 space-y-4">
              {/* Fill rate */}
              <div>
                <div className="flex justify-between text-xs text-slate-600 mb-1">
                  <span>Fill Rate</span>
                  <span className="font-bold">{fillPct}%</span>
                </div>
                <div className="h-2 bg-slate-100 rounded-full overflow-hidden">
                  <div
                    className={`h-full rounded-full transition-all ${fillPct === 100 ? 'bg-emerald-500' : fillPct > 80 ? 'bg-sky-500' : 'bg-amber-500'}`}
                    style={{ width: `${fillPct}%` }}
                  />
                </div>
                <div className="flex justify-between text-xs text-slate-500 mt-1">
                  <span>{filled_slots} filled</span>
                  <span className="text-red-500">{unfilled_slots} unfilled</span>
                </div>
              </div>

              {/* Group A */}
              <div>
                <div className="flex justify-between text-xs text-slate-600 mb-1">
                  <span className="text-blue-700 font-medium">Group A (RAH A/B)</span>
                  <span className="font-bold text-blue-700">{group_a_count} shifts ({Math.round(group_a_pct)}%)</span>
                </div>
                <div className="h-2 bg-slate-100 rounded-full overflow-hidden">
                  <div
                    className="h-full rounded-full bg-blue-400 transition-all"
                    style={{ width: `${Math.min(group_a_pct, 100)}%` }}
                  />
                </div>
              </div>

              {/* Group B */}
              <div>
                <div className="flex justify-between text-xs text-slate-600 mb-1">
                  <span className="text-emerald-700 font-medium">Group B (I/NEHC/F)</span>
                  <span className="font-bold text-emerald-700">{group_b_count} shifts ({Math.round(group_b_pct)}%)</span>
                </div>
                <div className="h-2 bg-slate-100 rounded-full overflow-hidden">
                  <div
                    className="h-full rounded-full bg-emerald-400 transition-all"
                    style={{ width: `${Math.min(group_b_pct, 100)}%` }}
                  />
                </div>
              </div>

              {/* Per-physician counts */}
              {Object.keys(physician_counts).length > 0 && (
                <div>
                  <p className="text-xs font-medium text-slate-600 mb-1">Shifts per Physician</p>
                  <div className="flex justify-end gap-3 text-xs text-slate-400 mb-1 pr-1">
                    <span className="w-6 text-right">shifts</span>
                    <span className="w-6 text-right" title="Isolated 2400h night shifts (singletons)">solo↑</span>
                  </div>
                  <div className="space-y-2 max-h-64 overflow-auto">
                    {Object.entries(physician_counts)
                      .sort((a, b) => b[1] - a[1])
                      .map(([name, count]) => {
                        const singletons = physician_singletons[name] || 0
                        const g = physicianGroupCounts[name] || { a: 0, b: 0 }
                        const total = g.a + g.b
                        const aPct = total > 0 ? (g.a / total) * 100 : 50
                        return (
                          <div key={name}>
                            <div className="flex items-center gap-2 text-xs">
                              <span className="truncate flex-1 text-slate-700">{name}</span>
                              <span className="flex-shrink-0 font-bold text-slate-800 w-6 text-right">{count}</span>
                              <span
                                className={`flex-shrink-0 w-6 text-right font-medium ${singletons > 0 ? 'text-amber-500' : 'text-slate-300'}`}
                                title={singletons > 0 ? `${singletons} isolated 2400h night(s)` : 'No isolated nights'}
                              >
                                {singletons > 0 ? singletons : '-'}
                              </span>
                            </div>
                            {/* Group A/B balance bar */}
                            <div
                              className="h-1.5 rounded-full overflow-hidden flex mt-0.5"
                              title={`Group A: ${g.a} (${Math.round(aPct)}%)  Group B: ${g.b} (${Math.round(100 - aPct)}%)`}
                            >
                              <div className="h-full bg-blue-400 transition-all" style={{ width: `${aPct}%` }} />
                              <div className="h-full bg-emerald-400 flex-1" />
                            </div>
                          </div>
                        )
                      })
                    }
                  </div>
                </div>
              )}
            </div>
          )}
        </div>

        {/* ── Issues Section ── */}
        <div>
          <SectionHeader
            title={`Issues ${issues.length > 0 ? `(${issues.length})` : ''}`}
            open={issuesOpen}
            onToggle={() => setIssuesOpen(v => !v)}
            icon={
              <svg className="w-4 h-4 text-amber-500" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v4m0 4h.01M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z" />
              </svg>
            }
          />
          {issuesOpen && (
            <div className="px-4 pb-4">
              {issues.length === 0 ? (
                <div className="flex items-center gap-2 text-emerald-600 text-sm py-2">
                  <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                  </svg>
                  No issues found
                </div>
              ) : (
                <div className="space-y-1.5 max-h-64 overflow-auto">
                  {issues.map((issue, i) => {
                    const text = typeof issue === 'string' ? issue : (issue.message || JSON.stringify(issue))
                    const severity = typeof issue === 'object' ? issue.severity : null
                    const isError = severity === 'error' || text.toLowerCase().includes('error')
                    return (
                      <div
                        key={i}
                        className={`flex items-start gap-2 p-2 rounded text-xs ${
                          isError ? 'bg-red-50 text-red-700' : 'bg-amber-50 text-amber-700'
                        }`}
                      >
                        {isError ? (
                          <svg className="w-3.5 h-3.5 flex-shrink-0 mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}>
                            <circle cx="12" cy="12" r="10" /><line x1="12" y1="8" x2="12" y2="12" /><line x1="12" y1="16" x2="12.01" y2="16" />
                          </svg>
                        ) : (
                          <svg className="w-3.5 h-3.5 flex-shrink-0 mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}>
                            <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v4m0 4h.01M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z" />
                          </svg>
                        )}
                        <span className="leading-snug">{text}</span>
                      </div>
                    )
                  })}
                </div>
              )}
            </div>
          )}
        </div>

        {/* ── Rules Section ── */}
        <div>
          <SectionHeader
            title="Rules Applied"
            open={rulesOpen}
            onToggle={() => setRulesOpen(v => !v)}
            icon={
              <svg className="w-4 h-4 text-slate-500" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-6 9l2 2 4-4" />
              </svg>
            }
          />
          {rulesOpen && (
            <div className="px-4 pb-4">
              <ul className="space-y-2">
                {SCHEDULING_RULES.map(rule => (
                  <li key={rule.id} className="flex items-start gap-2" title={rule.desc}>
                    <svg className="w-3.5 h-3.5 text-emerald-500 flex-shrink-0 mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2.5}>
                      <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                    </svg>
                    <div>
                      <span className="text-xs font-medium text-slate-700">{rule.label}</span>
                      <p className="text-xs text-slate-400 leading-snug">{rule.desc}</p>
                    </div>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>

      </div>

      {/* ── AI Adjustment Input ── */}
      <div className="flex-shrink-0 border-t border-slate-200 bg-white p-3 space-y-2">
        <p className="text-xs font-semibold text-slate-600 flex items-center gap-1">
          <svg className="w-3.5 h-3.5 text-violet-500" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
          </svg>
          AI Schedule Adjustment
        </p>
        <textarea
          className="w-full text-xs border border-slate-200 rounded p-2 resize-none focus:outline-none focus:ring-1 focus:ring-violet-400 text-slate-700 placeholder-slate-300"
          rows={2}
          placeholder="e.g. Give Wittmeier 2 fewer shifts and give Lam-Rico 2 more"
          value={instruction}
          onChange={e => setInstruction(e.target.value)}
          disabled={adjusting}
          onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleAdjust() } }}
        />
        <button
          onClick={handleAdjust}
          disabled={adjusting || !instruction.trim()}
          className="w-full py-1.5 text-xs font-semibold rounded bg-violet-500 text-white hover:bg-violet-600 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
        >
          {adjusting ? 'Asking Claude…' : 'Apply'}
        </button>
        {adjustResult && (
          <div className="text-xs space-y-1">
            {adjustResult.error && (
              <p className="text-red-600">{adjustResult.error}</p>
            )}
            {adjustResult.applied?.length > 0 && (
              <div className="bg-emerald-50 rounded p-1.5 space-y-0.5">
                {adjustResult.applied.map((a, i) => (
                  <p key={i} className="text-emerald-700">✓ {a}</p>
                ))}
              </div>
            )}
            {adjustResult.rejected?.length > 0 && (
              <div className="bg-amber-50 rounded p-1.5 space-y-0.5">
                {adjustResult.rejected.map((r, i) => (
                  <p key={i} className="text-amber-700">✗ {r}</p>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    </aside>
  )
}
