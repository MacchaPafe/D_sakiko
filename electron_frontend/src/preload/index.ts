import { contextBridge, ipcRenderer } from 'electron'

const api = {
  resizeWindow: (deltaX: number, deltaY: number, direction: string) =>
    ipcRenderer.invoke('resize-window', { deltaX, deltaY, direction }),

  toggleDevTools: () =>
    ipcRenderer.invoke('toggle-devtools'),

  setIgnoreMouseEvents: (ignore: boolean, options?: { forward: boolean }) =>
    ipcRenderer.invoke('set-ignore-mouse-events', ignore, options),

  onWindowState: (listener: (state: {
    cursor: { x: number; y: number }
    bounds: { x: number; y: number; width: number; height: number }
  }) => void) => {
    const handler = (_event: Electron.IpcRendererEvent, state: Parameters<typeof listener>[0]) => listener(state)
    ipcRenderer.on('window-state', handler)
    ipcRenderer.send('window-state-ready')
    return () => ipcRenderer.removeListener('window-state', handler)
  },

  toggleAlwaysOnTop: () =>
    ipcRenderer.invoke('toggle-always-on-top'),

  getWindowState: () =>
    ipcRenderer.invoke('get-window-state') as Promise<{
      alwaysOnTop: boolean
      bounds: { x: number; y: number; width: number; height: number }
      visible: boolean
    } | null>,

  closeWindow: () =>
    ipcRenderer.invoke('close-window'),

  hideWindow: () =>
    ipcRenderer.invoke('hide-window'),

  getBridgeSession: () =>
    ipcRenderer.invoke('get-bridge-session') as Promise<{
      protocol: string
      token: string
      ws_url: string
      instance_id: string
    } | null>,
}

contextBridge.exposeInMainWorld('electronAPI', api)
