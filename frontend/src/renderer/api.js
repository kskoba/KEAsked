let BASE_URL = 'http://127.0.0.1:5000'

/**
 * Set the backend base URL — called once at startup (see App.jsx) after
 * resolving the configured backend location (local, or a remote host like
 * a Docker container on another machine). Local dev/packaged default stays
 * http://127.0.0.1:5000 until this is called.
 */
export function setApiBaseUrl(url) {
  if (url) BASE_URL = url.replace(/\/+$/, '')
}

export function getApiBaseUrl() {
  return BASE_URL
}

async function request(method, path, body) {
  const options = {
    method,
    headers: { 'Content-Type': 'application/json' }
  }
  if (body !== undefined) {
    options.body = JSON.stringify(body)
  }

  const res = await fetch(`${BASE_URL}${path}`, options)

  if (!res.ok) {
    let errorMsg = `HTTP ${res.status} ${res.statusText}`
    try {
      const errData = await res.json()
      if (errData.detail) errorMsg = errData.detail
      else if (errData.message) errorMsg = errData.message
    } catch {
      // ignore JSON parse error
    }
    const err = new Error(errorMsg)
    err.status = res.status   // lets callers tell a 400 refusal from a network failure
    throw err
  }

  return res.json()
}

/**
 * Check API health.
 * @returns {{ status: string }}
 */
export async function checkHealth() {
  return request('GET', '/api/health')
}

/**
 * List all known physicians.
 * @returns {{ physicians: Array }}
 */
export async function getPhysicians() {
  return request('GET', '/api/physicians')
}

/**
 * Import physician submissions from a directory of per-physician xlsx files.
 */
export async function importSubmissions(directory, year, month) {
  return request('POST', '/api/import', { directory, year, month })
}

/**
 * Import physician submissions from a single flat-table xlsx file.
 */
export async function importFlatFile(file, year, month) {
  return request('POST', '/api/import-flat', { file, year, month })
}

/**
 * Detect the year/month encoded in a flat file without importing it.
 * @returns {{ year: number, month: number }}
 */
export async function detectFlatMonth(file) {
  return request('GET', `/api/detect-flat?file=${encodeURIComponent(file)}`)
}

/**
 * Generate a schedule from previously imported submissions.
 */
export async function generateSchedule(year, month, timeLimitSeconds) {
  return request('POST', '/api/generate', { year, month, time_limit_seconds: timeLimitSeconds ?? null })
}

/**
 * Poll the generation progress (non-blocking, call while generateSchedule is running).
 * @returns {{ current: number, total: number, running: boolean, best_unfilled: number|null }}
 */
export async function getGenerateProgress() {
  return request('GET', '/api/generate-progress')
}

/**
 * Fetch the most recently generated schedule.
 * @returns {ScheduleResponse}
 */
export async function getSchedule() {
  return request('GET', '/api/schedule')
}

/**
 * Request that an in-progress generate run stop early and return its
 * best-found-so-far result.
 */
export async function cancelGenerate() {
  return request('POST', '/api/generate-cancel')
}

/**
 * Mark one validation issue (physician_id + rule) as overridden for this
 * session. Returns the refreshed ImportDirectoryResponse.
 */
export async function overrideIssue(physicianId, rule) {
  return request('POST', '/api/override', { physician_id: physicianId, rule })
}

/**
 * Undo a single override. Returns the refreshed ImportDirectoryResponse.
 */
export async function overrideClear(physicianId, rule) {
  return request('POST', '/api/override-clear', { physician_id: physicianId, rule })
}

/**
 * Override every current error-severity issue for one physician. Returns
 * the refreshed ImportDirectoryResponse.
 */
export async function overrideAll(physicianId) {
  return request('POST', '/api/override-all', { physician_id: physicianId })
}

/**
 * Plain-English error list per physician, excluding overridden issues.
 * @returns {{ items: { physician_id: string, physician_name: string, errors: string[] }[] }}
 */
export async function getValidationSummary() {
  return request('GET', '/api/validation-summary')
}

/**
 * Whether outgoing email (reminder notifications) is configured.
 * @returns {{ configured: boolean }}
 */
export async function getEmailStatus() {
  return request('GET', '/api/email/status')
}

/**
 * Email one physician their current validation errors. Backend looks up
 * their address from physicians.yaml and composes the message itself.
 * @param {string} physicianId
 * @returns {{ ok: boolean, status: string }}
 */
export async function sendReminderEmail(physicianId) {
  return request('POST', '/api/email/send-reminder', { physician_id: physicianId })
}

/**
 * Whether sked (the physician shift-preference site) connection is configured.
 * @returns {{ configured: boolean }}
 */
export async function getSkedStatus() {
  return request('GET', '/api/sked/status')
}

/**
 * Push the active roster + that period's master schedule template to sked,
 * generate each physician's signed magic link, and email it out. Physicians
 * with no email on file (or whose send fails) come back in needs_attention
 * rather than being silently skipped. Pass physician_ids to target just
 * those physicians instead of the whole active roster.
 * @param {{ period_id: string, label: string, opens_at: string, closes_at: string, template_path: string, extra_message?: string, physician_ids?: string[] }} payload
 * @returns {{ ok: boolean, sent_count: number, results: object[], needs_attention: object[] }}
 */
export async function sendMonthlyRequests(payload) {
  return request('POST', '/api/monthly-requests/send', payload)
}

/**
 * List annual-survey periods on sked.
 * @returns {{ surveys: { id: string, label: string, opens_at: string, closes_at: string }[] }}
 */
export async function getSurveys() {
  return request('GET', '/api/sked/surveys')
}

/**
 * Completion status for every active roster physician against one survey,
 * cross-referenced from sked's raw responses -- "not_started" for anyone
 * active but missing from sked entirely.
 * @param {string} surveyId
 * @returns {{ survey: object, rows: object[], submitted_count: number, total_active: number }}
 */
export async function getSurveyCompletion(surveyId) {
  return request('GET', `/api/sked/survey-completion?survey_id=${encodeURIComponent(surveyId)}`)
}

/**
 * Regenerate one physician's survey link and email it. Only for physicians
 * with no response yet -- backend looks up their address from physicians.yaml.
 * @param {string} surveyId
 * @param {string} physicianId
 * @returns {{ ok: boolean, status: string, detail: string }}
 */
export async function resendSurveyLink(surveyId, physicianId) {
  return request('POST', '/api/sked/survey/resend', { survey_id: surveyId, physician_id: physicianId })
}

/**
 * List existing sked periods, newest first -- for the "view a physician's
 * preference sheet" period picker (RosterEditor's Preferences section).
 * @param {string} [kind='shift_request'] 'shift_request' | 'survey'
 * @returns {{ periods: { id: string, label: string, opens_at: string, closes_at: string }[] }}
 */
export async function getSkedPeriods(kind = 'shift_request') {
  return request('GET', `/api/sked/periods?kind=${encodeURIComponent(kind)}`)
}

/**
 * Generate (or regenerate) one physician's sked magic link for an existing
 * period, to open directly -- never emailed, unlike sendMonthlyRequests /
 * resendSurveyLink. Safe to call repeatedly.
 * @param {string} physicianId
 * @param {string} periodId
 * @returns {{ url: string }}
 */
export async function getPhysicianLink(physicianId, periodId) {
  return request('POST', '/api/sked/physician-link', { physician_id: physicianId, period_id: periodId })
}

/**
 * Pull in shift-preference submissions directly from sked for one period,
 * instead of a directory/flat file of xlsx exports. Returns the same shape
 * as importSubmissions/importFlatFile, plus a not_submitted list (active
 * roster physicians with no submission yet, or still "draft") for a
 * highlight + reminder UI.
 * @param {string} periodId
 * @param {number} year
 * @param {number} month
 * @returns {{ year, month, directory, physicians, total_physicians, valid_physicians,
 *   not_submitted: { physician_id: string, physician_name: string, status: string, email: string }[] }}
 */
export async function importFromSked(periodId, year, month) {
  return request('POST', '/api/sked/import', { period_id: periodId, year, month })
}

/**
 * Regenerate one physician's shift-request link and email it. Only for the
 * "not submitted yet" highlight list -- backend looks up their address from
 * physicians.yaml.
 * @param {string} periodId
 * @param {string} physicianId
 * @returns {{ ok: boolean, status: string, detail: string }}
 */
export async function resendMonthlyRequest(periodId, physicianId) {
  return request('POST', '/api/monthly-requests/resend', { period_id: periodId, physician_id: physicianId })
}

/**
 * What's been overridden this session and why — for deciding whether any
 * should become a permanent rule_override in physicians.yaml.
 * @returns {{ items: { physician_name: string, rule: string, message: string }[] }}
 */
export async function getOverrideLog() {
  return request('GET', '/api/override-log')
}

/**
 * Manually assign a physician to a shift slot (replaces any existing occupant).
 * @param {string} date          ISO date string e.g. "2026-06-01"
 * @param {string} shiftCode     e.g. "0600h RAH A side"
 * @param {string} physicianId
 * @returns {ManualAssignResponse}
 */
export async function assignPhysician(date, shiftCode, physicianId) {
  return request('POST', '/api/assign', {
    date,
    shift_code: shiftCode,
    physician_id: physicianId
  })
}

/**
 * Swap the physicians in two filled slots, evaluated against the POST-swap
 * state (both physicians are lifted out before either is checked in the
 * other's slot, so a same-day A-side/B-side trade is not a double booking).
 * @param {{date: string, shift: {code: string}}} a   first assignment (as shown in the grid)
 * @param {{date: string, shift: {code: string}}} b   second assignment
 * @param {boolean} dryRun   true = report violations only, change nothing
 * @returns {SwapResponse}  { success, applied, a: {physician_id, physician_name, date, shift_code, violations}, b: {...}, message }
 */
export async function swapAssignments(a, b, dryRun = false) {
  return request('POST', '/api/swap', {
    a: { date: a.date, shift_code: a.shift.code },
    b: { date: b.date, shift_code: b.shift.code },
    dry_run: dryRun
  })
}

/**
 * Check rule violations for assigning a physician to a slot WITHOUT actually assigning.
 * @param {string} date
 * @param {string} shiftCode
 * @param {string} physicianId
 * @returns {{ violations: ViolationSchema[] }}
 */
export async function checkViolations(date, shiftCode, physicianId) {
  return request('POST', '/api/check-violations', {
    date,
    shift_code: shiftCode,
    physician_id: physicianId
  })
}

/**
 * Load a previously exported schedule xlsx as the active schedule.
 * @param {string} file  Absolute path to the .xlsx file
 * @returns {ScheduleResponse}
 */
export async function loadScheduleFromFile(file) {
  return request('POST', '/api/load-schedule', { file })
}

/**
 * Whether the master Google Sheet connection is configured.
 * @returns {{ configured: boolean }}
 */
export async function getGoogleSheetsStatus() {
  return request('GET', '/api/google-sheets/status')
}

/**
 * Push the active schedule to the department's real master Google Sheet
 * for its month, and to sked's per-physician calendar feed, in one call.
 * Refuses unless confirmationText is exactly "CONFIRM" — pass through
 * whatever the human typed, do not hardcode it here.
 * @param {string} confirmationText
 * @returns {{ sheet: {ok: boolean, detail: string}, sked: {ok: boolean, detail: string}, spreadsheet_url: string|null }}
 */
export async function pushToMasterSheet(confirmationText) {
  return request('POST', '/api/google-sheets/push-schedule', { confirmation_text: confirmationText })
}

/**
 * Read a month's master Google Sheet and make it the active schedule —
 * same edit/swap tools available afterward as any other load.
 * @param {number} year
 * @param {number} month  1-based
 * @returns {ScheduleResponse}
 */
export async function loadScheduleFromMasterSheet(year, month) {
  return request('GET', `/api/google-sheets/load-schedule?year=${year}&month=${month}`)
}

/**
 * Fetch fresh candidates for an unfilled slot.
 * Hard-violation physicians are excluded entirely; soft violations are returned
 * as warnings on each candidate.
 * @param {string} date       ISO date string e.g. "2026-06-01"
 * @param {string} shiftCode  e.g. "RAH_A_D1"
 * @returns {{ date: string, shift_code: string, candidates: CandidateSchema[] }}
 */
export async function getCandidates(date, shiftCode) {
  const params = new URLSearchParams({ date, shift_code: shiftCode })
  return request('GET', `/api/candidates?${params}`)
}

/**
 * Fetch candidates for a DOC or NOC on-call slot.
 * All physicians are returned; constraint violations appear as warnings.
 * @param {string} date      ISO date string e.g. "2026-06-01"
 * @param {string} callType  "DOC" or "NOC"
 */
export async function getOnCallCandidates(date, callType) {
  const params = new URLSearchParams({ date, call_type: callType })
  return request('GET', `/api/oncall-candidates?${params}`)
}

/**
 * Assign, change, or remove a physician from a DOC or NOC slot.
 * Pass an empty string for physicianId to remove the on-call assignment.
 * @param {string} date         ISO date string
 * @param {string} callType     "DOC" or "NOC"
 * @param {string} physicianId  physician_id, or "" to remove
 * @returns {ScheduleResponse}
 */
export async function assignOnCall(date, callType, physicianId) {
  return request('POST', '/api/assign-oncall', {
    date,
    call_type: callType,
    physician_id: physicianId,
  })
}

/**
 * Build (but never send) the ByteBloc shift-requests payload from the
 * current, currently-valid physician submissions. Read-only — safe to
 * call any time, including just to check whether ByteBloc is configured.
 * @returns {{ configured: boolean, group_code: string, location_code: string,
 *   requester_id: string, sked_start_date: string,
 *   by_physician: { physician_id: string, physician_name: string, count: number,
 *     need_off_count: number, available_count: number }[],
 *   warnings: string[], physician_count: number, request_count: number,
 *   need_off_count: number, available_count: number,
 *   used_delta: boolean, skipped_unchanged_count: number }}
 * @param {boolean} useDelta - only include cells changed since this backend
 *   instance's last successful send for this period (default true).
 */
export async function getByteBlocPreview(useDelta = true) {
  return request('GET', `/api/bytebloc/preview?use_delta=${useDelta ? 'true' : 'false'}`)
}

/**
 * Actually send the current ByteBloc shift-requests payload.
 *
 * This hits a live external system. The backend refuses the call unless
 * `confirmation` is exactly the string "CONFIRM" — pass through whatever
 * the human typed into the confirmation dialog verbatim; do not
 * hardcode "CONFIRM" here as a way to skip the prompt.
 * @param {string} confirmation
 * @param {boolean} useDelta - must match whatever was last previewed.
 * @returns {{ ok: boolean, status: string, raw: object|null }}
 */
export async function sendByteBlocRequests(confirmation, useDelta = true) {
  return request('POST', '/api/bytebloc/send', { confirmation, use_delta: useDelta })
}

/**
 * Every field of every physician in the roster (physicians.yaml) — for
 * the roster editor window.
 * @returns {{ physicians: Array }}
 */
export async function getPhysiciansFull() {
  return request('GET', '/api/physicians/full')
}

/**
 * Save edits to one existing physician back into physicians.yaml.
 * Editing only — physicianId must already exist in the roster.
 * @param {string} physicianId
 * @param {object} physician  Full PhysicianDetail-shaped object, including id.
 * @returns {object} The saved PhysicianDetail.
 */
export async function updatePhysician(physicianId, physician) {
  return request('PUT', `/api/physicians/${encodeURIComponent(physicianId)}`, physician)
}

/**
 * Add a new physician to the roster. id must be a single word of
 * letters/numbers not already in use; every other field starts at bare
 * defaults (edit further from the roster editor afterward).
 * @param {{ id: string, first_name: string, last_name: string }} data
 * @returns {object} The created PhysicianDetail.
 */
export async function createPhysician(data) {
  return request('POST', '/api/physicians', data)
}

/**
 * Permanently remove a physician from the roster. Requires the literal
 * confirmation string "REMOVE", typed by a human — pass it through
 * unchanged, never hardcode it here as a way to skip the prompt.
 * @param {string} physicianId
 * @param {string} confirmation
 * @returns {{ ok: boolean, status: string }}
 */
export async function removePhysician(physicianId, confirmation) {
  return request('POST', `/api/physicians/${encodeURIComponent(physicianId)}/remove`, { confirmation })
}

/**
 * Read-only: every person-specific pair/sequencing rule currently in
 * scheduler_config.yaml (timed_separation, conditional_cowork,
 * forbidden_precursor_shifts, night_chain_ramp_in, linked_rest_pairs),
 * rendered in plain language. Never modifies anything.
 * @returns {{ rules: { kind: string, physician_ids: string[], physician_names: string[], description: string }[] }}
 */
export async function getSequencingRules() {
  return request('GET', '/api/scheduling-rules/person-specific')
}


/**
 * Cross-month continuity: load the PREVIOUS month's finalized schedule (and,
 * optionally, that month's requests) so the next generate enforces rest /
 * consecutive-shift rules across the month boundary and applies the
 * month-to-month carry-over terms (acute debt, repeat overage). Does not
 * touch the active month.
 * @param {{ sheet_url?: string, file?: string, year?: number, month?: number,
 *           preferences_directory?: string, sked_period_id?: string }} body
 * @returns {TrailingScheduleStatus}
 */
export async function loadTrailingSchedule(body) {
  return request('POST', '/api/trailing-schedule', body)
}

/**
 * What previous-month schedule (if any) is loaded, and what the last
 * generate did with it (`last_used`).
 * @returns {TrailingScheduleStatus}
 */
export async function getTrailingSchedule() {
  return request('GET', '/api/trailing-schedule')
}

/** Forget the loaded previous month; the next generate runs without it. */
export async function clearTrailingSchedule() {
  return request('DELETE', '/api/trailing-schedule')
}
