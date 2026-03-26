const BASE_URL = 'http://127.0.0.1:5000'

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

