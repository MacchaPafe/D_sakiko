import { app, BrowserWindow } from 'electron'
import { fileURLToPath } from 'node:url'
import { createBackend } from '../backend/create-backend.js'
import { registerRuntimeIpc } from './ipc/runtime.js'
import { createMainWindow } from './windows/main-window.js'

let mainWindow = null

async function openMainWindow() {
  mainWindow = createMainWindow()
  if (!app.isPackaged && process.env.ELECTRON_RENDERER_URL) {
    await mainWindow.loadURL(process.env.ELECTRON_RENDERER_URL)
    return
  }
  await mainWindow.loadFile(fileURLToPath(new URL('../renderer/index.html', import.meta.url)))
}

function handleStartupError(error) {
  console.error('应用启动失败：', error)
  app.quit()
}

async function startApplication() {
  app.setName('数字小祥')
  app.setAppUserModelId('io.dsakiko.desktop')
  const backend = createBackend()
  const unregisterIpc = registerRuntimeIpc(backend, () => mainWindow)
  app.once('will-quit', unregisterIpc)
  await openMainWindow()

  app.on('activate', function handleActivate() {
    if (BrowserWindow.getAllWindows().length === 0) {
      void openMainWindow().catch(handleStartupError)
    }
  })
}

app.whenReady().then(startApplication).catch(handleStartupError)

app.on('window-all-closed', function handleAllWindowsClosed() {
  // macOS 关闭窗口后保留菜单栏和 Dock 入口，允许再次打开窗口。
  if (process.platform !== 'darwin') app.quit()
})
