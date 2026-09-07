import { app, BrowserWindow, ipcMain, Menu, screen, Tray, type MenuItemConstructorOptions } from 'electron'
import { join, resolve } from 'node:path'
import { readFileSync, writeFileSync } from 'node:fs'

// GPU 开关
app.commandLine.appendSwitch('enable-webgl')
app.commandLine.appendSwitch('ignore-gpu-blacklist')

let mainWindow: BrowserWindow | null = null
let appTray: Tray | null = null
let windowStateTimer: ReturnType<typeof setInterval> | null = null
let lastWindowState = ''
let ipcHandlersRegistered = false
const hasSingleInstanceLock = app.requestSingleInstanceLock()

interface BridgeSession {
  protocol: string
  token: string
  ws_url: string
  instance_id: string
}

interface WindowPreferences {
  x: number
  y: number
  width: number
  height: number
  alwaysOnTop: boolean
}

const defaultWindowPreferences: WindowPreferences = {
  x: 0,
  y: 0,
  width: 450,
  height: 600,
  alwaysOnTop: true,
}
let persistWindowTimer: ReturnType<typeof setTimeout> | null = null

type TrayCommand = 'toggle-window' | 'exit-electron'

function mainWindowIsShown(): boolean {
  return Boolean(mainWindow && !mainWindow.isDestroyed()
    && !mainWindow.isMinimized() && mainWindow.isVisible())
}

function preferencesFile(): string {
  return join(app.getPath('userData'), 'window-preferences.json')
}

function validNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value)
}

function normalizeWindowPreferences(value: unknown): WindowPreferences | null {
  if (!value || typeof value !== 'object') return null
  const raw = value as Partial<WindowPreferences>
  if (!validNumber(raw.x) || !validNumber(raw.y) || !validNumber(raw.width) || !validNumber(raw.height)
    || typeof raw.alwaysOnTop !== 'boolean') return null
  return {
    x: Math.round(raw.x), y: Math.round(raw.y),
    width: Math.min(2400, Math.max(200, Math.round(raw.width))),
    height: Math.min(1800, Math.max(250, Math.round(raw.height))),
    alwaysOnTop: raw.alwaysOnTop,
  }
}

function visibleWindowBounds(preferences: WindowPreferences): Electron.Rectangle {
  const desired = { x: preferences.x, y: preferences.y, width: preferences.width, height: preferences.height }
  const displays = screen.getAllDisplays()
  const isVisible = displays.some(({ workArea }) => desired.x < workArea.x + workArea.width
    && desired.x + desired.width > workArea.x && desired.y < workArea.y + workArea.height
    && desired.y + desired.height > workArea.y)
  if (isVisible) return desired
  const workArea = screen.getPrimaryDisplay().workArea
  return {
    x: workArea.x + Math.max(0, Math.floor((workArea.width - desired.width) / 2)),
    y: workArea.y + Math.max(0, Math.floor((workArea.height - desired.height) / 2)),
    width: Math.min(desired.width, workArea.width),
    height: Math.min(desired.height, workArea.height),
  }
}

function readWindowPreferences(): WindowPreferences | null {
  try { return normalizeWindowPreferences(JSON.parse(readFileSync(preferencesFile(), 'utf8'))) } catch (_) { return null }
}

function persistWindowPreferences() {
  if (!mainWindow || mainWindow.isDestroyed()) return
  const bounds = mainWindow.getBounds()
  try {
    writeFileSync(preferencesFile(), JSON.stringify({ ...bounds, alwaysOnTop: mainWindow.isAlwaysOnTop() }), 'utf8')
  } catch (error) {
    console.warn('[Window] could not persist local preferences', error)
  }
}

function scheduleWindowPreferencesPersist() {
  if (persistWindowTimer) clearTimeout(persistWindowTimer)
  persistWindowTimer = setTimeout(() => {
    persistWindowTimer = null
    persistWindowPreferences()
  }, 350)
}

function stopWindowStatePolling() {
  if (windowStateTimer) clearInterval(windowStateTimer)
  windowStateTimer = null
}

function startWindowStatePolling() {
  if (!mainWindowIsShown() || windowStateTimer) return
  publishWindowState(true)
  windowStateTimer = setInterval(publishWindowState, 16)
}

function destroyTray() {
  appTray?.destroy()
  appTray = null
}

function handleTrayCommand(command: TrayCommand) {
  switch (command) {
    case 'toggle-window':
      if (mainWindowIsShown()) mainWindow?.hide()
      else revealMainWindow()
      return
    case 'exit-electron':
      if (mainWindow && !mainWindow.isDestroyed()) mainWindow.destroy()
      destroyTray()
      app.quit()
  }
}

function trayMenuTemplate(): MenuItemConstructorOptions[] {
  return [
    {
      label: mainWindowIsShown() ? '隐藏窗口' : '显示窗口',
      click: () => handleTrayCommand('toggle-window'),
    },
    { type: 'separator' },
    { label: '退出 Electron', click: () => handleTrayCommand('exit-electron') },
  ]
}

function refreshTrayMenu() {
  appTray?.setContextMenu(Menu.buildFromTemplate(trayMenuTemplate()))
}

function createTray() {
  if (process.platform !== 'win32' || appTray) return
  const icon = resolve(app.getAppPath(), '..', 'GPT_SoVITS', 'icons', 'chat_list.png')
  appTray = new Tray(icon)
  appTray.setToolTip('D_sakiko')
  appTray.on('click', () => handleTrayCommand('toggle-window'))
  appTray.on('right-click', refreshTrayMenu)
  refreshTrayMenu()
}

function readBridgeSession(): BridgeSession | null {
  const sessionFile = process.env.DSAKIKO_ELECTRON_SESSION_FILE
    || resolve(app.getAppPath(), '..', '.electron-bridge-session.json')
  try {
    const parsed = JSON.parse(readFileSync(sessionFile, 'utf8')) as Partial<BridgeSession>
    return typeof parsed.protocol === 'string' && typeof parsed.token === 'string'
      && typeof parsed.ws_url === 'string' && typeof parsed.instance_id === 'string'
      ? { protocol: parsed.protocol, token: parsed.token, ws_url: parsed.ws_url, instance_id: parsed.instance_id }
      : null
  } catch (_) {
    return null
  }
}

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

function isTrustedRenderer(event: Electron.IpcMainEvent | Electron.IpcMainInvokeEvent): boolean {
  return Boolean(mainWindow && !mainWindow.isDestroyed() && event.sender === mainWindow.webContents)
}

function createWindow() {
  if (mainWindow && !mainWindow.isDestroyed()) {
    revealMainWindow()
    return
  }
  const preferences = readWindowPreferences()
  mainWindow = new BrowserWindow({
    title: 'Saki',
    ...(preferences ? visibleWindowBounds(preferences) : { width: defaultWindowPreferences.width, height: defaultWindowPreferences.height }),
    show: false,
    frame: false,
    transparent: true,
    hasShadow: false,
    type: 'panel' as const,
    webPreferences: {
      preload: join(__dirname, '../preload/index.js'),
      sandbox: true,
      contextIsolation: true,
      nodeIntegration: false,
      webSecurity: true,
      allowRunningInsecureContent: false,
      webgl: true,
    },
  })

  mainWindow.setAlwaysOnTop(preferences?.alwaysOnTop ?? defaultWindowPreferences.alwaysOnTop, 'screen-saver', 1)
  mainWindow.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true })
  mainWindow.setFullScreenable(false)
  mainWindow.on('ready-to-show', () => mainWindow!.show())
  mainWindow.on('ready-to-show', startWindowStatePolling)
  mainWindow.on('move', () => { publishWindowState(); scheduleWindowPreferencesPersist() })
  mainWindow.on('resize', () => { publishWindowState(); scheduleWindowPreferencesPersist() })
  mainWindow.on('show', () => { refreshTrayMenu(); startWindowStatePolling() })
  mainWindow.on('hide', () => { refreshTrayMenu(); stopWindowStatePolling() })
  mainWindow.on('minimize', () => { refreshTrayMenu(); stopWindowStatePolling() })
  mainWindow.on('restore', () => { refreshTrayMenu(); startWindowStatePolling() })
  mainWindow.on('close', () => {
    if (!persistWindowTimer) return
    clearTimeout(persistWindowTimer)
    persistWindowTimer = null
    persistWindowPreferences()
  })
  mainWindow.on('closed', () => {
    if (persistWindowTimer) clearTimeout(persistWindowTimer)
    persistWindowTimer = null
    stopWindowStatePolling()
    mainWindow = null
    lastWindowState = ''
  })

  const viteDevServerUrl = process.env.ELECTRON_RENDERER_URL
  const allowedNavigation = (url: string) => viteDevServerUrl
    ? url.startsWith(viteDevServerUrl)
    : url.startsWith('file://')
  mainWindow.webContents.setWindowOpenHandler(() => ({ action: 'deny' }))
  mainWindow.webContents.on('will-navigate', (event, url) => {
    if (!allowedNavigation(url)) event.preventDefault()
  })
  if (viteDevServerUrl) {
    mainWindow.loadURL(viteDevServerUrl)
    mainWindow.webContents.openDevTools({ mode: 'detach' })
  } else {
    mainWindow.loadFile(join(__dirname, '../renderer/index.html'))
  }

  if (ipcHandlersRegistered) return
  ipcHandlersRegistered = true

  ipcMain.on('window-state-ready', (event) => {
    if (isTrustedRenderer(event)) publishWindowState(true)
  })
  ipcMain.handle('get-bridge-session', (event) => isTrustedRenderer(event) ? readBridgeSession() : null)
  ipcMain.handle('get-window-state', (event) => {
    if (!isTrustedRenderer(event) || !mainWindow) return null
    return { alwaysOnTop: mainWindow.isAlwaysOnTop(), bounds: mainWindow.getBounds(), visible: mainWindowIsShown() }
  })

  // Resize
  ipcMain.handle('resize-window', (event, payload) => {
    if (!isTrustedRenderer(event) || !mainWindow || !payload || typeof payload !== 'object') return
    const { deltaX, deltaY, direction } = payload as { deltaX?: unknown; deltaY?: unknown; direction?: unknown }
    if (typeof deltaX !== 'number' || !Number.isFinite(deltaX) || typeof deltaY !== 'number'
      || !Number.isFinite(deltaY) || typeof direction !== 'string'
      || !/^[nesw]+$/.test(direction)) return
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
  ipcMain.handle('toggle-devtools', (event) => {
    if (!isTrustedRenderer(event) || !mainWindow) return false
    if (mainWindow.webContents.isDevToolsOpened()) { mainWindow.webContents.closeDevTools(); return false }
    mainWindow.webContents.openDevTools({ mode: 'detach' }); return true
  })

  // 鼠标穿透
  ipcMain.handle('set-ignore-mouse-events', (event, ignore, options) => {
    if (!isTrustedRenderer(event) || typeof ignore !== 'boolean' || (options !== undefined
      && (!options || typeof options !== 'object' || typeof (options as { forward?: unknown }).forward !== 'boolean'))) return
    mainWindow?.setIgnoreMouseEvents(ignore, options)
  })

  // 置顶
  ipcMain.handle('toggle-always-on-top', (event) => {
    if (!isTrustedRenderer(event) || !mainWindow) return false
    const cur = mainWindow.isAlwaysOnTop()
    mainWindow.setAlwaysOnTop(!cur, 'screen-saver', 1)
    persistWindowPreferences()
    return !cur
  })

  ipcMain.handle('close-window', (event) => {
    if (!isTrustedRenderer(event)) return
    handleTrayCommand('exit-electron')
  })

  ipcMain.handle('hide-window', (event) => {
    if (!isTrustedRenderer(event)) return
    mainWindow?.hide()
  })
}

function revealMainWindow() {
  if (!mainWindow || mainWindow.isDestroyed()) {
    if (app.isReady()) createWindow()
    return
  }
  if (mainWindow.isMinimized()) mainWindow.restore()
  mainWindow.show()
  mainWindow.focus()
  publishWindowState(true)
}

if (!hasSingleInstanceLock) {
  app.quit()
} else {
  app.on('second-instance', () => revealMainWindow())
  app.whenReady().then(() => {
    createWindow()
    createTray()
  })
  app.on('will-quit', destroyTray)
  app.on('window-all-closed', () => { if (process.platform !== 'darwin') app.quit() })
  app.on('activate', revealMainWindow)
}
