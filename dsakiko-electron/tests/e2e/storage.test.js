import { access, mkdir, mkdtemp, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { expect, test } from 'vitest'
import { _electron as electron } from 'playwright-core'
import { createCoreBackend } from '../../src/backend/create-core-backend.js'

const root = fileURLToPath(new URL('../../', import.meta.url))

test('设置存储页提供独立位置卡片、应用数据标签和批量删除', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'sakiko-storage-ui-'))
  const roots = {
    appDataRoot: join(directory, 'data'),
    resourceLibraryRoot: join(directory, 'library'),
    cacheRoot: join(directory, 'cache')
  }
  let backend
  let desktop
  try {
    backend = await createCoreBackend({ ...roots, scanReminders: false })
    await backend.onboarding.begin({ path: 'quick' })
    await backend.onboarding.finish()
    for (const title of ['存储测试甲', '存储测试乙'])
      await backend.conversations.createChat({
        title,
        mode: 'single-character',
        characterIds: ['sakiko']
      })
    await backend.close()
    backend = null

    const env = {
      ...process.env,
      DSAKIKO_DATA_DIR: roots.appDataRoot,
      DSAKIKO_RESOURCE_LIBRARY_DIR: roots.resourceLibraryRoot,
      DSAKIKO_CACHE_DIR: roots.cacheRoot
    }
    delete env.ELECTRON_RUN_AS_NODE
    delete env.ELECTRON_RENDERER_URL
    const launch = () =>
      electron.launch({
        args: [root, '--user-data-dir=' + join(directory, 'electron')],
        env
      })
    desktop = await launch()
    const page = await desktop.firstWindow()
    page.setDefaultTimeout(8000)
    await page.getByRole('main', { name: '数字小祥主页' }).waitFor()
    await page.getByRole('button', { name: '设置', exact: true }).click()
    await page.getByRole('button', { name: '存储空间', exact: true }).click()
    const settings = page.getByRole('dialog', { name: '设置' })
    const locations = settings.locator('.storage-locations')
    await locations.getByRole('heading', { name: '存储位置' }).waitFor()
    expect(await locations.getByRole('button', { name: /打开.*文件夹/ }).count()).toBe(3)
    const logs = join(roots.cacheRoot, 'logs')
    await mkdir(logs, { recursive: true })
    const staleLog = join(logs, 'speech.log.old-test')
    await writeFile(staleLog, 'idle log bytes')
    const previewDirectory = join(roots.cacheRoot, 'background-previews')
    await mkdir(previewDirectory, { recursive: true })
    const cachedPreview = join(previewDirectory, 'a'.repeat(64) + '.png')
    await writeFile(cachedPreview, Buffer.alloc(32_000))
    await settings.getByRole('button', { name: '刷新', exact: true }).click()
    const previewRow = settings.locator('.storage-usage-row').filter({ hasText: '在线图库缩略图' })
    await previewRow.getByText('32 KB', { exact: true }).waitFor()
    await previewRow.getByRole('button', { name: '清理在线图库缩略图', exact: true }).click()
    await previewRow.waitFor({ state: 'detached' })
    await expect(access(cachedPreview)).rejects.toMatchObject({ code: 'ENOENT' })
    await expect(access(staleLog)).resolves.toBeUndefined()
    await settings.getByRole('button', { name: '清理语音日志', exact: true }).click()
    await settings.getByText('清理完成', { exact: true }).waitFor()
    await expect(access(staleLog)).rejects.toMatchObject({ code: 'ENOENT' })
    if (process.env.DSAKIKO_STORAGE_SCREENSHOT)
      await settings.screenshot({ path: process.env.DSAKIKO_STORAGE_SCREENSHOT })
    await settings.getByRole('button', { name: '管理聊天', exact: true }).first().click()
    await settings.getByRole('tab', { name: '角色' }).click()
    await settings.getByRole('button', { name: '前往管理' }).first().click()
    await page.getByRole('dialog', { name: '角色管理' }).waitFor()
    await page.keyboard.press('Escape')
    await settings.getByRole('tab', { name: '聊天记录' }).click()
    await settings.getByRole('checkbox', { name: '选择 存储测试甲' }).click()
    await settings.getByRole('checkbox', { name: '选择 存储测试乙' }).click()
    await settings.getByRole('button', { name: '删除（2）', exact: true }).click()
    const confirmation = settings.getByRole('group', { name: /预计释放 / })
    await confirmation.getByRole('button', { name: '确认删除 2 个聊天', exact: true }).click()
    await settings.getByText('已删除 2 个聊天。', { exact: true }).waitFor()
    await settings.getByText('没有已保存的聊天。', { exact: true }).waitFor()
    await page.evaluate(() =>
      window.dsakikoHost.setUiPreference('historySize', { width: 480, height: 550 })
    )
    await desktop.close()
    desktop = null
    desktop = await launch()
    const restarted = await desktop.firstWindow()
    await restarted.getByRole('main', { name: '数字小祥主页' }).waitFor()
    expect(await restarted.evaluate(() => window.dsakikoHost.getUiPreferences())).toMatchObject({
      historySize: { width: 480, height: 550 }
    })
  } finally {
    await desktop?.close()
    await backend?.close()
    await rm(directory, { recursive: true, force: true })
  }
})
