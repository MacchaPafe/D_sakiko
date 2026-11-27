import { mkdtemp, rm, copyFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { expect, test } from 'vitest'
import { _electron as electron } from 'playwright-core'
import { createBackendClient } from '../../src/renderer/src/backend/client.js'

test('本地多选导入、后台预览、直接应用与移除后保留对话背景', async () => {
  const root = fileURLToPath(new URL('../../', import.meta.url))
  const directory = await mkdtemp(join(tmpdir(), 'sakiko-background-ui-'))
  const env = {
    ...process.env,
    DSAKIKO_DATA_DIR: join(directory, 'app'),
    DSAKIKO_RESOURCE_LIBRARY_DIR: join(directory, 'library'),
    DSAKIKO_CACHE_DIR: join(directory, 'cache')
  }
  delete env.ELECTRON_RUN_AS_NODE
  delete env.ELECTRON_RENDERER_URL
  let application, client
  try {
    application = await electron.launch({
      args: [root, '--user-data-dir=' + join(directory, 'desktop')],
      env
    })
    const page = await application.firstWindow()
    page.setDefaultTimeout(8000)
    const errors = []
    page.on('pageerror', (error) => errors.push(error.message))
    await page.route('**/api/v1/call/backgrounds/catalog', (route) =>
      route.fulfill({ json: { value: [] } })
    )
    await page.getByRole('button', { name: '快速开始', exact: true }).click()
    await page.getByRole('button', { name: '跳过，直接开始', exact: true }).click()
    await page.getByRole('main', { name: '数字小祥主页' }).waitFor()
    client = createBackendClient(
      await page.evaluate(() => window.dsakikoHost.getBackendConnection())
    )
    const chatId = await client.conversations.createChat({
      title: '背景验收',
      mode: 'single-character',
      characterIds: ['sakiko']
    })
    await page.getByRole('button', { name: '暂无回复', exact: true }).click()

    const expand = page.getByRole('button', { name: '全窗口舞台' })
    await expand.click()
    const rail = page.getByRole('navigation', { name: '剧情舞台工具栏' })
    await rail.getByRole('button', { name: '展开工具栏' }).click()
    const automatic = rail.getByRole('button', { name: '自动', exact: true })
    const autoColor = () => automatic.evaluate((button) => getComputedStyle(button).backgroundColor)
    expect(await automatic.getAttribute('aria-pressed')).toBe('true')
    expect(await autoColor()).toBe('rgb(56, 71, 102)')
    await automatic.hover()
    expect(await autoColor()).toBe('rgb(71, 89, 121)')
    await rail.getByRole('button', { name: '退出', exact: true }).click()

    const directoryFiles = [join(directory, 'classroom.png'), join(directory, 'hall.png')]
    for (const filename of directoryFiles)
      await copyFile(join(root, 'resources/icon.png'), filename)
    await application.evaluate(({ dialog }, files) => {
      dialog.showOpenDialog = async () => ({ canceled: false, filePaths: files })
    }, directoryFiles)
    const trigger = page.getByRole('button', { name: '切换背景', exact: true })
    const picker = page.getByRole('dialog', { name: '切换舞台背景' })
    const manager = page.getByRole('dialog', { name: '舞台资源' })
    const image = page.locator('.home-stage > .stage-backdrop')
    const preview = page.locator('.stage-background-preview[data-pending="false"] .stage-backdrop')
    const builtinName = '默认背景 · 内置'
    const builtinTile = picker.getByRole('button', { name: builtinName, exact: true })
    const builtinCard = manager.getByRole('article').filter({ hasText: builtinName })
    const saved = async () =>
      (await client.conversations.getSnapshot(chatId)).chat.meta.settings.presentation.background
    await trigger.click()
    expect(await builtinTile.getAttribute('aria-pressed')).toBe('true')
    expect(await picker.locator('[data-background-option]').first().textContent()).toBe(builtinName)
    const bundledUrl = await builtinTile.locator('img').evaluate((img) => img.src)
    expect(
      await page.locator('.home-stage').evaluate((el) => getComputedStyle(el).backgroundImage)
    ).toContain(bundledUrl)
    await picker.getByRole('searchbox').fill('classroom')
    await picker.getByRole('button', { name: '管理背景' }).click()
    await manager.getByRole('tab', { name: '已下载', exact: true }).click()
    expect(await manager.getByRole('article').first().innerText()).toContain(builtinName)
    expect(await builtinCard.getByRole('button').allTextContents()).toEqual(['应用'])
    await manager.getByRole('button', { name: '导入图片' }).click()
    const card = manager.getByRole('article').filter({ hasText: 'classroom' })
    const rename = card.getByRole('button', { name: '改名' })
    await rename.waitFor()
    const originalHeight = (await card.boundingBox()).height
    await rename.click()
    const nameInput = page.getByRole('textbox', { name: '背景名称' })
    const editingCard = manager.getByRole('article').filter({ has: nameInput })
    expect(await editingCard.locator('strong').count()).toBe(0)
    expect((await editingCard.boundingBox()).height).toBe(originalHeight)
    expect(await nameInput.evaluate((input) => [input.selectionStart, input.selectionEnd])).toEqual(
      [0, 'classroom'.length]
    )
    await nameInput.fill('临时名称')
    await nameInput.press('Escape')
    expect(await rename.evaluate((button) => document.activeElement === button)).toBe(true)
    await rename.click()
    await nameInput.fill('教室')
    await nameInput.press('Enter')
    await manager.getByText('教室', { exact: true }).waitFor()
    await manager.getByRole('tab', { name: '服装', exact: true }).click()
    await manager.getByRole('tab', { name: '背景', exact: true }).click()
    await manager.getByText('教室', { exact: true }).waitFor()
    await manager.getByRole('button', { name: '关闭舞台资源' }).click()
    expect(await picker.getByRole('searchbox').inputValue()).toBe('classroom')
    await rm(directoryFiles[0])
    await picker.getByRole('searchbox').fill('')
    const classroomTile = picker.getByRole('button', { name: '教室', exact: true })
    const hallTile = picker.getByRole('button', { name: 'hall', exact: true })
    await expect
      .poll(() => hallTile.locator('img').evaluate((img) => img.naturalWidth))
      .toBeGreaterThan(0)
    await classroomTile.hover()
    await preview.waitFor()
    const classroomUrl = await preview.getAttribute('src')
    const classroomBox = await classroomTile.boundingBox()
    const builtinBox = await builtinTile.boundingBox()
    await page.mouse.move(
      (builtinBox.x + builtinBox.width + classroomBox.x) / 2,
      classroomBox.y + 20
    )
    expect(await preview.getAttribute('src')).toBe(classroomUrl)

    // 模拟下一张原图尚未载入，间隙和等待期间都保留上一张已解码的图。
    const imageRoute = /\/api\/v1\/assets\/[^/]+$/
    const requestStarted = Promise.withResolvers()
    const releaseNextPicture = Promise.withResolvers()
    const delayNextPicture = async (route) => {
      requestStarted.resolve()
      await releaseNextPicture.promise
      await route.continue()
    }
    await page.route(imageRoute, delayNextPicture)
    try {
      await hallTile.hover()
      await requestStarted.promise
      expect(await preview.getAttribute('src')).toBe(classroomUrl)
    } finally {
      releaseNextPicture.resolve()
      await page.unroute(imageRoute, delayNextPicture)
    }
    await expect.poll(() => preview.getAttribute('src')).not.toBe(classroomUrl)

    const settingsButton = page.getByRole('button', { name: '设置', exact: true })
    await settingsButton.click()
    const settings = page.getByRole('dialog', { name: '设置' })
    await settings.waitFor()
    const coveredPicker = page.locator('.background-picker')
    expect(await coveredPicker.evaluate((panel) => panel.inert)).toBe(true)
    await page.mouse.move(classroomBox.x + classroomBox.width / 2, classroomBox.y + 20)
    await page.waitForTimeout(400)
    expect(await preview.count()).toBe(0)
    await page.keyboard.press('Escape')
    await picker.waitFor()
    await page.waitForTimeout(400)
    expect(await preview.count()).toBe(0)
    await hallTile.focus()
    await preview.waitFor()
    expect(await saved()).toBeNull()
    await page.keyboard.press('Escape')
    await expect.poll(() => preview.count()).toBe(0)
    await picker.waitFor({ state: 'hidden' })
    await trigger.click()
    await picker.getByRole('button', { name: '管理背景' }).click()
    await manager.getByRole('tab', { name: '已下载', exact: true }).click()
    const room = manager.getByRole('article').filter({ hasText: '教室' })
    await room.getByRole('button', { name: '应用', exact: true }).click()
    await manager.waitFor({ state: 'hidden' })
    await picker.waitFor({ state: 'hidden' })
    const asset = await saved()
    expect(asset?.id).toBeTruthy()
    // 完整对话的首条快照延后交付，舞台仍应先显示已保存的自定义背景。
    const resumeSnapshots = Promise.withResolvers()
    const firstSnapshot = Promise.withResolvers()
    await page.routeWebSocket(/\/api\/v1\/events\?/, (socket) => {
      const server = socket.connectToServer()
      server.onMessage(async (message) => {
        const event = JSON.parse(message.toString())
        if (event.snapshot?.chat?.id === chatId) {
          firstSnapshot.resolve()
          await resumeSnapshots.promise
        }
        socket.send(message)
      })
    })
    // 从第一帧观察重载，资源加载期间也不应画出内置背景。
    await page.addInitScript(() => {
      window.stageBackgrounds = []
      function sample() {
        const el = document.querySelector('.home-stage')
        if (el) window.stageBackgrounds.push(getComputedStyle(el).backgroundImage)
        if (!el?.querySelector('img.stage-backdrop')?.naturalWidth) requestAnimationFrame(sample)
      }
      requestAnimationFrame(sample)
    })
    const home = page.locator('.home-page')
    const cover = page.locator('.setup-loading[data-covering-home]')
    let releaseBackground = Promise.withResolvers()
    await page.route(
      (url) => /^\/api\/v1\/assets\/[^/]+$/.test(url.pathname),
      async (route) => {
        if (route.request().resourceType() === 'image') await releaseBackground.promise
        await route.continue()
      }
    )
    const modelRequested = Promise.withResolvers()
    const releaseModel = Promise.withResolvers()
    await page.route(/\/api\/v1\/assets\/[^/]+\/.+\.model3?\.json$/, async (route) => {
      modelRequested.resolve()
      await releaseModel.promise
      await route.continue()
    })
    const clockStart = Date.now()
    await page.clock.install({ time: clockStart })
    await page.clock.pauseAt(clockStart + 1000)
    await page.reload({ waitUntil: 'domcontentloaded' })
    await cover.waitFor({ state: 'attached' })
    expect(await home.evaluate((element) => element.inert)).toBe(true)
    await modelRequested.promise
    await page.clock.runFor(32)
    releaseBackground.resolve()
    await expect
      .poll(() => image.evaluateAll((images) => images.some((img) => img.naturalWidth > 0)))
      .toBe(true)
    expect(await page.evaluate(() => [...new Set(window.stageBackgrounds)])).toEqual(['none'])
    expect(await cover.count()).toBe(1)
    releaseModel.resolve()
    await cover.waitFor({ state: 'detached' })
    expect(await home.evaluate((element) => element.inert)).toBe(false)
    await firstSnapshot.promise
    resumeSnapshots.resolve()

    // 背景请求仍在等待时，达到上限也要让主页可操作，随后继续完成图片加载。
    releaseBackground = Promise.withResolvers()
    await page.reload({ waitUntil: 'domcontentloaded' })
    await cover.waitFor({ state: 'attached' })
    await page.clock.runFor(599)
    expect(await cover.count()).toBe(1)
    await page.clock.runFor(1)
    await cover.waitFor({ state: 'detached' })
    expect(await home.evaluate((element) => element.inert)).toBe(false)
    releaseBackground.resolve()
    await page.clock.resume()
    await expect.poll(() => image.evaluate((img) => img.naturalWidth)).toBeGreaterThan(0)

    await trigger.click()
    await image.waitFor({ state: 'visible' })
    await builtinTile.hover()
    await picker.getByRole('heading').hover()
    await page.waitForTimeout(500)
    expect(await preview.count()).toBe(0)
    await builtinTile.hover()
    await preview.waitFor({ timeout: 1000 })
    expect(await preview.getAttribute('src')).toBe(bundledUrl)
    expect(await saved()).toEqual(asset)
    await picker.getByRole('heading').hover()
    await preview.waitFor({ state: 'hidden' })
    await builtinTile.focus()
    await preview.waitFor()
    await builtinTile.press('Enter')
    await expect.poll(saved).toBeNull()
    await picker.waitFor({ state: 'hidden' })
    await trigger.click()
    await picker.getByRole('button', { name: '教室', exact: true }).click()
    await expect.poll(saved).toEqual(asset)
    await picker.waitFor({ state: 'hidden' })
    await trigger.click()
    await picker.getByRole('button', { name: '管理背景' }).click()
    await manager.getByRole('tab', { name: '已下载', exact: true }).click()
    await room.getByRole('button', { name: '移除', exact: true }).click()
    await room.getByRole('button', { name: '确认移除', exact: true }).click()
    await room.waitFor({ state: 'hidden' })
    await manager.getByRole('button', { name: '关闭舞台资源' }).click()
    expect(await saved()).toEqual(asset)
    expect(await image.evaluate((img) => img.naturalWidth)).toBeGreaterThan(0)
    await picker.getByRole('button', { name: '管理背景' }).click()
    await manager.getByRole('tab', { name: '已下载', exact: true }).click()
    await builtinCard.hover()
    await preview.waitFor()
    expect(await saved()).toEqual(asset)
    await manager.getByRole('button', { name: '下载任务' }).press('Enter')
    const downloads = page.getByRole('dialog', { name: '下载中心' })
    await downloads.waitFor()
    await preview.waitFor({ state: 'hidden' })
    await downloads.getByRole('button', { name: '返回舞台资源' }).click()
    await builtinCard.getByRole('button', { name: '应用', exact: true }).click()
    await expect.poll(saved).toBeNull()
    await manager.waitFor({ state: 'hidden' })
    await picker.waitFor({ state: 'hidden' })
    expect(errors).toEqual([])
  } finally {
    client?.close()
    await application?.close()
    await rm(directory, { recursive: true, force: true })
  }
}, 60000)
