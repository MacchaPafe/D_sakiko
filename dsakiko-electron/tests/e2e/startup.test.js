import { mkdtemp, rm, mkdir } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { afterAll, beforeAll, expect, test } from 'vitest'
import { _electron as electron } from 'playwright-core'

const projectRoot = fileURLToPath(new URL('../../', import.meta.url))
let application
let temporaryData

beforeAll(async function launchApplication() {
  temporaryData = await mkdtemp(join(tmpdir(), 'dsakiko-electron-test-'))
  const environment = { ...process.env }
  // 测试必须启动真实 Electron，不能继承宿主的 Node 模式开关。
  delete environment.ELECTRON_RUN_AS_NODE
  delete environment.ELECTRON_RENDERER_URL
  application = await electron.launch({
    args: [projectRoot, '--user-data-dir=' + temporaryData],
    env: environment
  })
})

afterAll(async function closeApplication() {
  if (application) await application.close()
  if (temporaryData) await rm(temporaryData, { recursive: true, force: true })
})

test('桌面窗口加载 React、受控 IPC 和 shadcn 组件', async function verifyStartup() {
  const page = await application.firstWindow()
  const errors = []
  page.on('pageerror', (error) => errors.push(error.message))
  await page.getByRole('heading', { name: '数字小祥' }).waitFor()
  await page.locator('[role="status"][data-status="ready"]').waitFor()
  expect(await page.title()).toBe('数字小祥')

  const isolation = await page.evaluate(async function inspectBridge() {
    return {
      nodeAvailable: typeof window.require !== 'undefined',
      exposedMethods: Object.keys(window.dsakiko),
      runtime: await window.dsakiko.getRuntimeInfo()
    }
  })
  expect(isolation).toEqual({
    nodeAvailable: false,
    exposedMethods: ['getRuntimeInfo'],
    runtime: { status: 'ready' }
  })

  const initiallyDark = await page
    .locator('html')
    .evaluate((element) => element.classList.contains('dark'))
  await page.getByRole('button', { name: /切换到.*外观/ }).click()
  await page.waitForFunction(
    (previous) => document.documentElement.classList.contains('dark') !== previous,
    initiallyDark
  )
  expect(errors).toEqual([])

  await mkdir(join(projectRoot, 'test-results'), { recursive: true })
  await page.screenshot({ path: join(projectRoot, 'test-results/startup.png') })
})
