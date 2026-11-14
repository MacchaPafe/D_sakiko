import { existsSync } from 'node:fs'
import { mkdir, mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { expect, test } from 'vitest'
import { _electron as electron } from 'playwright-core'

const root = fileURLToPath(new URL('../../', import.meta.url))

test.skipIf(!existsSync(join(root, 'resources/builtin-characters/manifest.json')))(
  '引导共用模型与画布，跨页保留角色和追踪；隐藏恢复及资源页启动恢复',
  async () => {
    const directory = await mkdtemp(join(tmpdir(), 'sakiko-welcome-test-'))
    const env = {
      ...process.env,
      DSAKIKO_DATA_DIR: join(directory, 'app'),
      DSAKIKO_RESOURCE_LIBRARY_DIR: join(directory, 'library'),
      DSAKIKO_CACHE_DIR: join(directory, 'cache')
    }
    delete env.ELECTRON_RUN_AS_NODE
    delete env.ELECTRON_RENDERER_URL
    delete env.DSAKIKO_RESOURCE_ROOT
    let application
    try {
      application = await electron.launch({
        args: [root, '--user-data-dir=' + join(directory, 'desktop')],
        env
      })
      const page = await application.firstWindow()
      const errors = []
      page.on('pageerror', (error) => errors.push(error.message))
      await application.evaluate(({ app, BrowserWindow }) => {
        app.show?.()
        BrowserWindow.getAllWindows()[0].show()
      })
      await page.waitForSelector('[data-character="sakiko"][data-preview-state="ready"]')
      expect(await page.getByRole('button', { name: '快速开始', exact: true }).isDisabled()).toBe(
        false
      )
      expect(
        await page.getByRole('button', { name: '从旧版本导入数据', exact: true }).isDisabled()
      ).toBe(true)
      expect(await page.locator('canvas').count()).toBe(1)

      // 仅在测试中读取实际送入 Cubism 的值，验证追踪不是静态图片或仅有鼠标事件。
      await page.evaluate(() => {
        const prototype = window.Live2DCubismCore.Model.prototype
        const update = prototype.update
        window.previewFrames = 0
        prototype.update = function recordPreviewParameters() {
          window.previewModel = this
          const parameters = this.parameters
          window.previewFocus = parameters.values[parameters.ids.indexOf('ParamEyeBallX')]
          window.previewFrames += 1
          return update.call(this)
        }
      })
      const screen = await page.context().newCDPSession(page)
      await screen.send('Emulation.setDeviceMetricsOverride', {
        width: 1440,
        height: 1024,
        deviceScaleFactor: 1,
        mobile: false
      })
      await page.mouse.move(30, 300)
      // 原生窗口失焦会按产品规则回正；先确认焦点再检查真实鼠标追踪。
      await application.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].focus())
      await page.bringToFront()
      await page.mouse.move(31, 300)
      await expect
        .poll(() => page.evaluate(() => window.previewFocus), { timeout: 3000 })
        .toBeLessThan(-0.2)
      const output = join(root, 'test-results/welcome')
      await mkdir(output, { recursive: true })
      await page.screenshot({ path: join(output, 'tracking-left.png'), scale: 'css' })
      await page.mouse.move(1300, 300)
      await expect.poll(() => page.evaluate(() => window.previewFocus)).toBeGreaterThan(0.2)
      await page.screenshot({ path: join(output, 'tracking-right.png'), scale: 'css' })
      await page.evaluate(() =>
        window.dispatchEvent(new PointerEvent('pointerout', { relatedTarget: null }))
      )
      await expect.poll(() => page.evaluate(() => Math.abs(window.previewFocus))).toBeLessThan(0.03)
      await page.screenshot({ path: join(output, 'welcome-desktop.png'), scale: 'css' })

      await page.evaluate(() => {
        window.testVisibility = []
        window.dsakikoHost.onWindowVisibility((visible) => window.testVisibility.push(visible))
      })
      await application.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].hide())
      await expect.poll(() => page.evaluate(() => window.testVisibility.at(-1))).toBe(false)
      await page.waitForTimeout(150)
      const frames = await page.evaluate(() => window.previewFrames)
      await page.waitForTimeout(150)
      expect(await page.evaluate(() => window.previewFrames)).toBe(frames)
      await application.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].show())
      await expect.poll(() => page.evaluate(() => window.previewFrames)).toBeGreaterThan(frames)

      for (const [id, name] of [
        ['anon', '爱音'],
        ['soyo', '素世'],
        ['sakiko', '祥子']
      ]) {
        await page.getByRole('button', { name: '预览' + name, exact: true }).click()
        await page.waitForSelector(`[data-character="${id}"][data-preview-state="ready"]`)
        expect(await page.locator('canvas').count()).toBe(1)
      }
      await screen.send('Emulation.setDeviceMetricsOverride', {
        width: 1024,
        height: 640,
        deviceScaleFactor: 1,
        mobile: false
      })
      expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(1024)
      expect(await page.evaluate(() => innerHeight)).toBe(640)
      await page.screenshot({ path: join(output, 'welcome-small.png'), scale: 'css' })

      await page.route('**/api/v1/previews/**', (route) =>
        route.fulfill({ status: 503, body: 'unavailable' })
      )
      await page.getByRole('button', { name: '预览爱音', exact: true }).click()
      await page.waitForSelector('[data-preview-state="error"]')
      await page.unroute('**/api/v1/previews/**')
      await page.getByRole('button', { name: '重试', exact: true }).click()
      await page.waitForSelector('[data-character="anon"][data-preview-state="ready"]')
      const state = await page.evaluate(async () => {
        const { baseUrl, token } = await window.dsakikoHost.getBackendConnection()
        const response = await fetch(baseUrl + '/api/v1/call/onboarding/getState', {
          method: 'POST',
          headers: { Authorization: 'Bearer ' + token, 'Content-Type': 'application/json' },
          body: JSON.stringify({ args: [] })
        })
        return (await response.json()).value
      })
      expect(state).toMatchObject({ stage: 'welcome', initialized: false })
      await page.evaluate(() => {
        window.retainedPreviewCanvas = document.querySelector('canvas')
        window.retainedPreviewModel = window.previewModel
      })
      const previewRequests = []
      page.on('request', (request) => {
        if (/\/previews\/|\/onboarding\/getBuiltinPreview/.test(request.url()))
          previewRequests.push(request.url())
      })
      await page.getByRole('button', { name: '快速开始', exact: true }).click()
      await page.getByRole('heading', { name: '添加语音功能' }).waitFor()
      await page.waitForSelector('[data-character="anon"][data-preview-state="ready"]')
      await application.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].focus())
      await page.bringToFront()
      await page.mouse.move(30, 250)
      await expect
        .poll(() => page.evaluate(() => window.previewFocus), { timeout: 3000 })
        .toBeLessThan(-0.2)
      await page.mouse.move(1000, 250)
      await expect.poll(() => page.evaluate(() => window.previewFocus)).toBeGreaterThan(0.2)
      expect(
        await page.evaluate(
          () =>
            document.querySelector('canvas') === window.retainedPreviewCanvas &&
            window.previewModel === window.retainedPreviewModel
        )
      ).toBe(true)
      await page.getByRole('button', { name: '上一步', exact: true }).click()
      expect(
        await page.getByRole('button', { name: '预览爱音' }).getAttribute('aria-pressed')
      ).toBe('true')
      await page.getByRole('button', { name: '快速开始', exact: true }).click()
      await page.getByRole('heading', { name: '添加语音功能' }).waitFor()
      expect(
        await page.evaluate(() => document.querySelector('canvas') === window.retainedPreviewCanvas)
      ).toBe(true)
      expect(previewRequests).toEqual([])
      expect(
        await page.getByRole('checkbox', { name: '语音转文字', exact: true }).isDisabled()
      ).toBe(true)
      expect(
        await page.getByRole('button', { name: '跳过，直接开始', exact: true }).isDisabled()
      ).toBe(true)
      await page.screenshot({ path: join(output, 'resources-small.png'), scale: 'css' })
      const bounds = await page.getByRole('button', { name: '上一步', exact: true }).boundingBox()
      expect(bounds.y + bounds.height).toBeLessThanOrEqual(640)
      await page.reload()
      await page.getByRole('heading', { name: '添加语音功能' }).waitFor()
      await screen.send('Emulation.setDeviceMetricsOverride', {
        width: 1440,
        height: 1024,
        deviceScaleFactor: 1,
        mobile: false
      })
      await page.waitForSelector('[data-preview-state="ready"]')
      await page.screenshot({ path: join(output, 'resources-desktop.png'), scale: 'css' })
      expect(errors).toEqual([])
    } finally {
      await application?.close()
      await rm(directory, { recursive: true, force: true })
    }
  },
  60000
)
