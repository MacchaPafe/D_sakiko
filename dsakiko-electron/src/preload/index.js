import { contextBridge, ipcRenderer } from 'electron'

// IPC 只提供可信 bootstrap 和本地宿主能力；业务命令及演出快照统一使用 HTTP/WS。
/** @type {import('../shared/contracts/api.js').DesktopHost} */
const host = {
  getBackendConnection: () => ipcRenderer.invoke('host:backend-connection'),
  getRuntimeInfo: () => ipcRenderer.invoke('host:runtime-info'),
  importFiles: () => ipcRenderer.invoke('host:import-files')
}
contextBridge.exposeInMainWorld('dsakikoHost', host)
