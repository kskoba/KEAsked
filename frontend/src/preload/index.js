import { contextBridge, ipcRenderer } from 'electron'

contextBridge.exposeInMainWorld('electronAPI', {
  openDirectory: () => ipcRenderer.invoke('dialog:openDirectory'),
  openFile: (filters) => ipcRenderer.invoke('dialog:openFile', filters),
  getConfigDir: () => ipcRenderer.invoke('settings:getConfigDir'),
  chooseConfigDir: () => ipcRenderer.invoke('settings:chooseConfigDir'),
  platform: process.platform
})
