import { existsSync } from 'node:fs'
import { mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { expect, test } from 'vitest'
import { _electron as electron } from 'playwright-core'

const root = fileURLToPath(new URL('../../', import.meta.url))

async function call(page, target, method, args = []) {
  return page.evaluate(
    async ({ target, method, args }) => {
      const { baseUrl, token } = await window.dsakikoHost.getBackendConnection()
      const response = await fetch(`${baseUrl}/api/v1/call/${target}/${method}`, {
        method: 'POST',
        headers: { Authorization: 'Bearer ' + token, 'Content-Type': 'application/json' },
        body: JSON.stringify({ args })
      })
      const result = await response.json()
      if (!response.ok) throw new Error(JSON.stringify(result))
      return result.value
    },
    { target, method, args }
  )
}

test.skipIf(!existsSync(join(root, 'resources/builtin-characters/manifest.json')))(
  '旧根包含新版数据时真实目录授权、扫描、四类选择、告警结果及资源页恢复；来源不修改，舞台不重建',
  async () => {
    const directory = await mkdtemp(join(tmpdir(), 'sakiko-legacy-ui-'))
    const source = join(directory, '旧版程序')
    const files = {
      'live2d_related/anon/name.txt': '爱音',
      'live2d_related/anon/character_description.txt': '导入验证的人设',
      'live2d_related/anon/live2D_model/3.model.json': '{}',
      'live2d_related/anon/live2D_model/model.moc': 'fixture',
      'd_sakiko_config.json': JSON.stringify({
        llm_setting: {
          llm_api_provider: 'deepseek',
          llm_api_model: 'deepseek/deepseek-chat',
          llm_api_key: 'fixture-only'
        }
      }),
      'reference_audio/all_conversation.json': JSON.stringify({
        chat_list: [
          {
            chat_id: 'legacy-test',
            name: '导入的旧聊天',
            type: 1,
            prompt_config: { character_name: '爱音' },
            message_list: [
              { character_name: 'User', text: '你好' },
              {
                character_name: '爱音',
                text: '你好呀',
                audio_path: '../reference_audio/missing.wav'
              }
            ]
          }
        ]
      })
    }
    for (const [name, content] of Object.entries(files)) {
      await mkdir(dirname(join(source, name)), { recursive: true })
      await writeFile(join(source, name), content)
    }
    const env = {
      ...process.env,
      DSAKIKO_DATA_DIR: join(source, 'dsakiko-electron/.local/core-v1'),
      DSAKIKO_RESOURCE_LIBRARY_DIR: join(source, 'dsakiko-electron/.local/library'),
      DSAKIKO_CACHE_DIR: join(source, 'dsakiko-electron/.local/cache')
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
      await page.emulateMedia({ reducedMotion: 'no-preference' })
      const errors = []
      page.on('pageerror', (error) => errors.push(error.message))
      await application.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].show())
      await page.waitForSelector('[data-preview-state="ready"]')
      await page.getByRole('button', { name: '预览素世', exact: true }).click()
      await page.waitForSelector('[data-character="soyo"][data-preview-state="ready"]')
      await page.evaluate(() => {
        window.legacyCanvas = document.querySelector('canvas')
      })
      const screen = await page.context().newCDPSession(page)
      await screen.send('Emulation.setDeviceMetricsOverride', {
        width: 1440,
        height: 1024,
        deviceScaleFactor: 1,
        mobile: false
      })
      const output = join(root, 'test-results/legacy')
      await mkdir(output, { recursive: true })
      await page.getByRole('button', { name: '快速开始', exact: true }).click()
      await page.getByRole('heading', { name: '添加语音功能', exact: true }).waitFor()
      expect(await call(page, 'characters', 'list')).toEqual([])
      expect(await call(page, 'settings', 'listConnections')).toEqual([])
      await page.getByRole('button', { name: '上一步', exact: true }).click()
      await page.getByRole('button', { name: '从旧版本导入数据', exact: true }).waitFor()
      expect(await call(page, 'onboarding', 'getState')).toMatchObject({
        path: null,
        stage: 'welcome'
      })
      await page.getByRole('button', { name: '从旧版本导入数据', exact: true }).click()
      await page.getByRole('heading', { name: '导入旧版数据', exact: true }).waitFor()
      await page.screenshot({ path: join(output, 'directory.png'), scale: 'css' })
      expect(await page.getByRole('button', { name: '开始导入', exact: true }).isDisabled()).toBe(
        true
      )
      // 只替代系统对话框的选中结果，仍经过正式 IPC 授权及 HTTP/WS 后端。
      await application.evaluate(({ dialog }, source) => {
        dialog.showOpenDialog = async () => ({ canceled: false, filePaths: [source] })
      }, source)
      await page.evaluate(() => {
        const frame = document.querySelector('.legacy-directory-frame')
        window.directoryHeights = [frame.getBoundingClientRect().height]
        window.legacyAnimations = []
        const observer = new ResizeObserver(([entry]) => {
          window.directoryHeights.push(entry.contentRect.height)
        })
        observer.observe(frame)
        const scroll = document.querySelector('.setup-scroll')
        scroll.addEventListener('animationstart', (event) => {
          if (event.target.matches('.legacy-scan-results'))
            window.legacyAnimations.push(event.animationName)
        })
      })
      await page.getByRole('button', { name: '选择文件夹', exact: true }).click()
      await page.getByText('成功识别旧版程序目录', { exact: true }).waitFor()
      await expect
        .poll(() => page.evaluate(() => window.legacyAnimations))
        .toEqual(['legacy-results-enter'])
      await page.locator('.legacy-directory-frame').evaluate(async (frame) => {
        await Promise.all(frame.getAnimations().map((animation) => animation.finished))
      })
      const heights = await page.evaluate(() => window.directoryHeights)
      expect(heights.at(-1)).toBeLessThan(heights[0] - 40)
      expect(heights.some((height) => height < heights[0] - 5 && height > heights.at(-1) + 5)).toBe(
        true
      )
      expect(
        await page.getByRole('checkbox', { name: '角色与人设', exact: true }).isChecked()
      ).toBe(true)
      expect(await page.getByRole('checkbox', { name: '聊天记录', exact: true }).isChecked()).toBe(
        true
      )
      expect(
        await page.getByRole('checkbox', { name: '大模型连接', exact: true }).isChecked()
      ).toBe(true)
      expect(
        await page.getByRole('checkbox', { name: 'GPT-SoVITS 基础资源', exact: true }).isDisabled()
      ).toBe(true)
      await page.screenshot({ path: join(output, 'selection-desktop.png'), scale: 'css' })
      await application.evaluate(({ dialog }) => {
        dialog.showOpenDialog = async () => ({ canceled: true, filePaths: [] })
      })
      await page.getByRole('button', { name: '更换文件夹', exact: true }).click()
      await expect
        .poll(() => page.getByRole('button', { name: '开始导入', exact: true }).isEnabled())
        .toBe(true)
      await screen.send('Emulation.setDeviceMetricsOverride', {
        width: 1024,
        height: 640,
        deviceScaleFactor: 1,
        mobile: false
      })
      expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(1024)
      const bounds = await page.getByRole('button', { name: '开始导入', exact: true }).boundingBox()
      expect(bounds.y + bounds.height).toBeLessThanOrEqual(640)
      await page.screenshot({ path: join(output, 'selection-small.png'), scale: 'css' })
      await page.getByRole('button', { name: '开始导入', exact: true }).click()
      await page.getByRole('heading', { name: '导入部分完成', exact: true }).waitFor()
      expect(await page.locator('.legacy-warnings').count()).toBe(1)
      expect(
        await page.evaluate(() => window.legacyCanvas === document.querySelector('canvas'))
      ).toBe(true)
      await page.waitForSelector('[data-character="soyo"][data-preview-state="ready"]')
      await page.screenshot({ path: join(output, 'result-small.png'), scale: 'css' })
      expect(await call(page, 'onboarding', 'getState')).toMatchObject({
        path: 'legacy',
        stage: 'importing',
        initialized: false
      })
      expect((await call(page, 'characters', 'get', ['anon'])).forms[0].description).toBe(
        '导入验证的人设'
      )
      await page.getByRole('button', { name: '继续', exact: true }).click()
      await page.getByRole('heading', { name: '添加语音功能', exact: true }).waitFor()
      expect(await page.getByRole('button', { name: '上一步', exact: true }).count()).toBe(0)
      expect(
        await page.getByRole('button', { name: '跳过，直接开始', exact: true }).isDisabled()
      ).toBe(false)
      expect(
        await page.evaluate(() => window.legacyCanvas === document.querySelector('canvas'))
      ).toBe(true)
      await page.reload()
      await page.getByRole('heading', { name: '添加语音功能', exact: true }).waitFor()
      expect(await call(page, 'onboarding', 'getState')).toMatchObject({
        stage: 'optional-resources',
        initialized: false
      })
      for (const [name, content] of Object.entries(files))
        expect(await readFile(join(source, name), 'utf8')).toBe(content)
      await page.getByRole('button', { name: '跳过，直接开始', exact: true }).click()
      await page.getByRole('main', { name: '数字小祥主页' }).waitFor()
      expect(await call(page, 'onboarding', 'getState')).toMatchObject({ initialized: true })
      await page.screenshot({ path: join(output, 'home.png'), scale: 'css' })
      expect(errors).toEqual([])
    } finally {
      await application?.close()
      await rm(directory, { recursive: true, force: true })
    }
  },
  60000
)
