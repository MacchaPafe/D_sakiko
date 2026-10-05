import { app, dialog, ipcMain, net, protocol } from 'electron'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { join } from 'node:path'
import { createBackend } from '../backend/create-backend.js'
import { PerformanceProxy } from '../backend/conversation/performance-proxy.js'
import { SpeechProcess } from './processes/speech-process.js'
import { createMainWindow } from './windows/main-window.js'
import {
  COMMANDS,
  DESKTOP_EVENT,
  PERFORMANCE_COMMAND,
  PERFORMANCE_REPLY,
  PERFORMANCE_UPDATE,
  toProblem,
  problemError
} from '../shared/contracts/desktop.js'

protocol.registerSchemesAsPrivileged([
  {
    scheme: 'dsakiko-media',
    privileges: {
      standard: true,
      secure: true,
      supportFetchAPI: true,
      stream: true,
      corsEnabled: true
    }
  }
])
let mainWindow = null
let backend = null
let quitting = false

function checkSender(event) {
  if (
    !mainWindow ||
    mainWindow.isDestroyed() ||
    event.sender !== mainWindow.webContents ||
    event.senderFrame !== mainWindow.webContents.mainFrame
  )
    throw problemError('unauthorized', '不允许此页面调用桌面能力。')
}
async function startApplication() {
  // 名称会进入 Chromium User-Agent；中文会使自定义协议的 Headers 转换失败。
  app.setName('D_Sakiko')
  const dataRoot = process.env.DSAKIKO_DATA_DIR || join(app.getPath('userData'), 'prototype')
  const performance = new PerformanceProxy()
  const speech = new SpeechProcess(join(app.getAppPath(), 'python'), dataRoot, () =>
    backend.settings.get()
  )
  backend = await createBackend({
    dataRoot,
    defaults: {
      resourceRoot: process.env.DSAKIKO_RESOURCE_ROOT || '',
      pythonExecutable: process.env.DSAKIKO_PYTHON || 'python3'
    },
    performance,
    connectSpeech: () => speech.connect(),
    restartSpeech: () => speech.close(),
    emit(value) {
      if (mainWindow && !mainWindow.isDestroyed()) mainWindow.webContents.send(DESKTOP_EVENT, value)
    }
  })
  protocol.handle('dsakiko-media', async (request) => {
    try {
      const path = await backend.assets.pathForUrl(request.url)
      const response = await net.fetch(pathToFileURL(path).href, { headers: request.headers })
      const headers = new Headers(response.headers)
      headers.set('Access-Control-Allow-Origin', '*')
      return new Response(response.body, { status: response.status, headers })
    } catch (error) {
      console.warn('媒体资源读取失败', error.message)
      return new Response('资源不可用', { status: 404 })
    }
  })
  for (const name of COMMANDS)
    ipcMain.handle(`desktop:${name}`, async (event, args) => {
      try {
        checkSender(event)
        if (!Array.isArray(args)) throw problemError('invalid_request', '命令参数无效。')
        if (name === 'performanceReady') {
          performance.attach((command) => mainWindow.webContents.send(PERFORMANCE_COMMAND, command))
          return { value: true }
        }
        if (name === 'chooseResourceRoot' || name === 'choosePython') {
          const result = await dialog.showOpenDialog(mainWindow, {
            title: name === 'choosePython' ? '选择 Python 解释器' : '选择旧程序根目录',
            properties: [name === 'choosePython' ? 'openFile' : 'openDirectory']
          })
          return { value: result.canceled ? null : result.filePaths[0] }
        }
        return { value: await backend[name](...args) }
      } catch (error) {
        return { problem: toProblem(error) }
      }
    })
  ipcMain.handle('runtime:get-info', (event) => {
    checkSender(event)
    return backend.getRuntimeInfo()
  })
  ipcMain.on(PERFORMANCE_REPLY, (event, value) => {
    try {
      checkSender(event)
      performance.reply(value)
    } catch {
      /* 拒绝非主页面回执。 */
    }
  })
  ipcMain.on(PERFORMANCE_UPDATE, (event, value) => {
    try {
      checkSender(event)
      performance.update(value)
    } catch {
      /* 拒绝非主页面进度。 */
    }
  })
  mainWindow = createMainWindow()
  mainWindow.webContents.on('render-process-gone', () => performance.disconnect())
  mainWindow.webContents.on('did-start-navigation', (_event, _url, _inPlace, isMainFrame) => {
    if (isMainFrame && performance.ready) performance.disconnect()
  })
  mainWindow.on('close', (event) => {
    if (!quitting) {
      event.preventDefault()
      app.quit()
    }
  })
  if (!app.isPackaged && process.env.ELECTRON_RENDERER_URL)
    await mainWindow.loadURL(process.env.ELECTRON_RENDERER_URL)
  else await mainWindow.loadFile(fileURLToPath(new URL('../renderer/index.html', import.meta.url)))
}
app.on('before-quit', (event) => {
  if (quitting) return
  event.preventDefault()
  quitting = true
  // 清理正常完成后直接退出；失去响应的渲染端不能无限阻塞窗口关闭握手。
  const deadline = setTimeout(() => app.exit(0), 12000)
  void Promise.resolve()
    .then(() => backend?.close())
    .catch((error) => console.error('退出清理失败', error))
    .finally(() => {
      clearTimeout(deadline)
      app.exit(0)
    })
})
app
  .whenReady()
  .then(startApplication)
  .catch((error) => {
    console.error('启动失败：', error)
    quitting = true
    app.quit()
  })
