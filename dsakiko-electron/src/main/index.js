import { installSingleInstance } from './single-instance.js'
import { app, dialog, ipcMain } from 'electron'
import { fileURLToPath } from 'node:url'
import { createRequire } from 'node:module'
import { readJson } from '../backend/core/support.js'
import { join, dirname } from 'node:path'
import { mkdirSync } from 'node:fs'
import { resolveDataDirectories, validateDataDirectories } from './data-directories.js'
import { IMPORT_PURPOSES } from '../shared/contracts/assets.js'
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
let directories
const instance = installSingleInstance(app, () => mainWindow)

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
  const pythonRoot = join(app.isPackaged ? process.resourcesPath : app.getAppPath(), 'python')
  const resourcesRoot = join(app.isPackaged ? process.resourcesPath : app.getAppPath(), 'resources')
  const builtinConnection = await readJson(join(resourcesRoot, 'builtin-connection.json'))
  speechProcess = new SpeechProcess(pythonRoot, directories.cacheRoot, async () => {
    const runtime =
      process.env.DSAKIKO_PYTHON && !app.isPackaged
        ? null
        : await backend.setup.acquireRuntime('gsv')
    let startup
    try {
      startup = await backend.speechResources.acquire({ allArchitectures: true })
    } catch (error) {
      await runtime?.releaseResources()
      throw error
    }
    return {
      ...startup,
      runtimeEnvironment: runtime?.environment ?? {},
      managedRuntime: !!runtime,
      bindResources: async (pid, group) => {
        await startup.bindResources(pid, group)
        await runtime?.bindResources(pid, group)
      },
      releaseResources: async (pid) => {
        await startup.releaseResources(pid)
        await runtime?.releaseResources(pid)
      },
      hostProbe: transport.healthProbe,
      pythonExecutable: runtime?.pythonExecutable ?? process.env.DSAKIKO_PYTHON,
      assetsRoot: directories.resourceLibraryRoot,
      maxConcurrent: startup.maxConcurrentInferences,
      maxResident: startup.maxResidentModels
    }
  })
  backend = await createCoreBackend({
    ...directories,
    setupOptions: { catalogDirectory: join(resourcesRoot, 'setup') },
    builtinDirectory: join(resourcesRoot, 'builtin-characters'),
    builtinConnection,
    recognitionModelDirectory: app.isPackaged
      ? undefined
      : process.env.DSAKIKO_ASR_MODEL || join(app.getAppPath(), '.local/models/whisper-small'),
    recognitionDependencyEntry: app.isPackaged
      ? undefined
      : createRequire(import.meta.url)
          .resolve('@huggingface/transformers')
          .replace(/\.cjs$/, '.mjs'),
    recognitionWorkerPath: join(app.getAppPath(), 'out/main/recognition-worker.js'),
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
  ipcMain.handle('host:select-legacy-directory', async (event) => {
    checkSender(event)
    const result = await dialog.showOpenDialog(mainWindow, {
      properties: ['openDirectory'],
      title: '选择 Python 旧版本文件夹'
    })
    if (result.canceled || !result.filePaths[0]) return null
    return backend.setup.grantLegacySource(result.filePaths[0])
  })
  ipcMain.handle('host:runtime-info', (event) => {
    checkSender(event)
    return { platform: process.platform, appVersion: app.getVersion() }
  })
  ipcMain.handle('host:import-files', async (event, options) => {
    checkSender(event)
    requireValue(options && IMPORT_PURPOSES.includes(options.purpose), 'invalid_asset_purpose')
    requireValue(
      !options.directory || ['model', 'live2d'].includes(options.purpose),
      'invalid_asset_purpose'
    )
    const result = await dialog.showOpenDialog(mainWindow, {
      title: '导入本地资源',
      properties: [options.directory ? 'openDirectory' : 'openFile', 'multiSelections']
    })
    if (result.canceled) return []
    const imported = []
    for (const path of result.filePaths)
      imported.push(
        await backend.imports.importAsset(
          {
            kind: options.directory ? 'local-directory' : 'local-file',
            path,
            entry: options.entry
          },
          options.purpose,
          options.draftId ?? null
        )
      )
    return imported
  })
  mainWindow = createMainWindow()
  mainWindow.once('ready-to-show', instance.windowReady)
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
// 语音先拒绝新任务并完成音频交接；此时保留心跳，避免 Python 抢先清理输出。
app.on('before-quit', (event) => {
  if (quitting || !instance.primary) return
  event.preventDefault()
  quitting = true
  Promise.resolve()
    .then(() => backend?.speech.close?.())
    .catch((error) => console.error('语音交接失败', error))
    .then(() => transport?.close())
    .catch((error) => console.error('传输关闭失败', error))
    .then(() => backend?.close())
    .catch((error) => console.error('业务收尾失败', error))
    .then(() => speechProcess?.close())
    .then((confirmedPid) => backend?.speech.processStopped?.(confirmedPid))
    .catch((error) => console.error('退出清理失败', error))
    .finally(() => {
      app.exit(0)
    })
})
if (instance.primary) {
  // Chromium 也使用缓存目录，需在 ready 事件前指定。
  try {
    directories = resolveDataDirectories({
      platform: process.platform,
      home: app.getPath('home'),
      env: process.env
    })
    const readOnlyRoots = []
    if (app.isPackaged) {
      readOnlyRoots.push(process.resourcesPath, app.getAppPath())
      if (process.platform === 'darwin')
        readOnlyRoots.push(dirname(dirname(dirname(process.execPath))))
    }
    validateDataDirectories(directories, readOnlyRoots)
    requireValue(
      !process.env.DSAKIKO_RESOURCE_ROOT,
      'legacy_resource_root',
      'DSAKIKO_RESOURCE_ROOT 已停用，请配置 DSAKIKO_RESOURCE_LIBRARY_DIR，并准备资源库中的基础模型。'
    )
    const sessionData = join(directories.cacheRoot, 'chromium')
    mkdirSync(sessionData, { recursive: true })
    app.setPath('sessionData', sessionData)
  } catch (error) {
    console.error('目录配置失败', error)
    app.exit(1)
  }
  app
    .whenReady()
    .then(startApplication)
    .catch((error) => {
      console.error('启动失败', error)
      quitting = true
      app.quit()
    })
}
