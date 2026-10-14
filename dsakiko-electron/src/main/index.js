import { app, dialog, ipcMain } from 'electron'
import { fileURLToPath } from 'node:url'
import { join } from 'node:path'
import { createCoreBackend } from '../backend/create-core-backend.js'
import { startHttpServer } from '../server/http-server.js'
import { SpeechProcess } from './processes/speech-process.js'
import { createMainWindow } from './windows/main-window.js'
import { requireValue } from '../backend/core/support.js'

let mainWindow = null
let backend = null
let transport = null
let speechProcess = null
let quitting = false

/** IPC 是宿主权限入口，只信任当前主窗口顶层页面，不能接受子 frame 借用权限。 */
function checkSender(event) {
  requireValue(
    mainWindow &&
      !mainWindow.isDestroyed() &&
      event.sender === mainWindow.webContents &&
      event.senderFrame === mainWindow.webContents.mainFrame,
    'unauthorized'
  )
}
/** 先装配业务和本机传输，再创建窗口；后端不依赖 React 是否加载成功。 */
async function startApplication() {
  app.setName('D_Sakiko')
  // 正式存档与旧原型隔离；旧扁平历史不能被误当作新契约直接迁移。
  const dataRoot = process.env.DSAKIKO_DATA_DIR || join(app.getPath('userData'), 'core-v1')
  speechProcess = new SpeechProcess(join(app.getAppPath(), 'python'), dataRoot, async () => {
    const settings = await backend.settings.get()
    return {
      resourceRoot: process.env.DSAKIKO_RESOURCE_ROOT || '',
      pythonExecutable: process.env.DSAKIKO_PYTHON || 'python3',
      assetsRoot: join(dataRoot, 'assets'),
      precision: settings.speechStartup.precision,
      architecture: settings.speechStartup.architecture,
      device: settings.speechStartup.device,
      maxConcurrent: settings.speechStartup.maxConcurrentInferences,
      maxResident: settings.speechStartup.maxResidentModels
    }
  })
  backend = await createCoreBackend({
    dataRoot,
    connectSpeech: () => speechProcess.connect(),
    templateDirectory: join(app.getAppPath(), 'out/main/prompts')
  })
  const origins = ['null']
  if (!app.isPackaged && process.env.ELECTRON_RENDERER_URL)
    origins.push(new URL(process.env.ELECTRON_RENDERER_URL).origin)
  transport = await startHttpServer(backend, { origins })
  ipcMain.handle('host:backend-connection', (event) => {
    checkSender(event)
    return { baseUrl: transport.baseUrl, token: transport.token }
  })
  ipcMain.handle('host:runtime-info', (event) => {
    checkSender(event)
    return { platform: process.platform, appVersion: app.getVersion() }
  })
  ipcMain.handle('host:import-files', async (event) => {
    checkSender(event)
    const result = await dialog.showOpenDialog(mainWindow, {
      title: '导入本地资源',
      properties: ['openFile', 'multiSelections']
    })
    if (result.canceled) return []
    const imported = []
    for (const path of result.filePaths)
      imported.push(await backend.assets.importAsset({ kind: 'local-file', path }, 'host-import'))
    return imported
  })
  mainWindow = createMainWindow()
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
// 先关闭外部命令入口，再结算业务与 Python；总退出期限防止外部进程拖住应用。
app.on('before-quit', (event) => {
  if (quitting) return
  event.preventDefault()
  quitting = true
  const deadline = setTimeout(() => app.exit(0), 12000)
  Promise.resolve()
    .then(() => transport?.close())
    .then(() => backend?.close())
    .then(() => speechProcess?.close())
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
    console.error('启动失败', error)
    quitting = true
    app.quit()
  })
