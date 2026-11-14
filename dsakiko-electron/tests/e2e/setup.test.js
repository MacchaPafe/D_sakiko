import { createHash } from 'node:crypto'
import { existsSync } from 'node:fs'
import { mkdir, mkdtemp, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { expect, test } from 'vitest'
import { _electron as electron } from 'playwright-core'
import { createCoreBackend } from '../../src/backend/create-core-backend.js'
import { startHttpServer } from '../../src/server/http-server.js'

const root = fileURLToPath(new URL('../../', import.meta.url))

/** 小型可控下载源；走正式下载、哈希校验、文件登记、HTTP/WS，不执行真实模型推理。 */
function resourceFixture() {
  const payload = Buffer.alloc(512 * 1024, 5)
  const file = {
    source: 'gsv',
    path: 'chinese-roberta-wwm-ext-large/config.json',
    sizeBytes: payload.length,
    sha256: createHash('sha256').update(payload).digest('hex')
  }
  const component = {
    id: 'gsv.models',
    kind: 'gsv',
    version: 'test',
    available: true,
    requires: [],
    files: [file]
  }
  const control = { fail: false, requests: 0 }
  const catalog = {
    platform: 'darwin',
    arch: 'arm64',
    components: async () => [component],
    select: async (selection) =>
      selection.includes('gsv')
        ? [component]
        : [
            {
              id: 'test.unavailable',
              kind: 'runtime',
              available: false,
              files: [],
              unavailableReason: '测试源未提供该资源'
            }
          ]
  }
  async function fetchResource(_url, { signal, headers }) {
    control.requests += 1
    if (control.fail) return new Response('unavailable', { status: 503 })
    const offset = Number(headers.Range?.match(/bytes=(\d+)-/)?.[1] ?? 0)
    let position = offset
    const body = new ReadableStream({
      async pull(stream) {
        await new Promise((resolve) => setTimeout(resolve, 80))
        if (signal.aborted) {
          stream.error(signal.reason)
          return
        }
        if (position === payload.length) {
          stream.close()
          return
        }
        const end = Math.min(payload.length, position + 16384)
        stream.enqueue(payload.subarray(position, end))
        position = end
      }
    })
    return new Response(body, {
      status: offset ? 206 : 200,
      headers: offset
        ? {
            'content-range': `bytes ${offset}-${payload.length - 1}/${payload.length}`
          }
        : {}
    })
  }
  return { catalog, control, fetchResource }
}

test.skipIf(!existsSync(join(root, 'resources/builtin-characters/manifest.json')))(
  '快速开始、真实下载传输、暂停、重启继续、失败重试与完成；不进入主页',
  async () => {
    const directory = await mkdtemp(join(tmpdir(), 'sakiko-setup-ui-'))
    const roots = {
      appDataRoot: join(directory, 'app'),
      resourceLibraryRoot: join(directory, 'library'),
      cacheRoot: join(directory, 'cache')
    }
    const source = resourceFixture()
    let backend
    let server
    let application
    const hostFile = join(directory, 'host.cjs')
    await writeFile(
      hostFile,
      `
      const {app,BrowserWindow,ipcMain}=require('electron');
      app.whenReady().then(async()=>{
        ipcMain.handle('host:backend-connection',()=>JSON.parse(process.env.SETUP_TEST_CONNECTION));
        ipcMain.handle('host:get-window-visibility',()=>true);
        const window=new BrowserWindow({width:1360,height:860,webPreferences:{preload:process.env.SETUP_TEST_PRELOAD,contextIsolation:true,nodeIntegration:false,sandbox:true}});
        await window.loadFile(process.env.SETUP_TEST_RENDERER);
        app.show?.();window.show();
      });
      app.on('window-all-closed',()=>app.quit());
    `
    )
    async function launch() {
      backend = await createCoreBackend({
        ...roots,
        scanReminders: false,
        builtinDirectory: join(root, 'resources/builtin-characters'),
        setupOptions: { catalog: source.catalog },
        fetch: source.fetchResource
      })
      server = await startHttpServer(backend, { origins: ['null', 'file://'] })
      const env = {
        ...process.env,
        SETUP_TEST_CONNECTION: JSON.stringify({ baseUrl: server.baseUrl, token: server.token }),
        SETUP_TEST_PRELOAD: join(root, 'out/preload/index.cjs'),
        SETUP_TEST_RENDERER: join(root, 'out/renderer/index.html')
      }
      delete env.ELECTRON_RUN_AS_NODE
      application = await electron.launch({
        args: [hostFile, '--user-data-dir=' + join(directory, 'desktop')],
        env
      })
      return application.firstWindow()
    }
    try {
      let page = await launch()
      await page.getByRole('button', { name: '预览素世', exact: true }).click()
      await page.waitForSelector('[data-character="soyo"][data-preview-state="ready"]')
      await page.evaluate(() => {
        window.retainedPreviewCanvas = document.querySelector('canvas')
      })
      await page.getByRole('button', { name: '快速开始', exact: true }).click()
      await page.getByRole('heading', { name: '添加语音功能' }).waitFor()
      await expect
        .poll(() => page.getByRole('checkbox', { name: 'GPT-SoVITS', exact: true }).isEnabled())
        .toBe(true)
      const estimate = page.locator('.setup-estimate')
      const value = page.locator('.setup-estimate-value')
      await expect.poll(() => estimate.getAttribute('aria-busy')).toBe('false')
      const estimateBounds = await estimate.boundingBox()
      let releaseEstimate
      const heldEstimate = new Promise((resolve) => {
        releaseEstimate = resolve
      })
      let estimateMode = 'hold'
      await page.route('**/call/setup/getCatalog', async (route) => {
        if (estimateMode === 'fail') return route.abort('failed')
        if (estimateMode === 'hold') await heldEstimate
        return route.continue()
      })
      await page.getByRole('checkbox', { name: 'GPT-SoVITS', exact: true }).check()
      await expect.poll(() => estimate.getAttribute('aria-busy')).toBe('true')
      expect(await value.innerText()).toBe('0.00 MB')
      await page.locator('.setup-estimate-updating').waitFor({ state: 'visible' })
      expect(await estimate.boundingBox()).toEqual(estimateBounds)
      estimateMode = 'pass'
      releaseEstimate()
      await expect.poll(() => value.innerText()).toBe('524.29 KB')
      await expect.poll(() => estimate.getAttribute('aria-busy')).toBe('false')
      expect(await estimate.boundingBox()).toEqual(estimateBounds)
      estimateMode = 'fail'
      await page.getByRole('button', { name: '检查资源更新' }).click()
      await expect.poll(() => value.innerText()).toBe('核对失败')
      expect(await page.getByRole('button', { name: '下载并继续' }).isDisabled()).toBe(true)
      expect(await estimate.boundingBox()).toEqual(estimateBounds)
      estimateMode = 'pass'
      await page.getByRole('button', { name: '检查资源更新' }).click()
      await expect.poll(() => value.innerText()).toBe('524.29 KB')
      await page.getByRole('button', { name: '下载并继续' }).click()
      await expect
        .poll(
          async () => Number(await page.getByRole('progressbar').getAttribute('aria-valuenow')),
          { timeout: 5000 }
        )
        .toBeGreaterThan(0)
      const output = join(root, 'test-results/setup')
      await mkdir(output, { recursive: true })
      await page.screenshot({ path: join(output, 'downloading.png') })
      await page.getByRole('button', { name: '暂停下载' }).click()
      await page.getByRole('heading', { name: '语音资源准备已暂停' }).waitFor()
      expect(
        await page.evaluate(() => document.querySelector('canvas') === window.retainedPreviewCanvas)
      ).toBe(true)
      await page.waitForSelector('[data-character="soyo"][data-preview-state="ready"]')
      const id = (await backend.setup.listJobs())[0].id
      await application.close()
      application = null
      await server.close()
      server = null
      await backend.close()
      backend = null
      const requests = source.control.requests
      page = await launch()
      await page.getByRole('heading', { name: '语音资源准备已暂停' }).waitFor()
      expect(source.control.requests).toBe(requests)
      expect((await backend.setup.listJobs())[0].id).toBe(id)
      source.control.fail = true
      await page.getByRole('button', { name: '继续下载', exact: true }).click()
      await page.getByRole('heading', { name: '语音资源准备遇到问题' }).waitFor()
      await page.screenshot({ path: join(output, 'failed.png') })
      source.control.fail = false
      await page.getByRole('button', { name: '重试', exact: true }).click()
      await page.getByRole('heading', { name: '资源准备完成', exact: true }).waitFor()
      expect(await page.getByRole('button', { name: '开始使用', exact: true }).isDisabled()).toBe(
        true
      )
      expect((await backend.setup.getJob(id)).download.bytes).toBe(512 * 1024)
      expect((await backend.onboarding.getState()).initialized).toBe(false)
      expect(await backend.characters.list()).toHaveLength(3)
      await page.screenshot({ path: join(output, 'complete.png') })
      await page.getByRole('button', { name: '返回资源选择' }).click()
      await page.getByRole('heading', { name: '添加语音功能' }).waitFor()
    } catch (error) {
      console.log('失败时任务快照', await backend?.setup.listJobs())
      console.log(
        '失败时页面',
        await application?.firstWindow().then((page) => page.locator('body').innerText())
      )
      throw error
    } finally {
      await application?.close()
      await server?.close()
      await backend?.close()
      await rm(directory, { recursive: true, force: true })
    }
  },
  60000
)
