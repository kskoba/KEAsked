import React, { useState } from 'react'

export default function ValidationPanel({ importResult }) {
  const { physicians = [], total_physicians, valid_physicians } = importResult
  const [expanded, setExpanded] = useState({})

  const validCount = physicians.filter(p => p.is_valid).length
  const totalCount = total_physicians ?? physicians.length

  function toggleRow(id) {
    setExpanded(prev => ({ ...prev, [id]: !prev[id] }))
  }

  return (
    <div className="bg-white rounded-xl shadow-sm border border-slate-200 overflow-hidden">
      {/* Summary bar */}
      <div className="flex items-center justify-between px-6 py-3 border-b border-slate-200 bg-slate-50">
        <h2 className="text-base font-semibold text-slate-800 flex items-center gap-2">
          <svg className="w-5 h-5 text-sky-500" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M17 20h5v-1a4 4 0 00-4-4H6a4 4 0 00-4 4v1h5M12 11a4 4 0 100-8 4 4 0 000 8z" />
          </svg>
          Physician Validation
        </h2>
        <div className="flex items-center gap-3">
          <div className={`px-3 py-1 rounded-full text-sm font-semibold ${
            validCount === totalCount
              ? 'bg-emerald-100 text-emerald-700'
              : 'bg-amber-100 text-amber-700'
          }`}>
            {validCount} / {totalCount} valid
          </div>
          {importResult.directory && (
            <span className="text-xs text-slate-400 font-mono truncate max-w-xs" title={importResult.directory}>
              {importResult.directory}
            </span>
          )}
        </div>
      </div>

      {/* Progress bar */}
      <div className="h-1.5 bg-slate-100">
        <div
          className={`h-full transition-all ${validCount === totalCount ? 'bg-emerald-500' : 'bg-amber-500'}`}
          style={{ width: totalCount > 0 ? `${(validCount / totalCount) * 100}%` : '0%' }}
        />
      </div>

      {/* Table */}
      <div className="overflow-auto max-h-96">
        <table className="w-full text-sm">
          <thead className="sticky top-0 bg-slate-50 border-b border-slate-200">
            <tr>
              <th className="text-left px-6 py-2.5 font-medium text-slate-600 w-8"></th>
              <th className="text-left px-3 py-2.5 font-medium text-slate-600">Physician</th>
              <th className="text-left px-3 py-2.5 font-medium text-slate-600">ID</th>
              <th className="text-right px-3 py-2.5 font-medium text-slate-600">Shifts Requested</th>
              <th className="text-center px-3 py-2.5 font-medium text-slate-600">Status</th>
              <th className="text-right px-6 py-2.5 font-medium text-slate-600">Issues</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {physicians.length === 0 && (
              <tr>
                <td colSpan={6} className="text-center py-8 text-slate-400">
                  No physician records found.
                </td>
              </tr>
            )}
            {physicians.map((physician) => {
              const isOpen = expanded[physician.physician_id]
              const issueCount = physician.issues ? physician.issues.length : 0

              return (
                <React.Fragment key={physician.physician_id}>
                  <tr
                    className={`hover:bg-slate-50 ${issueCount > 0 ? 'cursor-pointer' : ''}`}
                    onClick={() => issueCount > 0 && toggleRow(physician.physician_id)}
                  >
                    {/* Expand chevron */}
                    <td className="px-6 py-3 text-slate-400">
                      {issueCount > 0 && (
                        <svg
                          className={`w-4 h-4 transition-transform ${isOpen ? 'rotate-90' : ''}`}
                          fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}
                        >
                          <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
                        </svg>
                      )}
                    </td>

                    {/* Name */}
                    <td className="px-3 py-3 font-medium text-slate-800">
                      {physician.physician_name}
                    </td>

                    {/* ID */}
                    <td className="px-3 py-3 text-slate-500 font-mono text-xs">
                      {physician.physician_id}
                    </td>

                    {/* Shifts requested */}
                    <td className="px-3 py-3 text-right text-slate-700">
                      {physician.shifts_requested ?? '—'}
                    </td>

                    {/* Valid badge */}
                    <td className="px-3 py-3 text-center">
                      {physician.is_valid ? (
                        <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-emerald-100 text-emerald-700 text-xs font-medium">
                          <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2.5}>
                            <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                          </svg>
                          Valid
                        </span>
                      ) : (
                        <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-red-100 text-red-700 text-xs font-medium">
                          <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2.5}>
                            <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                          </svg>
                          Invalid
                        </span>
                      )}
                    </td>

                    {/* Issue count */}
                    <td className="px-6 py-3 text-right">
                      {issueCount > 0 ? (
                        <span className="inline-flex items-center justify-center min-w-[1.5rem] px-1.5 py-0.5 rounded-full bg-red-100 text-red-700 text-xs font-bold">
                          {issueCount}
                        </span>
                      ) : (
                        <span className="text-slate-300">—</span>
                      )}
                    </td>
                  </tr>

                  {/* Expanded issues */}
                  {isOpen && issueCount > 0 && (
                    <tr className="bg-red-50">
                      <td colSpan={6} className="px-12 py-3">
                        <ul className="space-y-1.5">
                          {physician.issues.map((issue, i) => (
                            <li key={i} className="flex items-start gap-2 text-sm">
                              <IssueIcon severity={issue.severity} />
                              <div>
                                <span className={`font-medium mr-1 ${
                                  issue.severity === 'error' ? 'text-red-700' : 'text-amber-700'
                                }`}>
                                  [{issue.severity?.toUpperCase() ?? 'INFO'}]
                                </span>
                                <span className="text-slate-700">{issue.message}</span>
                                {issue.rule && (
                                  <span className="ml-2 text-xs text-slate-400 font-mono">({issue.rule})</span>
                                )}
                              </div>
                            </li>
                          ))}
                        </ul>
                      </td>
                    </tr>
                  )}
                </React.Fragment>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}

function IssueIcon({ severity }) {
  if (severity === 'error') {
    return (
      <svg className="w-4 h-4 text-red-500 flex-shrink-0 mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}>
        <circle cx="12" cy="12" r="10" />
        <line x1="12" y1="8" x2="12" y2="12" />
        <line x1="12" y1="16" x2="12.01" y2="16" />
      </svg>
    )
  }
  return (
    <svg className="w-4 h-4 text-amber-500 flex-shrink-0 mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}>
      <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v4m0 4h.01M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z" />
    </svg>
  )
}
