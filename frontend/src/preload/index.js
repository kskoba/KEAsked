import { contextBridge, ipcRenderer } from 'electron'

contextBridge.exposeInMainWorld('electronAPI', {
  openDirectory: () => ipcRenderer.invoke('dialog:openDirectory'),
  openFile: (filters) => ipcRenderer.invoke('dialog:openFile', filters),
  getConfigDir: () => ipcRenderer.invoke('settings:getConfigDir'),
  chooseConfigDir: () => ipcRenderer.invoke('settings:chooseConfigDir'),
  openRosterWindow: () => ipcRenderer.invoke('window:openRoster'),
  forceCloseSelf: () => ipcRenderer.send('window:forceClose'),
  platform: process.platform
})
