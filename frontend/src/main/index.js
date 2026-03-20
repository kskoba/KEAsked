import { app, BrowserWindow, ipcMain, dialog } from 'electron'
import { join } from 'path'
import { spawn } from 'child_process'
import http from 'http'

// Simple dev-mode check — no external @electron-toolkit/utils dependency needed
const isDev = !app.isPackaged || process.env.NODE_ENV === 'development'

let mainWindow = null
let pythonProcess = null

// Project root is one level up from the frontend directory
const projectRoot = join(app.getAppPath(), '..')

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

function startPythonServer() {
  if (process.platform === 'win32') killPortWindows(5000)

  let spawnCmd, spawnArgs, spawnOpts

  if (app.isPackaged) {
    // Packaged app — launch the bundled PyInstaller executable
    const exeName = process.platform === 'win32' ? 'scheduler_server.exe' : 'scheduler_server'
    const exePath = join(process.resourcesPath, 'backend', exeName)
    const configDir = join(process.resourcesPath, 'config')
    console.log('[main] Starting bundled server:', exePath)
    spawnCmd = exePath
    spawnArgs = []
    spawnOpts = {
      env: { ...process.env, CONFIG_DIR: configDir },
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
      <p>Starting API server, please wait…</p>
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

app.whenReady().then(async () => {
  const loadingWin = createLoadingWindow()

  // Start Python backend
  startPythonServer()

  try {
    await pollServerReady('http://127.0.0.1:5000/api/health', 500, 30000)
    console.log('[main] Python API server is ready')
  } catch (err) {
    console.warn('[main] API server not ready:', err.message)
    // Continue anyway — user may have server running separately
  }

  const win = await createMainWindow()

  win.once('ready-to-show', () => {
    loadingWin.close()
    win.show()
    win.focus()
  })

  // If renderer loads before ready-to-show fires, show anyway
  setTimeout(() => {
    if (!win.isVisible()) {
      loadingWin.close()
      win.show()
    }
  }, 5000)
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
