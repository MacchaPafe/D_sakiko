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
    args: [fileURLToPath(new URL('../../', import.meta.url))],
    env
  }
  if (process.env.DSAKIKO_TEST_EXECUTABLE) {
    launchOptions.executablePath = process.env.DSAKIKO_TEST_EXECUTABLE
    // 指定打包程序时，Playwright 不会自动注入启动协调脚本。
    const loader = fileURLToPath(
      new URL('./lib/server/electron/loader.js', import.meta.resolve('playwright-core'))
    )
    launchOptions.args = ['-r', loader]
  }
  application = await electron.launch(launchOptions)
  const page = await application.firstWindow()
  // 旧页面仍可能报告缺少原型桥；此测试只验收真实 preload、IPC 和业务传输，不冒充页面验收。
  await page.waitForFunction(() => Boolean(window.dsakikoHost))
  const bootstrap = await page.evaluate(async () => ({
    connection: await window.dsakikoHost.getBackendConnection(),
    runtime: await window.dsakikoHost.getRuntimeInfo(),
    methods: Object.keys(window.dsakikoHost),
    nodeVisible: typeof window.require !== 'undefined'
  }))
  expect(bootstrap.methods.sort()).toEqual([
    'getBackendConnection',
    'getRuntimeInfo',
    'importFiles'
  ])
  expect(bootstrap.nodeVisible).toBe(false)
  expect(bootstrap.runtime.platform).toBe(process.platform)
  expect(bootstrap.connection.baseUrl).toMatch(/^http:\/\/127\.0\.0\.1:\d+$/)
  client = createBackendClient(bootstrap.connection)
  const settings = await client.settings.get()
  expect(settings).not.toHaveProperty('playback')
  const saved = await client.settings.save({
    ...settings,
    display: { ...settings.display, theme: 'dark' }
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
    const imported = await client.assets.upload({
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
