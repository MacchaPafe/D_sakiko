import { contextBridge, ipcRenderer } from 'electron'
import {
  COMMANDS,
  DESKTOP_EVENT,
  PERFORMANCE_COMMAND,
  PERFORMANCE_REPLY,
  PERFORMANCE_UPDATE
} from '../shared/contracts/desktop.js'

function subscribe(channel, callback) {
  function receive(_event, value) {
    callback(value)
  }
  ipcRenderer.on(channel, receive)
  return () => ipcRenderer.removeListener(channel, receive)
}
const bridge = {
  getRuntimeInfo: () => ipcRenderer.invoke('runtime:get-info'),
  onUpdate: (callback) => subscribe(DESKTOP_EVENT, callback),
  onPerformanceCommand: (callback) => subscribe(PERFORMANCE_COMMAND, callback),
  replyPerformance: (reply) => ipcRenderer.send(PERFORMANCE_REPLY, reply),
  updatePerformance: (update) => ipcRenderer.send(PERFORMANCE_UPDATE, update)
}
for (const name of COMMANDS) bridge[name] = (...args) => ipcRenderer.invoke(`desktop:${name}`, args)
contextBridge.exposeInMainWorld('dsakiko', bridge)
