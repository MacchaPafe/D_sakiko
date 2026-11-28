import { access, link, mkdir, mkdtemp, rm, stat, writeFile } from 'node:fs/promises'
import { randomUUID } from 'node:crypto'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { afterEach, expect, test, vi } from 'vitest'
import { createCoreBackend } from '../../src/backend/create-core-backend.js'
import { resolveApplicationBundlePath, StorageSpace } from '../../src/main/storage-space.js'
import { createUiPreferences } from '../../src/main/ui-preferences.js'

const cleanup = []
afterEach(async () => {
  for (const dispose of cleanup.splice(0).reverse()) await dispose()
})

async function fixture() {
  const directory = await mkdtemp(path.join(tmpdir(), 'sakiko-storage-'))
  cleanup.push(() => rm(directory, { recursive: true, force: true }))
  const directories = {
    appDataRoot: path.join(directory, 'data'),
    resourceLibraryRoot: path.join(directory, 'library'),
    cacheRoot: path.join(directory, 'cache')
  }
  const backend = await createCoreBackend({ ...directories, scanReminders: false })
  cleanup.push(() => backend.close())
  return { backend, directories }
}

async function put(root, name, buffer) {
  const file = path.join(root, name)
  await mkdir(path.dirname(file), { recursive: true })
  await writeFile(file, buffer)
  return file
}

test('删除聊天批次只计一次共享文件，新版本被跳过且继续保护该文件', async () => {
  const { backend } = await fixture()
  await backend.onboarding.begin({ path: 'quick' })
  await backend.onboarding.finish()
  const first = await backend.conversations.createChat({
    title: '第一条',
    mode: 'single-character',
    characterIds: ['sakiko']
  })
  const second = await backend.conversations.createChat({
    title: '第二条',
    mode: 'single-character',
    characterIds: ['sakiko']
  })
  const { asset } = await backend.assets.importAsset(
    {
      kind: 'bytes',
      name: 'image.png',
      mediaType: 'image/png',
      bytes: Buffer.from('image')
    },
    'draft',
    'chat-image'
  )
  const file = await backend.assets.localPath(asset)
  for (const id of [first, second]) {
    const { chat } = await backend.store.load(id)
    chat.meta.settings.presentation.background = asset
    await backend.store.save(chat)
  }
  await backend.assets.releaseOwner('draft')
  const { chats, estimatedBytes } = await backend.conversations.previewChatCleanup([first, second])
  expect(estimatedBytes - chats.reduce((sum, chat) => sum + chat.estimatedBytes, 0)).toBe(5)
  await backend.conversations.renameChat(second, '仍应保留')
  const result = await backend.conversations.deleteChats(
    chats.map(({ id, revision }) => ({ id, revision }))
  )
  expect(result.deleted).toEqual([first])
  expect(result.skipped).toMatchObject([{ id: second, problem: { code: 'revision_conflict' } }])
  await expect(access(file)).resolves.toBeUndefined()
  await backend.conversations.deleteChat(second)
  await expect(access(file)).rejects.toMatchObject({ code: 'ENOENT' })
})

test('语音卸载撤销安装与模型绑定，保留其他持有者和用户选项', async () => {
  const { backend, directories } = await fixture()
  const runtime = path.join(
    directories.resourceLibraryRoot,
    'runtimes',
    randomUUID(),
    'gsv.runtime'
  )
  await put(runtime, 'entry', 'runtime')
  const refs = {}
  for (const name of ['owned', 'shared']) {
    refs[name] = (
      await backend.assets.importAsset(
        {
          kind: 'bytes',
          name,
          mediaType: 'application/octet-stream',
          bytes: Buffer.from(name)
        },
        'draft',
        'model'
      )
    ).asset
  }
  const sharedFile = await backend.assets.localPath(refs.shared)
  const ownedFile = await backend.assets.localPath(refs.owned)
  await backend.assets.addReferences('library:retained', [refs.shared])
  await backend.setup.activate({
    id: 'gsv.runtime',
    status: 'ready',
    directory: runtime,
    runtime: { pythonExecutable: path.join(runtime, 'entry') },
    refs: {}
  })
  await backend.setup.activate({ id: 'gsv.models', status: 'ready', refs })
  await backend.settings.set('speechStartup.gptSovits.resources', {
    shared: { bert: { weights: refs.owned, config: refs.shared } },
    versions: {}
  })
  await backend.settings.set('speech.speed', 1.5)
  await backend.assets.releaseOwner('draft')
  const preview = await backend.setup.previewRuntimeRemoval('gsv')
  expect(preview).toMatchObject({ installed: true, modelBytes: 5, runtimeBytes: 7 })
  const result = await backend.setup.uninstallRuntime('gsv')
  expect(result.pending).toBe(false)
  expect(await backend.setup.installations()).toEqual({})
  expect(await backend.settings.get('speech.speed')).toBe(1.5)
  await expect(stat(runtime)).rejects.toMatchObject({ code: 'ENOENT' })
  await expect(stat(ownedFile)).rejects.toMatchObject({ code: 'ENOENT' })
  await expect(stat(sharedFile)).resolves.toBeDefined()
})

test('清理已无任务的下载文件，保留续传分片和用途不明的文件', async () => {
  const { backend, directories } = await fixture()
  const keepHash = 'a'.repeat(64)
  const orphan = await put(directories.cacheRoot, 'setup/' + 'b'.repeat(64), 'remove')
  const keep = await put(directories.cacheRoot, 'setup/' + keepHash + '.part', 'resume')
  const unknown = await put(directories.cacheRoot, 'setup/user.txt', 'keep')
  backend.setup.listJobs = async () => [{ id: 'resume' }]
  backend.setup.readJob = async () => ({
    id: 'resume',
    kind: 'download',
    status: 'paused',
    selected: [{ files: [{ sha256: keepHash }] }]
  })
  const session = { getCacheSize: async () => 0, clearCache: vi.fn() }
  const page = new StorageSpace({ backend, directories, session })
  expect((await page.preview()).find(({ id }) => id === 'downloads').bytes).toBe(6)
  expect(await page.clean(['downloads'])).toMatchObject([{ id: 'downloads', failed: [] }])
  await expect(access(orphan)).rejects.toMatchObject({ code: 'ENOENT' })
  await expect(access(keep)).resolves.toBeUndefined()
  await expect(access(unknown)).resolves.toBeUndefined()
  expect(session.clearCache).not.toHaveBeenCalled()
})

test('并发保存桌面偏好并在重新读取时恢复各字段', async () => {
  const { directories } = await fixture()
  const file = path.join(directories.appDataRoot, 'ui-preferences.json')
  const writer = createUiPreferences(file)
  await Promise.all([
    writer.set('playback', { volume: 0.6, bgmVolume: 0.4 }),
    writer.set('historySize', { width: 380, height: 700 }),
    writer.set('outfitVersion', 'v3')
  ])
  expect(await createUiPreferences(file).get()).toEqual({
    playback: { volume: 0.6, bgmVolume: 0.4 },
    historySize: { width: 380, height: 700 },
    outfitVersion: 'v3'
  })
})

test('在线图库缩略图单独统计和清理，清理前的请求不会重新写入缓存', async () => {
  const { backend, directories } = await fixture()
  const { source } = backend.backgrounds
  const previewName = 'a'.repeat(64) + '.png'
  const cached = await put(directories.cacheRoot, 'background-previews/' + previewName, '缓存')
  const temporary = await put(
    directories.cacheRoot,
    'background-previews/' + previewName + '.' + randomUUID() + '.tmp',
    '暂存'
  )
  const unknown = await put(directories.cacheRoot, 'background-previews/user.png', '保留')
  const builtin = await put(directories.cacheRoot, 'builtin-characters/v1/image.png', '保留')
  const session = { getCacheSize: async () => 0, clearCache: vi.fn() }
  const storage = new StorageSpace({ backend, directories, session })
  const usage = async () => {
    const cache = (await storage.overview()).find(({ id }) => id === 'cacheRoot')
    return cache.entries.find(({ name }) => name === '在线图库缩略图')
  }
  const bytes = Buffer.byteLength('缓存暂存')
  expect(await usage()).toMatchObject({ bytes })
  expect((await storage.preview()).find(({ id }) => id === 'background-previews').bytes).toBe(bytes)

  const started = Promise.withResolvers()
  const download = Promise.withResolvers()
  const png = Buffer.from(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+XKZkAAAAASUVORK5CYII=',
    'base64'
  )
  vi.spyOn(source, 'item').mockImplementation(async (key) => ({ key, sha256: null }))
  vi.spyOn(source, 'bytes')
    .mockImplementationOnce(() => {
      started.resolve()
      return download.promise
    })
    .mockResolvedValue(png)
  const pending = backend.backgrounds.preview('images/backgrounds/first.png')
  await Promise.race([started.promise, pending])
  try {
    expect(await storage.clean(['background-previews'])).toMatchObject([
      { id: 'background-previews', bytes, failed: [] }
    ])
  } finally {
    download.resolve(png)
    await pending
  }
  for (const file of [cached, temporary])
    await expect(access(file)).rejects.toMatchObject({ code: 'ENOENT' })
  for (const file of [unknown, builtin]) await expect(access(file)).resolves.toBeUndefined()
  expect(await usage()).toBeUndefined()
  expect(session.clearCache).not.toHaveBeenCalled()

  await backend.backgrounds.preview('images/backgrounds/second.png')
  expect(await usage()).toMatchObject({ bytes: png.length })
})

test('存储总览忽略系统元文件、折叠小未识别项并附带磁盘卷容量', async () => {
  const { backend, directories } = await fixture()
  await put(directories.appDataRoot, '.DS_Store', Buffer.alloc(1024))
  await put(directories.appDataRoot, '._hidden', Buffer.alloc(512))
  await put(directories.appDataRoot, 'small-unknown.tmp', Buffer.alloc(2048))
  await put(directories.resourceLibraryRoot, 'mid-unknown.bin', Buffer.alloc(2 * 1024 * 1024))
  const session = { getCacheSize: async () => 0, clearCache: vi.fn() }
  const page = new StorageSpace({ backend, directories, session })
  const overview = await page.overview()
  const appRoot = overview.find(({ id }) => id === 'appDataRoot')
  const libRoot = overview.find(({ id }) => id === 'resourceLibraryRoot')
  expect(appRoot.volume).toMatchObject({
    totalBytes: expect.any(Number),
    availableBytes: expect.any(Number)
  })
  expect(appRoot.volume.totalBytes).toBeGreaterThan(0)
  // .DS_Store / ._hidden 被忽略，且不足 1MB 的未识别文件不单独展示分组。
  expect(appRoot.entries.find(({ name }) => name === '未识别文件')).toBeUndefined()
  // 2MB 的未识别文件会显示分组，但因不足 10MB 明细阈值而折叠到 hiddenCount。
  const libUnknown = libRoot.entries.find(({ name }) => name === '未识别文件')
  expect(libUnknown).toMatchObject({
    bytes: 2 * 1024 * 1024,
    details: [],
    hiddenCount: 1,
    hiddenBytes: 2 * 1024 * 1024
  })
})

test('角色占用统计按角色汇总关联资源与对话数', async () => {
  const { backend, directories } = await fixture()
  await backend.onboarding.begin({ path: 'quick' })
  await backend.onboarding.finish()
  await backend.conversations.createChat({
    title: '角色占用测试',
    mode: 'single-character',
    characterIds: ['sakiko']
  })
  const session = { getCacheSize: async () => 0, clearCache: vi.fn() }
  const page = new StorageSpace({ backend, directories, session })
  const usage = await page.characterUsage()
  expect(usage.sakiko).toMatchObject({
    totalBytes: expect.any(Number),
    formCount: expect.any(Number),
    outfitCount: expect.any(Number),
    hasVoice: expect.any(Boolean),
    chatCount: 1
  })
})

test('语音合成运行环境与基础模型合并分组，并标注硬链接共享字节', async () => {
  const { backend, directories } = await fixture()
  const runtime = path.join(
    directories.resourceLibraryRoot,
    'runtimes',
    randomUUID(),
    'gsv.runtime'
  )
  await put(runtime, 'entry', 'runtime')
  const { asset: baseModel } = await backend.assets.importAsset(
    {
      kind: 'bytes',
      name: 'base.bin',
      mediaType: 'application/octet-stream',
      bytes: Buffer.from('0123456789')
    },
    'draft',
    'model'
  )
  const basePath = await backend.assets.localPath(baseModel)
  // 模拟从旧版目录硬链接导入的模型文件（nlink > 1）。
  await mkdir(directories.cacheRoot, { recursive: true })
  await link(basePath, path.join(directories.cacheRoot, 'legacy-hardlink.bin'))
  await backend.setup.activate({
    id: 'gsv.runtime',
    status: 'ready',
    directory: runtime,
    runtime: { pythonExecutable: path.join(runtime, 'entry') },
    refs: {}
  })
  await backend.setup.activate({ id: 'gsv.models', status: 'ready', refs: { base: baseModel } })
  await backend.assets.releaseOwner('draft')

  const preview = await backend.setup.previewRuntimeRemoval('gsv')
  expect(preview).toMatchObject({
    installed: true,
    runtimeBytes: 7,
    modelBytes: 0,
    sharedBytes: 10
  })

  const session = { getCacheSize: async () => 0, clearCache: vi.fn() }
  const page = new StorageSpace({ backend, directories, session })
  const overview = await page.overview()
  const libRoot = overview.find(({ id }) => id === 'resourceLibraryRoot')
  const merged = libRoot.entries.find(({ name }) => name === 'GPT-SoVITS 语音合成')
  expect(merged).toMatchObject({
    bytes: 17,
    details: [
      { name: '基础模型', bytes: 10 },
      { name: '运行环境', bytes: 7 }
    ]
  })
})

test('打包版本解析应用程序本体路径并在存储总览末尾展示且缓存', async () => {
  expect(
    resolveApplicationBundlePath({
      isPackaged: false,
      platform: 'darwin',
      execPath: '/repo/node_modules/electron/dist/Electron.app/Contents/MacOS/Electron'
    })
  ).toBeNull()
  expect(
    resolveApplicationBundlePath({
      isPackaged: true,
      platform: 'darwin',
      execPath: '/Applications/数字小祥.app/Contents/MacOS/数字小祥',
      resourcesPath: '/Applications/数字小祥.app/Contents/Resources'
    })
  ).toBe('/Applications/数字小祥.app')
  expect(
    resolveApplicationBundlePath({
      isPackaged: true,
      platform: 'win32',
      execPath: 'C:\\Program Files\\数字小祥\\dsakiko.exe',
      resourcesPath: 'C:\\Program Files\\数字小祥\\resources'
    })
  ).toBe('C:\\Program Files\\数字小祥')
  expect(
    resolveApplicationBundlePath({
      isPackaged: true,
      platform: 'linux',
      execPath: '/tmp/.mount_dsakiko/dsakiko',
      env: { APPIMAGE: '/home/user/dsakiko.AppImage' }
    })
  ).toBe('/home/user/dsakiko.AppImage')

  const { backend, directories } = await fixture()
  const bundleDir = path.join(path.dirname(directories.appDataRoot), 'app-bundle')
  await put(bundleDir, 'Contents/Resources/app.asar', Buffer.alloc(1234))
  const session = { getCacheSize: async () => 0, clearCache: vi.fn() }
  const page = new StorageSpace({ backend, directories, session, applicationBundle: bundleDir })
  const overview = await page.overview()
  expect(overview[overview.length - 1]).toMatchObject({
    id: 'applicationBundle',
    name: '应用程序',
    readonly: true,
    bytes: 1234,
    entries: [{ name: '程序本体', bytes: 1234, details: [] }]
  })
})
