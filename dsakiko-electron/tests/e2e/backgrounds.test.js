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
    const directoryFiles = [join(directory, 'classroom.png'), join(directory, 'hall.png')]
    for (const filename of directoryFiles)
      await copyFile(join(root, 'resources/icon.png'), filename)
    await application.evaluate(({ dialog }, files) => {
      dialog.showOpenDialog = async () => ({ canceled: false, filePaths: files })
    }, directoryFiles)
    const trigger = page.getByRole('button', { name: '切换背景', exact: true })
    const picker = page.getByRole('dialog', { name: '切换舞台背景' })
    const manager = page.getByRole('dialog', { name: '舞台资源' })
    const image = page.locator('.stage-backdrop')
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
    await card.getByRole('button', { name: '改名' }).click()
    await card.getByLabel('背景名称').fill('教室')
    await card.getByRole('button', { name: '保存' }).click()
    await manager.getByText('教室', { exact: true }).waitFor()
    await manager.getByRole('tab', { name: '服装', exact: true }).click()
    await manager.getByRole('tab', { name: '背景', exact: true }).click()
    await manager.getByText('教室', { exact: true }).waitFor()
    await manager.getByRole('button', { name: '关闭舞台资源' }).click()
    expect(await picker.getByRole('searchbox').inputValue()).toBe('classroom')
    await rm(directoryFiles[0])
    await picker.getByRole('button', { name: '教室', exact: true }).hover()
    await expect
      .poll(() => image.evaluateAll((images) => images.some((img) => img.naturalWidth > 0)), {
        timeout: 5000
      })
      .toBe(true)
    expect(await saved()).toBeNull()
    await page.keyboard.press('Escape')
    await expect.poll(() => image.count()).toBe(0)
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
    await trigger.click()
    await image.waitFor({ state: 'visible' })
    await builtinTile.hover()
    await picker.getByRole('heading').hover()
    await page.waitForTimeout(500)
    expect(await image.isVisible()).toBe(true)
    await builtinTile.hover()
    await image.waitFor({ state: 'hidden', timeout: 1000 })
    expect(await saved()).toEqual(asset)
    await picker.getByRole('heading').hover()
    await image.waitFor({ state: 'visible' })
    await builtinTile.focus()
    await image.waitFor({ state: 'hidden' })
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
    await image.waitFor({ state: 'hidden' })
    expect(await saved()).toEqual(asset)
    await manager.getByRole('searchbox').hover()
    await image.waitFor({ state: 'visible' })
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
