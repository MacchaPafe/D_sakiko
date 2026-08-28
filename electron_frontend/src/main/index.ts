import { app, BrowserWindow, ipcMain, screen } from 'electron'
import { join } from 'node:path'

// GPU 开关
app.commandLine.appendSwitch('enable-webgl')
app.commandLine.appendSwitch('ignore-gpu-blacklist')

let mainWindow: BrowserWindow | null = null
let windowStateTimer: ReturnType<typeof setInterval> | null = null
let lastWindowState = ''
let ipcHandlersRegistered = false

function publishWindowState(force = false) {
  if (!mainWindow || mainWindow.isDestroyed() || mainWindow.webContents.isDestroyed()) return
  const bounds = mainWindow.getBounds()
  const cursor = screen.getCursorScreenPoint()
  const state = {
    cursor: { x: cursor.x, y: cursor.y },
    bounds: { x: bounds.x, y: bounds.y, width: bounds.width, height: bounds.height },
  }
  const serialized = JSON.stringify(state)
  if (!force && serialized === lastWindowState) return
  lastWindowState = serialized
  mainWindow.webContents.send('window-state', state)
}

function createWindow() {
  mainWindow = new BrowserWindow({
    title: 'Saki',
    width: 450,
    height: 600,
    show: false,
    frame: false,
    transparent: true,
    hasShadow: false,
    type: 'panel' as const,
    webPreferences: {
      preload: join(__dirname, '../preload/index.js'),
      sandbox: false,
      webgl: true,
    },
  })

  mainWindow.setAlwaysOnTop(true, 'screen-saver', 1)
  mainWindow.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true })
  mainWindow.setFullScreenable(false)
  mainWindow.on('ready-to-show', () => mainWindow!.show())
  mainWindow.on('ready-to-show', publishWindowState)
  mainWindow.on('move', publishWindowState)
  mainWindow.on('resize', publishWindowState)
  mainWindow.on('closed', () => {
    if (windowStateTimer) clearInterval(windowStateTimer)
    windowStateTimer = null
    mainWindow = null
    lastWindowState = ''
  })

  let allowClose = false
  mainWindow.on('close', (event) => {
    if (allowClose) return
    event.preventDefault()
    mainWindow?.hide()
  })

  const viteDevServerUrl = process.env.ELECTRON_RENDERER_URL
  if (viteDevServerUrl) {
    mainWindow.loadURL(viteDevServerUrl)
    mainWindow.webContents.openDevTools({ mode: 'detach' })
  } else {
    mainWindow.loadFile(join(__dirname, '../renderer/index.html'))
  }

  if (ipcHandlersRegistered) {
    windowStateTimer = setInterval(publishWindowState, 16)
    return
  }
  ipcHandlersRegistered = true

  ipcMain.on('window-state-ready', () => publishWindowState(true))

  // Cursor coordinates are only available in the main process while the
  // transparent window is click-through. Publish one shared snapshot so the
  // renderer never performs hover-time IPC round trips.
  windowStateTimer = setInterval(publishWindowState, 16)

  // Resize
  ipcMain.handle('resize-window', (_e, { deltaX, deltaY, direction }) => {
    if (!mainWindow) return
    const bounds = mainWindow.getBounds()
    const minW = 200, minH = 250
    let { x, y, width, height } = bounds
    if (direction.includes('e')) width = Math.max(minW, width + deltaX)
    if (direction.includes('w')) { const nw = Math.max(minW, width - deltaX); x += width - nw; width = nw }
    if (direction.includes('s')) height = Math.max(minH, height + deltaY)
    if (direction.includes('n')) { const nh = Math.max(minH, height - deltaY); y += height - nh; height = nh }
    mainWindow.setBounds({ x, y, width, height })
  })

  // DevTools
  ipcMain.handle('toggle-devtools', () => {
    if (!mainWindow) return false
    if (mainWindow.webContents.isDevToolsOpened()) { mainWindow.webContents.closeDevTools(); return false }
    mainWindow.webContents.openDevTools({ mode: 'detach' }); return true
  })

  // 鼠标穿透
  ipcMain.handle('set-ignore-mouse-events', (_e, ignore, options) => {
    mainWindow?.setIgnoreMouseEvents(ignore, options)
  })

  // 置顶
  ipcMain.handle('toggle-always-on-top', () => {
    if (!mainWindow) return false
    const cur = mainWindow.isAlwaysOnTop()
    mainWindow.setAlwaysOnTop(!cur, 'screen-saver', 1)
    return !cur
  })

  ipcMain.handle('close-window', () => {
    mainWindow?.hide()
  })
}

app.whenReady().then(createWindow)
app.on('window-all-closed', () => { if (process.platform !== 'darwin') app.quit() })
app.on('activate', () => { if (BrowserWindow.getAllWindows().length === 0) createWindow() })
