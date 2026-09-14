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
    throw new Error(errorMsg)
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
 *   items: { physician_id: string, physician_name: string, day: string, shift_code: string }[],
 *   warnings: string[], physician_count: number, request_count: number }}
 */
export async function getByteBlocPreview() {
  return request('GET', '/api/bytebloc/preview')
}

/**
 * Actually send the current ByteBloc shift-requests payload.
 *
 * This hits a live external system. The backend refuses the call unless
 * `confirmation` is exactly the string "CONFIRM" — pass through whatever
 * the human typed into the confirmation dialog verbatim; do not
 * hardcode "CONFIRM" here as a way to skip the prompt.
 * @param {string} confirmation
 * @returns {{ ok: boolean, status: string, raw: object|null }}
 */
export async function sendByteBlocRequests(confirmation) {
  return request('POST', '/api/bytebloc/send', { confirmation })
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

