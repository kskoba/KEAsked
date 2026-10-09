import React, { useState, useMemo } from 'react'

const SCHEDULING_RULES = [
  { id: '22h_spacing', label: '23-hour rule', desc: 'Minimum 23 hours between any two shifts for the same physician.' },
  { id: 'weekend_limit', label: 'Weekend limit', desc: 'Physicians are capped on the number of weekend shifts per schedule period.' },
  { id: 'anchor_limit', label: 'Anchor shift limit', desc: 'Limits the number of anchor (overnight/long) shifts per physician.' },
  { id: 'consecutive_limit', label: 'Consecutive shift limit', desc: 'No physician may work more than a defined number of consecutive days.' },
  { id: 'forbidden_sites', label: 'Forbidden sites', desc: 'Some physicians cannot be assigned to specific sites (e.g., no paediatric experience).' },
  { id: 'paired_exclusions', label: 'Paired exclusions', desc: 'Certain physician pairs cannot be scheduled on the same shift.' },
  { id: 'group_mix', label: 'Acute/Non-acute mix', desc: 'The schedule must maintain the required ratio of Acute vs Non-acute shifts.' },
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

export default function Sidebar({ scheduleData, importResult = null, physicianViolations = {}, swapMode = false, onToggleSwap = null }) {
  const [statsOpen, setStatsOpen] = useState(true)
  const [issuesOpen, setIssuesOpen] = useState(true)
  const [rulesOpen, setRulesOpen] = useState(false)

  const { stats = {}, issues = [], assignments = [] } = scheduleData

  // Build min/requested/max lookup from importResult
  // Keys: exact name, lowercase name, and physician_id (all pointing to same entry)
  const physicianLimits = useMemo(() => {
    if (!importResult?.physicians?.length) return {}
    const map = {}
    importResult.physicians.forEach(p => {
      const entry = { min: p.shifts_min, requested: p.shifts_requested, max: p.shifts_max }
      if (p.physician_name) map[p.physician_name] = entry
      if (p.physician_name) map[p.physician_name.toLowerCase()] = entry
      if (p.physician_id) map[p.physician_id] = entry
      if (p.physician_id) map[p.physician_id.toLowerCase()] = entry
    })
    return map
  }, [importResult])

  // Per-physician Acute/Non-acute breakdown computed from assignments.
  // Keyed by physician_id — must match physician_counts' keys (also
  // physician_id, see generator.py/generator_cpsat.py's _compute_stats),
  // not physician_name, or every lookup below silently misses and falls
  // back to the 50/50 default.
  const physicianGroupCounts = useMemo(() => {
    const counts = {}
    assignments.forEach(a => {
      const key = a.physician_id
      if (!counts[key]) counts[key] = { a: 0, b: 0 }
      if (a.shift?.site_group === 'A') counts[key].a++
      else counts[key].b++
    })
    return counts
  }, [assignments])

  const {
    total_slots = 0,
    filled_slots = 0,
    unfilled_slots = 0,
    physician_counts = {},
    physician_singletons = {},
    solver_status = null,
    optimality_gap_pct = null,
    solve_seconds = null,
    stalled_seconds = null,
    recent_gain_pct = null,
    recent_gain_window_seconds = null,
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

  // True if any physician has importResult data with min/max available
  const hasLimits = Object.keys(physicianLimits).length > 0

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
              {/* Solution quality badge + shift swap toggle */}
              <div className="flex items-center gap-2 flex-wrap">
                {solver_status && (() => {
                // Verdict (2026-10-07): convergence first, gap second. The
                // objective now carries many step/indicator penalty terms
                // whose LP relaxation leaves the proven bound several
                // percent above any reachable schedule, so a fully
                // converged run reads ~6% and the old "<5% good, else
                // sub-optimal" cutoffs mislabelled it. What actually tells
                // you whether more solver time would help is whether the
                // search was still finding better schedules at the end.
                const isOptimal = solver_status === 'optimal'
                const gap = optimality_gap_pct ?? 0
                const gapText = optimality_gap_pct != null ? `${gap < 1 ? gap.toFixed(2) : gap.toFixed(1)}% gap` : 'gap unknown'
                const stalled = solve_seconds != null && stalled_seconds != null ? stalled_seconds : null
                // "Converged": the score barely moved over the final window
                // (last 10 min, or last quarter of a shorter run) -- under
                // 0.1% of the objective is tie-break crumbs, whatever the
                // stall timer says. Seen on a real 60-min run: 0.04% in the
                // last 10 min, yet a 6-point improvement 8 s before the end.
                // Falls back to the stall timer for older results.
                const CONVERGED_GAIN_PCT = 0.1
                const convergeAfter = solve_seconds != null ? Math.min(600, Math.max(60, solve_seconds * 0.25)) : null
                const flattened = recent_gain_pct != null && recent_gain_pct < CONVERGED_GAIN_PCT
                const converged = flattened || (stalled !== null && stalled >= convergeAfter)
                const stillImproving = !flattened && stalled !== null && stalled < 30
                const gainText = recent_gain_pct != null && recent_gain_window_seconds != null
                  ? `the score moved ${recent_gain_pct}% in the last ${Math.round(recent_gain_window_seconds / 60)} min`
                  : null
                const mins = (s) => s >= 90 ? `${Math.round(s / 60)} min` : `${Math.round(s)} s`
                let label, colors, dotColor, tooltip
                if (isOptimal) {
                  label = 'Optimal solution'
                  colors = 'bg-emerald-50 border-emerald-300 text-emerald-700'; dotColor = 'bg-emerald-500'
                  tooltip = 'CP-SAT proved this is the mathematically best possible schedule'
                } else if (converged) {
                  label = `Converged (${gapText})`
                  colors = 'bg-sky-50 border-sky-300 text-sky-700'; dotColor = 'bg-sky-500'
                  tooltip = (gainText
                    ? `In a ${mins(solve_seconds)} run ${gainText}: tie-break-sized changes only.`
                    : `No better schedule was found in the last ${mins(stalled)} of a ${mins(solve_seconds)} run.`)
                    + ` The ${gapText} is the distance to a bound the solver could not tighten further, not evidence of a better schedule. More time is unlikely to help.`
                } else if (gap < 3) {
                  label = `Near-optimal (${gapText})`
                  colors = 'bg-sky-50 border-sky-300 text-sky-700'; dotColor = 'bg-sky-500'
                  tooltip = `Best schedule is within ${gapText} of the proven bound.`
                } else if (stillImproving) {
                  label = `Still improving when time ran out (${gapText})`
                  colors = 'bg-amber-50 border-amber-300 text-amber-700'; dotColor = 'bg-amber-500'
                  tooltip = `The solver found a better schedule in the last ${mins(stalled)} of the run${gainText ? ` and ${gainText}` : ''} — a longer time limit would likely improve this.`
                } else if (gap < 8) {
                  label = `Good solution (${gapText})`
                  colors = 'bg-amber-50 border-amber-300 text-amber-700'; dotColor = 'bg-amber-500'
                  tooltip = stalled !== null
                    ? `Last improvement ${mins(stalled)} before the end of a ${mins(solve_seconds)} run; ${gapText} to the proven bound.`
                    : `Best schedule is within ${gapText} of the proven bound. More solver time may improve this.`
                } else {
                  label = `Sub-optimal (${gapText})`
                  colors = 'bg-red-50 border-red-300 text-red-700'; dotColor = 'bg-red-500'
                  tooltip = `Best schedule is ${gapText} from the proven bound and the run had not converged. More solver time should help.`
                }
                return (
                  <div className={`flex items-center gap-2 px-3 py-2 rounded-md border text-xs font-medium ${colors}`} title={tooltip}>
                    <span className={`inline-block w-2 h-2 rounded-full flex-shrink-0 ${dotColor}`} />
                    <span>{label}</span>
                  </div>
                )
                })()}

                {onToggleSwap && (
                  <button
                    onClick={onToggleSwap}
                    className={`inline-flex items-center gap-1.5 px-3 py-2 rounded-md text-xs font-semibold border transition-colors
                      ${swapMode
                        ? 'bg-amber-400 border-amber-500 text-amber-900 hover:bg-amber-300'
                        : 'bg-white border-slate-300 text-slate-700 hover:bg-slate-100'
                      }`}
                    title="Toggle shift swap mode"
                  >
                    ⇄ Shift Swap
                  </button>
                )}
              </div>

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

              {/* Per-physician counts */}
              {Object.keys(physician_counts).length > 0 && (
                <div>
                  <p className="text-xs font-medium text-slate-600 mb-1">Shifts per Physician</p>
                  {/* Column headers */}
                  <div className="flex items-center text-xs text-slate-400 mb-1 gap-1">
                    <span className="flex-1" />
                    {/* violations spacer */}
                    <span className="w-5" />
                    {/* shifts column */}
                    <span
                      className={`text-right font-medium ${hasLimits ? 'w-20' : 'w-7'}`}
                      title={hasLimits ? 'Current shifts (requested/max)' : 'Shifts assigned'}
                    >
                      {hasLimits ? 'shifts (r/max)' : 'shifts'}
                    </span>
                    {/* singleton column */}
                    <span
                      className="w-8 text-right pr-2"
                      title="Isolated 2400h night shifts (singletons)"
                    >
                      solo↑
                    </span>
                  </div>
                  <div className="space-y-2 max-h-72 overflow-auto">
                    {Object.entries(physician_counts)
                      .sort((a, b) => b[1] - a[1])
                      .map(([name, count]) => {
                        const singletons = physician_singletons[name] || 0
                        const g = physicianGroupCounts[name] || { a: 0, b: 0 }
                        const total = g.a + g.b
                        const aPct = total > 0 ? (g.a / total) * 100 : 50
                        const limits = physicianLimits[name] || physicianLimits[name?.toLowerCase()] || null
                        const violations = physicianViolations[name] || []
                        const violationCount = violations.length
                        const violationTitle = violations
                          .map(v => `${v.date} ${v.shiftCode}: ${v.description || v.rule}`)
                          .join('\n')

                        return (
                          <div key={name}>
                            <div className="flex items-center gap-1 text-xs">
                              <span className="truncate flex-1 text-slate-700">{name}</span>
                              {/* Violation badge */}
                              {violationCount > 0 ? (
                                <span
                                  className="flex-shrink-0 w-5 h-5 rounded-full bg-red-500 text-white flex items-center justify-center font-bold cursor-help"
                                  style={{ fontSize: 9 }}
                                  title={`${violationCount} active rule violation${violationCount !== 1 ? 's' : ''}:\n${violationTitle}`}
                                >
                                  {violationCount}
                                </span>
                              ) : (
                                <span className="flex-shrink-0 w-5" />
                              )}
                              {/* Shift count with optional req/max */}
                              {limits ? (
                                <span className="flex-shrink-0 w-20 text-right">
                                  <span className="font-bold text-slate-800">{count}</span>
                                  <span className="text-slate-400" style={{ fontSize: 9 }}>
                                    {' '}({limits.requested}/{limits.max})
                                  </span>
                                </span>
                              ) : (
                                <span className="flex-shrink-0 w-7 text-right font-bold text-slate-800">
                                  {count}
                                </span>
                              )}
                              {/* Singleton count */}
                              <span
                                className={`flex-shrink-0 w-8 text-right pr-2 font-medium ${singletons > 0 ? 'text-amber-500' : 'text-slate-300'}`}
                                title={singletons > 0 ? `${singletons} isolated 2400h night(s)` : 'No isolated nights'}
                              >
                                {singletons > 0 ? singletons : '-'}
                              </span>
                            </div>
                            {/* Acute/Non-acute balance bar with 40% target line */}
                            <div
                              className="relative h-1.5 rounded-full overflow-hidden flex mt-0.5"
                              title={`Acute: ${g.a} (${Math.round(aPct)}%)  Non-acute: ${g.b} (${Math.round(100 - aPct)}%)  Target: 40% Acute / 60% Non-acute`}
                            >
                              <div className="h-full bg-blue-400 transition-all" style={{ width: `${aPct}%` }} />
                              <div className="h-full bg-emerald-400 flex-1" />
                              {/* Target line at 40% */}
                              <div
                                className="absolute top-0 bottom-0 w-px bg-white opacity-80"
                                style={{ left: '40%' }}
                              />
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
    </aside>
  )
}
