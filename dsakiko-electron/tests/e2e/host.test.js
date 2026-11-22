import { spawn } from 'node:child_process'
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { fileURLToPath } from 'node:url'
import { join } from 'node:path'
import { afterEach, expect, test } from 'vitest'
import { _electron as electron } from 'playwright-core'
import { createBackendClient } from '../../src/renderer/src/backend/client.js'

let application
let directory
let client
afterEach(async () => {
  client?.close()
  await application?.close()
  if (directory) await rm(directory, { recursive: true, force: true })
})

async function verifyFormVoiceFiles(page, modelFile) {
  await page.getByRole('button', { name: '快速开始', exact: true }).click()
  await page.getByRole('button', { name: '跳过，直接开始', exact: true }).click()
  await page.getByRole('button', { name: '角色管理', exact: true }).click()
  const manager = page.getByRole('dialog', { name: '角色管理' })
  await manager.getByRole('button', { name: '新增角色' }).click()
  await manager.getByLabel('角色名称', { exact: true }).fill('语音文件测试')
  await manager.locator('input[type=file]').setInputFiles({
    name: '介绍.txt',
    mimeType: 'text/plain',
    buffer: Buffer.from('逐句选择参考录音。')
  })
  await manager.getByRole('button', { name: '创建角色' }).click()
  const pick = async (label, file) => {
    await application.evaluate(({ dialog }, selected) => {
      dialog.showOpenDialog = async () => ({ canceled: false, filePaths: [selected] })
    }, file)
    await manager
      .locator('.role-file')
      .filter({ has: page.getByText(label, { exact: true }) })
      .getByRole('button', { name: '选择文件' })
      .click()
    await manager.getByText(file.split(/[\\/]/).pop(), { exact: true }).waitFor()
  }
  await pick('GPT 模型', modelFile)
  await manager.getByRole('button', { name: '关闭角色管理' }).click()
  await page
    .getByRole('button', { name: '编辑角色：语音文件测试' })
    .or(page.getByRole('button', { name: '确认角色归属：语音文件测试' }))
    .click()
  await manager.getByText('model.ckpt', { exact: true }).waitFor()
  expect(await manager.getByLabel('启用回复语音').isDisabled()).toBe(true)
  const weights = join(directory, 'voice.pth'),
    recording = join(directory, 'reference.wav')
  await writeFile(weights, 'voice')
  await writeFile(recording, Buffer.alloc(44))
  await pick('SoVITS 模型', weights)
  await pick('默认参考录音', recording)
  await manager.getByLabel('录音对应文本').fill('你好。')
  const language = manager.getByRole('combobox', { name: '默认参考语言' })
  await language.click()
  await manager.getByRole('option', { name: '中文', exact: true }).click()
  await manager.getByLabel('启用回复语音').check()
  const role = (await client.characters.list()).find((item) => item.name === '语音文件测试')
  await expect
    .poll(async () => (await client.characters.get(role.id)).forms[0].voice?.defaultReference.text)
    .toBe('你好。')
  await manager.getByLabel('启用回复语音').uncheck()
  await expect
    .poll(
      async () =>
        (await client.characters.get(role.id)).forms[0].inactiveVoice?.defaultReference.language
    )
    .toBe('all_zh')
  await manager.getByRole('button', { name: '关闭角色管理' }).click()
}

test('构建后的 Electron 提供新宿主接口，HTTP 后端可独立于原型页面工作', async () => {
  directory = await mkdtemp(join(tmpdir(), 'dsakiko-host-'))
  const env = {
    ...process.env,
    DSAKIKO_DATA_DIR: join(directory, 'app'),
    DSAKIKO_RESOURCE_LIBRARY_DIR: join(directory, 'library'),
    DSAKIKO_CACHE_DIR: join(directory, 'cache')
  }
  delete env.DSAKIKO_RESOURCE_ROOT
  delete env.ELECTRON_RUN_AS_NODE
  delete env.ELECTRON_RENDERER_URL
  const launchOptions = {
    args: [
      fileURLToPath(new URL('../../', import.meta.url)),
      '--user-data-dir=' + join(directory, 'desktop')
    ],
    env
  }
  if (process.env.DSAKIKO_TEST_EXECUTABLE) {
    launchOptions.executablePath = process.env.DSAKIKO_TEST_EXECUTABLE
    // 指定打包程序时，Playwright 不会自动注入启动协调脚本。
    const loader = fileURLToPath(
      new URL('./lib/server/electron/loader.js', import.meta.resolve('playwright-core'))
    )
    launchOptions.args = ['-r', loader, '--user-data-dir=' + join(directory, 'desktop')]
  }
  application = await electron.launch(launchOptions)
  const page = await application.firstWindow()
  await application.evaluate(({ app, BrowserWindow }) => {
    globalThis.secondInstanceCount = 0
    app.on('second-instance', () => {
      globalThis.secondInstanceCount += 1
    })
    const window = BrowserWindow.getAllWindows()[0]
    window.hide()
  })
  const secondArgs = process.env.DSAKIKO_TEST_EXECUTABLE
    ? []
    : [fileURLToPath(new URL('../../', import.meta.url))]
  secondArgs.push('--user-data-dir=' + join(directory, 'desktop'))
  const second = spawn(application.process().spawnfile, secondArgs, { env, stdio: 'ignore' })
  const secondExit = await new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      second.kill()
      reject(new Error('第二实例没有退出'))
    }, 10000)
    second.once('error', (error) => {
      clearTimeout(timer)
      reject(error)
    })
    second.once('exit', (code) => {
      clearTimeout(timer)
      resolve(code)
    })
  })
  expect(secondExit).toBe(0)
  await expect
    .poll(() =>
      application.evaluate(({ BrowserWindow }) => ({
        received: globalThis.secondInstanceCount,
        visible: BrowserWindow.getAllWindows()[0].isVisible()
      }))
    )
    .toEqual({ received: 1, visible: true })

  // 旧页面仍可能报告缺少原型桥；此测试只验收真实 preload、IPC 和业务传输，不冒充页面验收。
  await page.waitForFunction(() => Boolean(window.dsakikoHost))
  const dragRegion = page.locator('.window-drag-region')
  if (process.platform === 'darwin') {
    await dragRegion.waitFor()
    const region = await dragRegion.boundingBox()
    const controls = await page.evaluate(() =>
      navigator.windowControlsOverlay.getTitlebarAreaRect().toJSON()
    )
    expect(controls.x).toBeGreaterThan(0)
    expect(region.x).toBeGreaterThanOrEqual(controls.x)
    expect(region.height).toBe(controls.height)
  } else {
    await dragRegion.waitFor({ state: 'hidden' })
  }
  const bootstrap = await page.evaluate(async () => ({
    connection: await window.dsakikoHost.getBackendConnection(),
    runtime: await window.dsakikoHost.getRuntimeInfo(),
    methods: Object.keys(window.dsakikoHost),
    nodeVisible: typeof window.require !== 'undefined'
  }))
  expect(bootstrap.methods.sort()).toEqual([
    'chatGPTAccount',
    'getBackendConnection',
    'getRuntimeInfo',
    'getWindowCloseBehavior',
    'getWindowVisibility',
    'importAttachment',
    'importFiles',
    'importOutfitDirectory',
    'onWindowVisibility',
    'openProviderKeyPage',
    'openWebSource',
    'saveChatArchive',
    'selectLegacyDirectory',
    'selectVertexAccount',
    'setWindowCloseBehavior'
  ])
  expect(
    await page.evaluate(() => window.dsakikoHost.chatGPTAccount('access', 'test'))
  ).toMatchObject({ problem: { code: 'invalid_input' } })
  expect(bootstrap.nodeVisible).toBe(false)
  expect(bootstrap.runtime.platform).toBe(process.platform)
  expect(bootstrap.connection.baseUrl).toMatch(/^http:\/\/127\.0\.0\.1:\d+$/)
  client = createBackendClient(bootstrap.connection)
  expect(await client.notices.list()).toMatchObject({
    runtimeId: expect.any(String),
    revision: 0,
    notices: []
  })
  const settings = await client.settings.get()
  expect(settings).not.toHaveProperty('playback')
  const saved = await client.settings.set('display.theme', 'dark', {
    expectedRevision: settings.revision
  })
  expect(saved.revision).toBe(settings.revision + 1)
  // 从真正的 renderer 发起请求，额外验证 file:// 来源白名单与页面 CSP。
  const visible = await page.evaluate(async () => {
    const { baseUrl, token } = await window.dsakikoHost.getBackendConnection()
    const response = await fetch(baseUrl + '/api/v1/call/settings/get', {
      method: 'POST',
      headers: { Authorization: 'Bearer ' + token, 'Content-Type': 'application/json' },
      body: JSON.stringify({ args: [] })
    })
    return response.json()
  })
  expect(visible.value.display.theme).toBe('dark')
  for (const purpose of ['chat-image', 'audio']) {
    const { draftId } = await client.assets.createDraft()
    const imported = await client.assets.upload({
      draftId,
      name: purpose,
      mediaType: 'application/octet-stream',
      base64: Buffer.from(purpose).toString('base64'),
      purpose
    })
    const folder =
      purpose === 'chat-image'
        ? join(directory, 'app/assets/files')
        : join(directory, 'library/audio')
    expect(await readFile(join(folder, imported.asset.id), 'utf8')).toBe(purpose)
    await client.assets.discardDraft(draftId)
    expect((await client.assets.collectUnused({ resources: [imported.asset] })).deleted).toEqual([
      imported.asset
    ])
  }
  const modelFile = join(directory, 'model.ckpt')
  await writeFile(modelFile, 'model')
  await application.evaluate(({ dialog }, file) => {
    dialog.showOpenDialog = async () => ({ canceled: false, filePaths: [file] })
  }, modelFile)
  const imported = await page.evaluate(() => window.dsakikoHost.importFiles({ purpose: 'model' }))
  expect(await readFile(join(directory, 'library/models', imported[0].asset.id), 'utf8')).toBe(
    'model'
  )
  await verifyFormVoiceFiles(page, modelFile)
  const archivePath = join(directory, '跨端归档.dsakiko-chat.zip')
  await application.evaluate(({ dialog }, path) => {
    dialog.showSaveDialog = async () => ({ canceled: false, filePath: path })
    dialog.showOpenDialog = async () => ({ canceled: false, filePaths: [path] })
  }, archivePath)
  const sourceId = await client.conversations.createChat({
    title: '归档验收',
    characterIds: ['sakiko'],
    mode: 'single-character'
  })
  await page.getByRole('button', { name: '设置', exact: true }).click()
  const settingsDialog = page.getByRole('dialog', { name: '设置' })
  await settingsDialog.getByRole('button', { name: '对话导入 / 导出', exact: true }).click()
  await settingsDialog.getByRole('checkbox', { name: '归档验收', exact: true }).check()
  await settingsDialog.getByRole('button', { name: '导出所选 1 个对话…' }).click()
  await settingsDialog.getByText('已导出 1 个对话。', { exact: true }).waitFor()
  expect((await readFile(archivePath)).subarray(0, 2).toString()).toBe('PK')
  await settingsDialog.getByRole('tab', { name: '导入对话', exact: true }).click()
  await settingsDialog.getByRole('button', { name: '导入对话…' }).click()
  await settingsDialog.getByText('已导入 1 个对话。', { exact: true }).waitFor()
  const copies = (await client.conversations.listChats()).filter(
    (chat) => chat.title === '归档验收'
  )
  expect(copies).toHaveLength(2)
  expect(copies.find((chat) => chat.id !== sourceId)).toBeDefined()
  const sessionData = await application.evaluate(({ app }) => app.getPath('sessionData'))
  expect(sessionData).toBe(join(directory, 'cache/chromium'))
  await application.close()
  application = null
  await expect(
    fetch(bootstrap.connection.baseUrl + '/api/v1/call/settings/get', {
      method: 'POST',
      signal: AbortSignal.timeout(1000)
    })
  ).rejects.toThrow()
})
