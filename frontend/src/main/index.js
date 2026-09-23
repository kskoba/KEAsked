import { app, BrowserWindow, ipcMain, dialog, session, shell } from 'electron'
import { join } from 'path'
import { existsSync, readFileSync, writeFileSync } from 'fs'
import { spawn } from 'child_process'
import http from 'http'

// Simple dev-mode check — no external @electron-toolkit/utils dependency needed
const isDev = !app.isPackaged || process.env.NODE_ENV === 'development'

// This machine's Wayland/EGL stack crash-loops the GPU process (EGL_BAD_ALLOC),
// which eventually kills the whole app. Linux-only so packaged Win/Mac builds
// for end users are unaffected.
if (process.platform === 'linux') {
  app.disableHardwareAcceleration()
}

let mainWindow = null
let pythonProcess = null

// Project root is one level up from the frontend directory
const projectRoot = join(app.getAppPath(), '..')

// ---------------------------------------------------------------------------
// Physician config folder (physicians.yaml, scheduler_config.yaml,
// bytebloc.yaml) location
// ---------------------------------------------------------------------------
// This data is org-specific (real physician names/preferences, and for
// bytebloc.yaml a security token) and is never bundled into the app —
// it's gitignored in the repo and, deliberately, not copied into the
// packaged installer either (see package.json — extraResources no longer
// includes scheduler/config). The user points the app at wherever their
// org keeps this folder, and that choice is remembered across launches.

const SETTINGS_PATH = join(app.getPath('userData'), 'kea-settings.json')
const DEV_DEFAULT_CONFIG_DIR = join(projectRoot, 'scheduler', 'config')

function readAppSettings() {
  try {
    return JSON.parse(readFileSync(SETTINGS_PATH, 'utf-8'))
  } catch {
    return {}
  }
}

function writeAppSettings(patch) {
  const merged = { ...readAppSettings(), ...patch }
  writeFileSync(SETTINGS_PATH, JSON.stringify(merged, null, 2))
  return merged
}

function configDirLooksValid(dir) {
  return !!dir && existsSync(join(dir, 'physicians.yaml'))
}

// ---------------------------------------------------------------------------
// Backend location (local vs. a remote host, e.g. a Docker container on
// another machine — see Dockerfile at the repo root). The renderer talks to
// whichever one is configured; the local backend below is always spawned on
// startup regardless, so local dev and "no remote configured" keep working
// exactly as before — this setting only changes which URL the UI's own API
// calls target, not whether a local backend exists.
// ---------------------------------------------------------------------------

function getEffectiveBackendUrl() {
  const settings = readAppSettings()
  if (settings.backendMode === 'remote' && settings.remoteBackendUrl) {
    return settings.remoteBackendUrl.replace(/\/+$/, '')
  }
  return 'http://127.0.0.1:5000'
}

// Sets the Content-Security-Policy response header for the app's own
// top-level page load, computed from the actual configured backend URL —
// the static meta tag in index.html deliberately leaves connect-src
// unrestricted (connect-src *) since it can't know that URL ahead of time,
// and Chromium enforces the INTERSECTION of every CSP source, so this
// header is what actually narrows it down in practice.
function registerDynamicCsp() {
  session.defaultSession.webRequest.onHeadersReceived((details, callback) => {
    if (details.resourceType !== 'mainFrame') {
      callback({})
      return
    }
    const backendUrl = getEffectiveBackendUrl()
    const policy = [
      "default-src 'self'",
      "script-src 'self' 'unsafe-inline' 'unsafe-eval'",
      "style-src 'self' 'unsafe-inline'",
      `connect-src 'self' ${backendUrl} http://localhost:5173 ws://localhost:5173`
    ].join('; ')
    callback({
      responseHeaders: {
        ...details.responseHeaders,
        'Content-Security-Policy': [policy]
      }
    })
  })
}

// Resolves the config folder to use, prompting the user to pick one (and
// remembering the choice) if none is set yet or the saved one no longer
// has physicians.yaml in it. Returns null if there's no valid folder —
// the app still starts in that case (see app.whenReady below); it just
// can't import or generate anything until one is set, which the renderer
// surfaces as a banner rather than this ever blocking startup.
async function resolveConfigDir() {
  const settings = readAppSettings()
  if (configDirLooksValid(settings.configDir)) return settings.configDir

  // In dev, fall back to the repo's own scheduler/config without prompting
  // — that's the normal working copy every dev checkout already has.
  if (!app.isPackaged && configDirLooksValid(DEV_DEFAULT_CONFIG_DIR)) {
    return DEV_DEFAULT_CONFIG_DIR
  }

  return promptForConfigDir()
}

async function promptForConfigDir(retry = false) {
  const { response: introResponse } = await dialog.showMessageBox({
    type: 'info',
    title: 'Physician Config Folder',
    message: retry
      ? "That folder doesn't contain physicians.yaml. Please choose the folder again, or skip for now."
      : 'Select the folder containing physicians.yaml, scheduler_config.yaml, and (optionally) bytebloc.yaml.',
    detail: 'This is your organization\'s own data — it is not bundled with the app. ' +
      'You can set or change this later from Settings; the app will still open without it, ' +
      'you just won\'t be able to import or generate a schedule until it\'s set.',
    buttons: ['Choose Folder', 'Skip for Now'],
    defaultId: 0,
    cancelId: 1
  })
  if (introResponse === 1) return null

  const result = await dialog.showOpenDialog({
    properties: ['openDirectory'],
    title: 'Select Physician Config Folder'
  })

  if (result.canceled || result.filePaths.length === 0) {
    return null
  }

  const dir = result.filePaths[0]
  if (!configDirLooksValid(dir)) {
    return promptForConfigDir(true)
  }

  writeAppSettings({ configDir: dir })
  return dir
}

function killPortWindows(port) {
  try {
    // Find and kill any process already listening on the port
    const { execSync } = require('child_process')
    const out = execSync(`netstat -ano 2>nul | findstr LISTENING | findstr :${port}`, { encoding: 'utf8' })
    const pids = [...new Set(out.split('\n')
      .map(l => l.trim().split(/\s+/).pop())
      .filter(p => p && /^\d+$/.test(p) && p !== '0'))]
    pids.forEach(pid => {
      try { execSync(`taskkill /F /PID ${pid} 2>nul`) } catch {}
    })
  } catch {
    // No process on that port — nothing to do
  }
}

function startPythonServer(configDir) {
  if (process.platform === 'win32') killPortWindows(5000)

  let spawnCmd, spawnArgs, spawnOpts
  // Only set CONFIG_DIR when we actually have one — configDir can be null
  // (no physician config folder chosen/found yet), and passing that
  // through as an env value isn't meaningful; better to let the backend's
  // own fallback resolution run and just not find physicians.yaml, which
  // the renderer already surfaces as a banner rather than relying on this
  // to fail loudly.
  const env = { ...process.env }
  if (configDir) env.CONFIG_DIR = configDir

  if (app.isPackaged) {
    // Packaged app — launch the bundled PyInstaller executable
    const exeName = process.platform === 'win32' ? 'scheduler_server.exe' : 'scheduler_server'
    const exePath = join(process.resourcesPath, 'backend', exeName)
    console.log('[main] Starting bundled server:', exePath)
    spawnCmd = exePath
    spawnArgs = []
    spawnOpts = {
      env,
      stdio: ['ignore', 'pipe', 'pipe']
    }
  } else {
    // Development — run via python module
    console.log('[main] Starting Python API server (dev) from:', projectRoot)
    const pythonCmd = process.platform === 'win32' ? 'python' : 'python3'
    spawnCmd = pythonCmd
    spawnArgs = ['-m', 'scheduler.api.server']
    spawnOpts = {
      cwd: projectRoot,
      env,
      stdio: ['ignore', 'pipe', 'pipe'],
      shell: process.platform === 'win32'
    }
  }

  pythonProcess = spawn(spawnCmd, spawnArgs, spawnOpts)

  pythonProcess.stdout.on('data', (data) => {
    console.log('[python]', data.toString().trim())
  })

  pythonProcess.stderr.on('data', (data) => {
    console.error('[python err]', data.toString().trim())
  })

  pythonProcess.on('error', (err) => {
    console.error('[main] Failed to start Python server:', err)
  })

  pythonProcess.on('exit', (code) => {
    console.log('[main] Python server exited with code:', code)
    pythonProcess = null
  })
}

function stopPythonServer() {
  if (pythonProcess) {
    console.log('[main] Stopping Python server...')
    if (process.platform === 'win32') {
      spawn('taskkill', ['/pid', String(pythonProcess.pid), '/f', '/t'])
    } else {
      pythonProcess.kill('SIGTERM')
    }
    pythonProcess = null
  }
}

// Shared restart path for both the config-folder and backend-location
// settings changes below. app.exit() (unlike app.quit()) skips the
// before-quit event entirely, so stopPythonServer() must be called
// explicitly here or the old local backend is orphaned holding port 5000.
// app.relaunch() is also unreliable under electron-vite's dev-mode process
// supervision (it owns the electron child process and doesn't expect it to
// re-spawn itself) — only use it in a packaged build; in dev, quit cleanly
// and let the developer re-run `npm run dev`.
function restartApp() {
  stopPythonServer()
  if (app.isPackaged) {
    app.relaunch()
    app.exit(0)
  } else {
    app.quit()
  }
}

function pollServerReady(url, intervalMs, timeoutMs) {
  return new Promise((resolve, reject) => {
    const start = Date.now()

    function check() {
      const req = http.get(url, (res) => {
        if (res.statusCode === 200) {
          resolve(true)
        } else {
          retry()
        }
      })
      req.on('error', retry)
      req.setTimeout(400, () => { req.destroy(); retry() })
    }

    function retry() {
      if (Date.now() - start > timeoutMs) {
        reject(new Error('Python API server did not become ready within timeout'))
        return
      }
      setTimeout(check, intervalMs)
    }

    check()
  })
}

function createLoadingWindow() {
  const win = new BrowserWindow({
    width: 480,
    height: 300,
    frame: false,
    resizable: false,
    center: true,
    backgroundColor: '#1e293b',
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true
    }
  })

  win.loadURL('data:text/html,' + encodeURIComponent(`
    <!DOCTYPE html>
    <html>
    <head>
      <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
          background: #1e293b;
          color: #f1f5f9;
          font-family: system-ui, -apple-system, sans-serif;
          display: flex;
          flex-direction: column;
          align-items: center;
          justify-content: center;
          height: 100vh;
          gap: 24px;
        }
        h1 { font-size: 22px; font-weight: 700; color: #38bdf8; }
        p { font-size: 14px; color: #94a3b8; }
        .spinner {
          width: 40px; height: 40px;
          border: 4px solid #334155;
          border-top-color: #38bdf8;
          border-radius: 50%;
          animation: spin 0.8s linear infinite;
        }
        @keyframes spin { to { transform: rotate(360deg); } }
      </style>
    </head>
    <body>
      <div class="spinner"></div>
      <h1>KEA Physician Scheduler</h1>
      <p>Starting API server, please wait...</p>
    </body>
    </html>
  `))

  return win
}

async function createMainWindow() {
  mainWindow = new BrowserWindow({
    width: 1400,
    height: 900,
    show: false,
    backgroundColor: '#f8fafc',
    webPreferences: {
      preload: join(__dirname, '../preload/index.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false
    }
  })

  mainWindow.on('closed', () => {
    mainWindow = null
  })

  if (isDev && process.env['ELECTRON_RENDERER_URL']) {
    mainWindow.loadURL(process.env['ELECTRON_RENDERER_URL'])
    mainWindow.webContents.openDevTools()
  } else {
    mainWindow.loadFile(join(__dirname, '../renderer/index.html'))
  }

  return mainWindow
}

// Physician roster editor — a second, independent window so it can stay
// open side-by-side with the main scheduler window. It's just another
// view of the same renderer bundle (picked via the "#roster" URL hash in
// main.jsx) talking to the same backend on :5000, so no window-to-window
// IPC is needed.
let rosterWindow = null

async function createRosterWindow() {
  if (rosterWindow && !rosterWindow.isDestroyed()) {
    rosterWindow.focus()
    return rosterWindow
  }

  rosterWindow = new BrowserWindow({
    width: 1000,
    height: 800,
    backgroundColor: '#f8fafc',
    webPreferences: {
      preload: join(__dirname, '../preload/index.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false
    }
  })

  rosterWindow.on('closed', () => {
    rosterWindow = null
  })

  if (isDev && process.env['ELECTRON_RENDERER_URL']) {
    rosterWindow.loadURL(`${process.env['ELECTRON_RENDERER_URL']}#roster`)
  } else {
    rosterWindow.loadFile(join(__dirname, '../renderer/index.html'), { hash: 'roster' })
  }

  return rosterWindow
}

// Same pattern as rosterWindow above — a separate window/renderer showing
// each physician's individual monthly schedule at a glance, for the person
// overseeing the schedule to sanity-check before it goes out.
let scheduleViewerWindow = null

async function createScheduleViewerWindow() {
  if (scheduleViewerWindow && !scheduleViewerWindow.isDestroyed()) {
    scheduleViewerWindow.focus()
    return scheduleViewerWindow
  }

  scheduleViewerWindow = new BrowserWindow({
    width: 1000,
    height: 800,
    backgroundColor: '#f8fafc',
    webPreferences: {
      preload: join(__dirname, '../preload/index.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false
    }
  })

  scheduleViewerWindow.on('closed', () => {
    scheduleViewerWindow = null
  })

  if (isDev && process.env['ELECTRON_RENDERER_URL']) {
    scheduleViewerWindow.loadURL(`${process.env['ELECTRON_RENDERER_URL']}#schedule-viewer`)
  } else {
    scheduleViewerWindow.loadFile(join(__dirname, '../renderer/index.html'), { hash: 'schedule-viewer' })
  }

  return scheduleViewerWindow
}

// Same pattern as rosterWindow/scheduleViewerWindow above — a separate
// window for pushing the roster + a period's master schedule template to
// sked and emailing out each physician's signed magic link.
let monthlyRequestsWindow = null

async function createMonthlyRequestsWindow() {
  if (monthlyRequestsWindow && !monthlyRequestsWindow.isDestroyed()) {
    monthlyRequestsWindow.focus()
    return monthlyRequestsWindow
  }

  monthlyRequestsWindow = new BrowserWindow({
    width: 900,
    height: 750,
    backgroundColor: '#f8fafc',
    webPreferences: {
      preload: join(__dirname, '../preload/index.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false
    }
  })

  monthlyRequestsWindow.on('closed', () => {
    monthlyRequestsWindow = null
  })

  if (isDev && process.env['ELECTRON_RENDERER_URL']) {
    monthlyRequestsWindow.loadURL(`${process.env['ELECTRON_RENDERER_URL']}#monthly-requests`)
  } else {
    monthlyRequestsWindow.loadFile(join(__dirname, '../renderer/index.html'), { hash: 'monthly-requests' })
  }

  return monthlyRequestsWindow
}

// Same pattern again — a window for tracking who has/hasn't completed the
// annual preference survey. Viewer only; encoding responses into solver
// rules is a manual/Claude-assisted step outside this app.
let surveyResponsesWindow = null

async function createSurveyResponsesWindow() {
  if (surveyResponsesWindow && !surveyResponsesWindow.isDestroyed()) {
    surveyResponsesWindow.focus()
    return surveyResponsesWindow
  }

  surveyResponsesWindow = new BrowserWindow({
    width: 950,
    height: 750,
    backgroundColor: '#f8fafc',
    webPreferences: {
      preload: join(__dirname, '../preload/index.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false
    }
  })

  surveyResponsesWindow.on('closed', () => {
    surveyResponsesWindow = null
  })

  if (isDev && process.env['ELECTRON_RENDERER_URL']) {
    surveyResponsesWindow.loadURL(`${process.env['ELECTRON_RENDERER_URL']}#survey-responses`)
  } else {
    surveyResponsesWindow.loadFile(join(__dirname, '../renderer/index.html'), { hash: 'survey-responses' })
  }

  return surveyResponsesWindow
}

// Same pattern again — preview and (on explicit human confirmation, typed
// in this window, not pre-filled) send physician unavailability to ByteBloc
// as Off/NeedOff requests. See scheduler/backend/bytebloc.py's hard safety
// rule: nothing is sent without that confirmation.
let byteBlocWindow = null

async function createByteBlocWindow() {
  if (byteBlocWindow && !byteBlocWindow.isDestroyed()) {
    byteBlocWindow.focus()
    return byteBlocWindow
  }

  byteBlocWindow = new BrowserWindow({
    width: 900,
    height: 750,
    backgroundColor: '#f8fafc',
    webPreferences: {
      preload: join(__dirname, '../preload/index.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false
    }
  })

  byteBlocWindow.on('closed', () => {
    byteBlocWindow = null
  })

  if (isDev && process.env['ELECTRON_RENDERER_URL']) {
    byteBlocWindow.loadURL(`${process.env['ELECTRON_RENDERER_URL']}#bytebloc`)
  } else {
    byteBlocWindow.loadFile(join(__dirname, '../renderer/index.html'), { hash: 'bytebloc' })
  }

  return byteBlocWindow
}

// IPC: open native directory picker
ipcMain.handle('dialog:openDirectory', async () => {
  const result = await dialog.showOpenDialog(mainWindow, {
    properties: ['openDirectory'],
    title: 'Select Submissions Directory'
  })
  if (result.canceled || result.filePaths.length === 0) return null
  return result.filePaths[0]
})

// IPC: open native file picker
ipcMain.handle('dialog:openFile', async (_event, filters) => {
  const result = await dialog.showOpenDialog(mainWindow, {
    properties: ['openFile'],
    title: 'Select Preferences File',
    filters: filters || [{ name: 'Excel Files', extensions: ['xlsx', 'xls'] }]
  })
  if (result.canceled || result.filePaths.length === 0) return null
  return result.filePaths[0]
})

// IPC: open a URL in the system's default browser -- used for e.g. opening
// a physician's sked magic link from the Roster Editor. Restricted to
// http(s) so a renderer bug or bad backend response can never make this
// hand a file:// or custom-protocol string to the OS.
ipcMain.handle('shell:openExternal', (_event, url) => {
  if (typeof url !== 'string' || !/^https?:\/\//i.test(url)) {
    throw new Error(`Refusing to open non-http(s) URL: ${url}`)
  }
  return shell.openExternal(url)
})

// IPC: open (or focus) the physician roster editor window
ipcMain.handle('window:openRoster', () => {
  createRosterWindow()
})

// IPC: open (or focus) the per-physician monthly schedule viewer window
ipcMain.handle('window:openScheduleViewer', () => {
  createScheduleViewerWindow()
})

// IPC: open (or focus) the "Send Monthly Shift Requests" window
ipcMain.handle('window:openMonthlyRequests', () => {
  createMonthlyRequestsWindow()
})

// IPC: open (or focus) the "Survey Responses" window
ipcMain.handle('window:openSurveyResponses', () => {
  createSurveyResponsesWindow()
})

// IPC: open (or focus) the ByteBloc window
ipcMain.handle('window:openByteBloc', () => {
  createByteBlocWindow()
})

// IPC: force-close whichever window sent this, bypassing its own
// beforeunload/close negotiation. Used once a window's own renderer code
// has already confirmed (via window.confirm) that closing is fine —
// calling window.close() again from deep inside that confirm's callback
// is unreliable (it's far enough removed from the original click's user
// activation that Chromium silently ignores it), so the renderer asks
// the main process to just tear the window down directly instead.
ipcMain.on('window:forceClose', (event) => {
  BrowserWindow.fromWebContents(event.sender)?.destroy()
})

// IPC: current physician config folder (Settings screen)
ipcMain.handle('settings:getConfigDir', () => {
  const settings = readAppSettings()
  if (configDirLooksValid(settings.configDir)) return settings.configDir
  if (!app.isPackaged && configDirLooksValid(DEV_DEFAULT_CONFIG_DIR)) return DEV_DEFAULT_CONFIG_DIR
  return null
})

// IPC: let the user change the physician config folder from Settings.
// Takes effect after a restart, which this offers to do immediately.
ipcMain.handle('settings:chooseConfigDir', async () => {
  const result = await dialog.showOpenDialog(mainWindow, {
    properties: ['openDirectory'],
    title: 'Select Physician Config Folder'
  })
  if (result.canceled || result.filePaths.length === 0) return { changed: false }

  const dir = result.filePaths[0]
  if (!configDirLooksValid(dir)) {
    return { changed: false, error: 'That folder does not contain physicians.yaml.' }
  }

  writeAppSettings({ configDir: dir })

  const { response } = await dialog.showMessageBox(mainWindow, {
    type: 'question',
    buttons: ['Restart Now', 'Later'],
    defaultId: 0,
    message: 'Config folder updated.',
    detail: app.isPackaged
      ? 'KEA Physician Scheduler needs to restart to load data from the new location.'
      : 'The app will quit to load data from the new location — re-run "npm run dev" to start it again.'
  })
  if (response === 0) {
    restartApp()
  }
  return { changed: true, path: dir }
})

// IPC: current backend location (Settings screen)
ipcMain.handle('settings:getBackendConfig', () => {
  const settings = readAppSettings()
  return {
    mode: settings.backendMode === 'remote' ? 'remote' : 'local',
    remoteUrl: settings.remoteBackendUrl || '',
    effectiveUrl: getEffectiveBackendUrl()
  }
})

// IPC: let the user switch between the local backend and a remote one
// (e.g. a Docker container on another machine) from Settings. Takes effect
// after a restart, which this offers to do immediately — same pattern as
// settings:chooseConfigDir above. The local backend still always spawns on
// startup either way; this only changes which URL the renderer's own API
// calls target.
ipcMain.handle('settings:setBackendConfig', async (_event, { mode, remoteUrl }) => {
  if (mode === 'remote') {
    const trimmed = (remoteUrl || '').trim()
    if (!/^https?:\/\/.+/.test(trimmed)) {
      return { changed: false, error: 'Enter a full URL, e.g. http://192.168.0.5:5000' }
    }
    writeAppSettings({ backendMode: 'remote', remoteBackendUrl: trimmed.replace(/\/+$/, '') })
  } else {
    writeAppSettings({ backendMode: 'local' })
  }

  const { response } = await dialog.showMessageBox(mainWindow, {
    type: 'question',
    buttons: ['Restart Now', 'Later'],
    defaultId: 0,
    message: 'Backend location updated.',
    detail: app.isPackaged
      ? 'KEA Physician Scheduler needs to restart to connect to the new backend.'
      : 'The app will quit to connect to the new backend — re-run "npm run dev" to start it again.'
  })
  if (response === 0) {
    restartApp()
  }
  return { changed: true }
})

app.whenReady().then(async () => {
  registerDynamicCsp()
  const loadingWin = createLoadingWindow()

  // configDir may be null (no physicians.yaml found/chosen yet) — the app
  // still starts either way; the renderer shows a banner and disables
  // import/generate until a valid folder is set from Settings.
  const configDir = await resolveConfigDir()

  // Start Python backend
  startPythonServer(configDir)

  try {
    await pollServerReady('http://127.0.0.1:5000/api/health', 500, 30000)
    console.log('[main] Python API server is ready')
  } catch (err) {
    console.warn('[main] API server not ready:', err.message)
    // Continue anyway — user may have server running separately
  }

  const win = await createMainWindow()

  let revealed = false
  const reveal = () => {
    if (revealed) return
    revealed = true
    clearTimeout(fallbackTimer)
    if (!loadingWin.isDestroyed()) loadingWin.close()
    win.show()
    win.focus()
  }

  win.once('ready-to-show', reveal)

  // If renderer loads before ready-to-show fires, show anyway
  const fallbackTimer = setTimeout(reveal, 5000)
})

app.on('window-all-closed', () => {
  stopPythonServer()
  if (process.platform !== 'darwin') {
    app.quit()
  }
})

app.on('activate', () => {
  if (BrowserWindow.getAllWindows().length === 0) {
    createMainWindow()
  }
})

app.on('before-quit', () => {
  stopPythonServer()
})
