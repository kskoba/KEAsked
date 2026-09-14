import { contextBridge, ipcRenderer } from 'electron'

contextBridge.exposeInMainWorld('electronAPI', {
  openDirectory: () => ipcRenderer.invoke('dialog:openDirectory'),
  openFile: (filters) => ipcRenderer.invoke('dialog:openFile', filters),
  getConfigDir: () => ipcRenderer.invoke('settings:getConfigDir'),
  chooseConfigDir: () => ipcRenderer.invoke('settings:chooseConfigDir'),
  getBackendConfig: () => ipcRenderer.invoke('settings:getBackendConfig'),
  setBackendConfig: (cfg) => ipcRenderer.invoke('settings:setBackendConfig', cfg),
  openRosterWindow: () => ipcRenderer.invoke('window:openRoster'),
  openScheduleViewerWindow: () => ipcRenderer.invoke('window:openScheduleViewer'),
  forceCloseSelf: () => ipcRenderer.send('window:forceClose'),
  platform: process.platform
})
