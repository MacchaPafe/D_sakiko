import { contextBridge, ipcRenderer } from 'electron'

// IPC 只提供可信 bootstrap 和本地宿主能力；业务命令及演出快照统一使用 HTTP/WS。
/** @type {import('../shared/contracts/api.js').DesktopHost} */
const host = {
  getBackendConnection: () => ipcRenderer.invoke('host:backend-connection'),
  getRuntimeInfo: () => ipcRenderer.invoke('host:runtime-info'),
  importFiles: (options) => ipcRenderer.invoke('host:import-files', options),
  selectLegacyDirectory: () => ipcRenderer.invoke('host:select-legacy-directory'),
  getWindowCloseBehavior: () => ipcRenderer.invoke('host:get-window-close-behavior'),
  setWindowCloseBehavior: (behavior) =>
    ipcRenderer.invoke('host:set-window-close-behavior', behavior),
  getWindowVisibility: () => ipcRenderer.invoke('host:get-window-visibility'),
  onWindowVisibility(listener) {
    const receive = (_event, visible) => listener(visible)
    ipcRenderer.on('host:window-visibility', receive)
    return () => ipcRenderer.removeListener('host:window-visibility', receive)
  }
}
contextBridge.exposeInMainWorld('dsakikoHost', host)
