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
  startDraggingWindow: () => Promise<void>
  onWindowState: (listener: (state: {
    cursor: { x: number; y: number }
    bounds: { x: number; y: number; width: number; height: number }
  }) => void) => () => void
  closeWindow: () => Promise<void>
}

declare global {
  interface Window {
    electronAPI: ElectronAPI
  }
}

export {}
