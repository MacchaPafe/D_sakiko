import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { expect, test } from 'vitest'
import { _electron as electron } from 'playwright-core'
import { createCoreBackend } from '../../src/backend/create-core-backend.js'

const root = fileURLToPath(new URL('../../', import.meta.url))

test('完整快照延迟时，Logo 退场已显示上次台词和译文', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'sakiko-startup-ui-'))
  const roots = {
    appDataRoot: join(directory, 'app'),
    resourceLibraryRoot: join(directory, 'library'),
    cacheRoot: join(directory, 'cache')
  }
  const resumeSnapshots = Promise.withResolvers()
  let backend, application
  try {
    backend = await createCoreBackend({
      ...roots,
      builtinDirectory: join(root, 'resources/builtin-characters'),
      scanReminders: false
    })
    await backend.onboarding.begin({ path: 'quick' })
    await backend.onboarding.finish()
    const chatId = await backend.conversations.createChat({
      title: '启动恢复',
      mode: 'single-character',
      characterIds: ['sakiko']
    })
    const { chat } = await backend.store.load(chatId)
    const text = 'おかえりなさい。'
    const translation = '欢迎回来。'
    chat.turns.push({
      id: 'turn',
      meta: {},
      messages: [
        {
          id: 'reply',
          kind: 'assistant',
          parts: [
            {
              id: 'dialogue',
              kind: 'dialogue',
              lines: [
                {
                  id: 'line',
                  speakerId: 'sakiko',
                  formId: chat.meta.settings.characters.sakiko.formId,
                  text,
                  translation
                }
              ]
            }
          ]
        }
      ]
    })
    await backend.store.save(chat)
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
    application = await electron.launch({
      args: [root, '--user-data-dir=' + join(directory, 'desktop')],
      env
    })
    const page = await application.firstWindow()
    page.setDefaultTimeout(8000)
    await page.getByRole('main', { name: '数字小祥主页' }).waitFor()
    await page.addInitScript((id) => {
      localStorage.setItem(
        'dsakiko.home.navigation.v1',
        JSON.stringify({ characterId: 'sakiko', chatId: id, tab: 'single-character' })
      )
    }, chatId)
    let snapshotHeld = false
    await page.routeWebSocket(/\/api\/v1\/events\?/, (socket) => {
      const server = socket.connectToServer()
      server.onMessage(async (message) => {
        if (JSON.parse(message.toString()).snapshot?.chat?.id === chatId) {
          snapshotHeld = true
          await resumeSnapshots.promise
        }
        socket.send(message)
      })
    })
    await page.reload({ waitUntil: 'domcontentloaded' })
    const cover = page.locator('.setup-loading[data-covering-home]')
    await cover.waitFor({ state: 'attached' })
    await cover.waitFor({ state: 'detached' })
    const stage = page.locator('.home-stage')
    expect(
      await stage.evaluate((element) => [
        element.querySelector('.subtitle-text')?.textContent,
        element.querySelector('.subtitle-translation')?.textContent
      ])
    ).toEqual([text, translation])
    await expect.poll(() => snapshotHeld).toBe(true)
    resumeSnapshots.resolve()
    await stage.getByRole('button', { name: '回放当句对话' }).waitFor()
    expect(await stage.locator('.subtitle-text').textContent()).toBe(text)
  } finally {
    resumeSnapshots.resolve()
    await application?.close()
    await backend?.close()
    await rm(directory, { recursive: true, force: true })
  }
})
