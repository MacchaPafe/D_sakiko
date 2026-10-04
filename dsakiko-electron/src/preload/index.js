import { contextBridge, ipcRenderer } from 'electron'
import { RUNTIME_INFO_CHANNEL } from '../shared/contracts/runtime.js'

// 只暴露明确的能力，避免让页面取得任意 IPC 通道或 Node 权限。
contextBridge.exposeInMainWorld('dsakiko', {
  getRuntimeInfo: () => ipcRenderer.invoke(RUNTIME_INFO_CHANNEL)
})
