import { mkdtemp, rm } from 'node:fs/promises'
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
  const env = { ...process.env, DSAKIKO_DATA_DIR: directory }
  delete env.ELECTRON_RUN_AS_NODE
  delete env.ELECTRON_RENDERER_URL
  application = await electron.launch({
    args: [fileURLToPath(new URL('../../', import.meta.url))],
    env
  })
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
  await application.close()
  application = null
  await expect(
    fetch(bootstrap.connection.baseUrl + '/api/v1/call/settings/get', {
      method: 'POST',
      signal: AbortSignal.timeout(1000)
    })
  ).rejects.toThrow()
})
