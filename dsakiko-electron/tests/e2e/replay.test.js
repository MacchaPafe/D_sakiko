import { mkdtemp, rm, mkdir, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { afterAll, beforeAll, expect, test } from 'vitest'
import { _electron as electron } from 'playwright-core'
import { Assets } from '../../src/backend/resources/assets.js'
import { JsonStore } from '../../src/backend/storage/files.js'

const projectRoot = fileURLToPath(new URL('../../', import.meta.url))
let temporary, application, page, ids
const errors = []

function silentWave(durationMs) {
  const samples = Math.round((16000 * durationMs) / 1000)
  const buffer = Buffer.alloc(44 + samples * 2)
  buffer.write('RIFF', 0)
  buffer.writeUInt32LE(buffer.length - 8, 4)
  buffer.write('WAVEfmt ', 8)
  buffer.writeUInt32LE(16, 16)
  buffer.writeUInt16LE(1, 20)
  buffer.writeUInt16LE(1, 22)
  buffer.writeUInt32LE(16000, 24)
  buffer.writeUInt32LE(32000, 28)
  buffer.writeUInt16LE(2, 32)
  buffer.writeUInt16LE(16, 34)
  buffer.write('data', 36)
  buffer.writeUInt32LE(samples * 2, 40)
  return buffer
}

beforeAll(async () => {
  temporary = await mkdtemp(join(tmpdir(), 'dsakiko-replay-'))
  const resourceRoot = process.env.DSAKIKO_E2E_RESOURCES || join(temporary, 'resources')
  const dataRoot = join(temporary, 'data')
  if (!process.env.DSAKIKO_E2E_RESOURCES) {
    const folder = join(resourceRoot, 'live2d_related', 'a')
    await mkdir(folder, { recursive: true })
    await writeFile(join(folder, 'name.txt'), '回放角色')
    await writeFile(join(folder, 'character_description.txt'), '测试角色')
  }
  ids = process.env.DSAKIKO_E2E_RESOURCES ? ['anon', 'sakiko'] : ['a']
  await mkdir(join(dataRoot, 'audio'), { recursive: true })
  const audioPath = join(dataRoot, 'audio', 'replay.wav')
  // 真实音频元素与媒体协议播放静音 WAV，不需要额外推理或干扰用户声音。
  await writeFile(audioPath, silentWave(2400))
  const assets = await new Assets(dataRoot, () => resourceRoot).initialize()
  const audio = { asset: await assets.register(audioPath, 'data'), durationMs: 2400 }
  const store = new JsonStore(join(dataRoot, 'chats'))
  for (const id of ids) {
    await store.save({
      id: `replay-${id}`,
      revision: 0,
      title: `回放验收 ${id}`,
      characterIds: [id],
      userPersona: null,
      mode: 'single-character',
      meta: { defaults: {}, reminders: [], extensions: {} },
      turns: [
        {
          id: `turn-${id}`,
          metadata: { state: 'completed' },
          messages: [
            {
              id: `line-${id}`,
              kind: 'character',
              speakerId: id,
              text: 'こんにちは。お会いできて嬉しいです。',
              translation: '很高兴再次与你见面。',
              emotion: 'happiness',
              performance: { motion: 'auto', expression: 'auto' },
              audio,
              attachments: [],
              metadata: {}
            }
          ]
        }
      ]
    })
  }
  const environment = {
    ...process.env,
    DSAKIKO_RESOURCE_ROOT: resourceRoot,
    DSAKIKO_DATA_DIR: dataRoot
  }
  delete environment.ELECTRON_RUN_AS_NODE
  delete environment.ELECTRON_RENDERER_URL
  application = await electron.launch({
    args: [projectRoot, '--user-data-dir=' + join(temporary, 'browser')],
    env: environment
  })
  page = await application.firstWindow()
  page.setDefaultTimeout(5000)
  page.on('pageerror', (error) => errors.push(error.message))
  await page.locator('[data-status="ready"]').waitFor()
  if (ids.includes('sakiko')) {
    const changed = await page.evaluate(async () => {
      const bootstrap = await window.dsakiko.getBootstrap()
      const character = bootstrap.value.characters.find((item) => item.id === 'sakiko')
      const model = character.models.find((item) => item.version === 'v3' && !item.problem)
      return window.dsakiko.setCharacterModel(character.id, model.id)
    })
    expect(changed.problem).toBeUndefined()
  }
})

afterAll(async () => {
  if (application?.process().exitCode === null) {
    const deadline = setTimeout(() => application.process().kill('SIGKILL'), 14000)
    try {
      await application.close()
    } finally {
      clearTimeout(deadline)
    }
  }
  if (temporary) await rm(temporary, { recursive: true, force: true })
})

test('真实音频重复回放仍能操作界面、切换聊天，并能自然结束', async () => {
  // 仅兜底终止本测试创建的进程，旧版卡死不能拖住整组验收。
  const deadline = setTimeout(() => application.process().kill('SIGKILL'), 25000)
  try {
    for (const id of ids) {
      await page.locator(`.chat-entry[data-id="replay-${id}"]`).click()
      await page.locator('.stage-name').waitFor()
      await page.locator('.stage-pane[aria-busy="false"]').waitFor()
      const button = page.getByRole('button', { name: /回放.*这句台词/ })
      for (let index = 0; index < 3; index += 1) {
        await button.click()
        await page.locator('.stage-subtitle').waitFor()
        await page.getByRole('button', { name: /我的人格/ }).click()
        await page.getByRole('dialog').waitFor()
        await page.getByRole('button', { name: '关闭', exact: true }).click()
      }
      // 设置会把回放切出前台，随后同一模型仍应可以再次启动。
      await page.getByRole('button', { name: '设置', exact: true }).click()
      await page.getByRole('dialog').waitFor()
      await page.getByRole('button', { name: '关闭', exact: true }).click()
      await button.click()
      await page.locator('.stage-subtitle').waitFor()
      await page.locator('.stage-subtitle').waitFor({ state: 'hidden', timeout: 12000 })
      expect(await page.locator('.message').count()).toBe(1)
      expect(await page.locator('.stage-problem').count()).toBe(0)
      expect(await page.getByLabel('消息', { exact: true }).isEnabled()).toBe(true)
    }
    expect(errors).toEqual([])
  } finally {
    clearTimeout(deadline)
  }
}, 30000)

test('即使渲染线程失去响应，系统退出请求也在限定时间内结束程序', async () => {
  const process = application.process()
  const fallback = setTimeout(() => process.kill('SIGKILL'), 14500)
  try {
    await page.getByRole('button', { name: /回放.*这句台词/ }).click()
    await page.locator('.stage-subtitle').waitFor()
    // 模拟渲染端故障；退出由主进程发起，不能依赖卡住的页面执行清理。
    await page.evaluate(() => {
      setTimeout(() => {
        const deadline = performance.now() + 20000
        while (performance.now() < deadline) {
          /* 故意阻塞此测试窗口。 */
        }
      }, 0)
    })
    await new Promise((resolve) => setTimeout(resolve, 50))
    const exited = new Promise((resolve) =>
      process.once('exit', (code, signal) => resolve({ code, signal }))
    )
    await application.evaluate(({ app }) => {
      app.quit()
    })
    expect(await exited).toEqual({ code: 0, signal: null })
  } finally {
    clearTimeout(fallback)
  }
}, 18000)
