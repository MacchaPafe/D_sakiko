/// <reference types="vite/client" />

declare module '*.vue' {
  import type { DefineComponent } from 'vue'
  const component: DefineComponent<{}, {}, any>
  export default component
}

interface ElectronAPI {
  resizeWindow: (deltaX: number, deltaY: number, direction: string) => Promise<void>
  toggleDevTools: () => Promise<boolean>
  setIgnoreMouseEvents: (ignore: boolean, options?: { forward: boolean }) => Promise<void>
  toggleAlwaysOnTop: () => Promise<boolean>
  getWindowState: () => Promise<{
    alwaysOnTop: boolean
    bounds: { x: number; y: number; width: number; height: number }
    visible: boolean
  } | null>
  startDraggingWindow: () => Promise<void>
  onWindowState: (listener: (state: {
    cursor: { x: number; y: number }
    bounds: { x: number; y: number; width: number; height: number }
  }) => void) => () => void
  closeWindow: () => Promise<void>
  hideWindow: () => Promise<void>
  getBridgeSession: () => Promise<{
    protocol: string
    token: string
    ws_url: string
    instance_id: string
  } | null>
}

declare global {
  interface Window {
    electronAPI: ElectronAPI
  }
}

export {}
