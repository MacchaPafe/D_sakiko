import { contextBridge, ipcRenderer } from 'electron'

// IPC 只提供可信 bootstrap 和本地宿主能力；业务命令及演出快照统一使用 HTTP/WS。
/** @type {import('../shared/contracts/api.js').DesktopHost} */
const host = {
  getBackendConnection: () => ipcRenderer.invoke('host:backend-connection'),
  getRuntimeInfo: () => ipcRenderer.invoke('host:runtime-info'),
  getUiPreferences: () => ipcRenderer.invoke('host:get-ui-preferences'),
  setUiPreference: (key, value) => ipcRenderer.invoke('host:set-ui-preference', key, value),
  getStorageOverview: () => ipcRenderer.invoke('host:storage-overview'),
  getCharacterStorageUsage: () => ipcRenderer.invoke('host:storage-character-usage'),
  previewStorageCleanup: () => ipcRenderer.invoke('host:preview-storage-cleanup'),
  cleanStorage: (ids) => ipcRenderer.invoke('host:clean-storage', ids),
  openStorageFolder: (id) => ipcRenderer.invoke('host:open-storage-folder', id),
  selectVertexAccount: () => ipcRenderer.invoke('host:select-vertex-account'),
  chatGPTAccount: (action, id) => ipcRenderer.invoke('host:chatgpt-account', action, id),
  openProviderKeyPage: (provider) => ipcRenderer.invoke('host:open-provider-key-page', provider),
  openWebSource: (href) => ipcRenderer.invoke('host:open-web-source', href),
  importAttachment: (input) => ipcRenderer.invoke('host:import-attachment', input),
  saveChatArchive: (asset) => ipcRenderer.invoke('host:save-chat-archive', asset),
  importFiles: (options) => ipcRenderer.invoke('host:import-files', options),
  selectLegacyDirectory: () => ipcRenderer.invoke('host:select-legacy-directory'),
  importOutfitDirectory: (target) => ipcRenderer.invoke('host:import-outfit-directory', target),
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
