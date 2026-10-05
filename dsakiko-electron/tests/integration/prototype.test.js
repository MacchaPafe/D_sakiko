import { afterEach, describe, expect, test, vi } from 'vitest'
import { mkdtemp, mkdir, writeFile, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { JsonStore } from '../../src/backend/storage/files.js'
import { Assets } from '../../src/backend/resources/assets.js'
import { CharacterCatalog } from '../../src/backend/characters/catalog.js'
import { Agent } from '../../src/backend/llm/agent.js'
import { Conversations } from '../../src/backend/conversation/conversations.js'
import { Performance } from '../../src/renderer/src/performance/performance.js'
import { Drafts } from '../../src/renderer/src/features/chat/drafts.js'
import { Settings } from '../../src/backend/settings/settings.js'

const directories = []
afterEach(async () => {
  for (const path of directories.splice(0)) await rm(path, { recursive: true, force: true })
})
async function directory() {
  const path = await mkdtemp(join(tmpdir(), 'dsakiko-test-'))
  directories.push(path)
  return path
}
const scene = {
  background: null,
  slots: [
    { id: 'a', displayName: '角色 A', presentation: null, layout: { x: 0.5, y: 0.5, scale: 1 } }
  ]
}
function guide(text = '你好', ready = true) {
  return {
    slotId: 'a',
    text,
    translation: null,
    emotion: null,
    performance: { motion: 'auto', expression: 'auto' },
    audio: null,
    ready
  }
}
function line(text = '你好') {
  return {
    kind: 'character',
    speakerId: 'a',
    text,
    translation: null,
    emotion: null,
    performance: { motion: 'auto', expression: 'auto' }
  }
}

describe('演出顺序、后台推进与终态', () => {
  test('等待材料时不越过，后台按截止时间补算，回前台仅重播当前句', async () => {
    let now = 0
    const surface = { stop: vi.fn(), showScene: vi.fn(), play: vi.fn(() => new Promise(() => {})) }
    const performance = new Performance({ now: () => now, autoTick: false, surface })
    const id = await performance.createSequence(scene)
    const ids = await performance.appendGuides(id, [guide('第一句', false), guide('第二句')])
    now = 5000
    performance.tick()
    expect((await performance.getProgress(id)).waitingFor).toBe(ids[0])
    await performance.resolveGuide(ids[0], { audio: null })
    now = 6400
    performance.tick()
    expect((await performance.getProgress(id)).current.guideId).toBe(ids[1])
    await performance.setForeground(id)
    expect(surface.play).toHaveBeenCalledTimes(1)
    expect(surface.play.mock.calls[0][0].text).toBe('第二句')
    expect((await performance.getProgress(id)).current.elapsedMs).toBe(0)
    await performance.setForeground(id)
    expect(surface.play).toHaveBeenCalledTimes(1)
    performance.dispose()
  })
  test('停止后续 guide 保留当前句；删除与关闭都结算等待', async () => {
    const performance = new Performance({ autoTick: false })
    const id = await performance.createSequence(scene)
    const [first, second] = await performance.appendGuides(id, [guide(), guide()])
    const waiting = performance.waitForGuideFinish(second)
    expect(await performance.discardPendingGuides([first, second])).toEqual([second])
    expect(await waiting).toEqual({ guideId: second, outcome: 'removed' })
    const current = performance.waitForGuideFinish(first)
    await performance.closeSequence(id)
    expect((await current).outcome).toBe('closed')
    await expect(performance.waitForGuideFinish(first)).rejects.toHaveProperty(
      'problem.code',
      'guide_unavailable'
    )
  })
  test('音频乱序就绪仍按顺序，取消等待不取消演出', async () => {
    const performance = new Performance({ autoTick: false })
    const id = await performance.createSequence(scene)
    const [first, second] = await performance.appendGuides(id, [
      guide('甲', false),
      guide('乙', false)
    ])
    await performance.resolveGuide(second, { audio: null })
    expect((await performance.getProgress(id)).waitingFor).toBe(first)
    const controller = new AbortController(),
      waiting = performance.waitForGuideFinish(first, controller.signal)
    controller.abort()
    await expect(waiting).rejects.toHaveProperty('name', 'AbortError')
    expect(await performance.resolveGuide(first, { audio: null })).toBe(true)
    expect((await performance.getProgress(id)).current.guideId).toBe(first)
    performance.dispose()
  })
})

test('存档比较与写入原子完成，失败不修改输入', async () => {
  const store = new JsonStore(await directory())
  const original = { id: 'test', revision: 0, turns: [] }
  const saved = await store.save(original)
  expect(original.revision).toBe(0)
  const updates = await Promise.allSettled([
    store.save({ ...saved, title: 'A' }),
    store.save({ ...saved, title: 'B' })
  ])
  expect(updates.filter((result) => result.status === 'fulfilled')).toHaveLength(1)
  expect((await store.load('test')).revision).toBe(2)
  await expect(store.load('../settings')).rejects.toHaveProperty('problem.code', 'invalid_id')
})

test('人格快照固定，角色引用每次解析当前目录', async () => {
  const root = await directory(),
    data = await directory()
  const folder = join(root, 'live2d_related', 'a')
  await mkdir(folder, { recursive: true })
  await writeFile(join(folder, 'name.txt'), 'A')
  await writeFile(join(folder, 'character_description.txt'), '原始设定')
  const assets = await new Assets(data, () => root).initialize()
  const catalog = new CharacterCatalog(data, assets, () => root)
  await catalog.refresh()
  const persona = await catalog.savePersona({
    revision: 0,
    source: { kind: 'character', characterId: 'a' }
  })
  const previous = await catalog.resolvePersona(persona.id)
  await writeFile(join(folder, 'character_description.txt'), '新的设定')
  await catalog.refresh()
  expect((await catalog.resolvePersona(persona.id)).description).toBe('新的设定')
  expect(previous.description).toBe('原始设定')
  expect((await catalog.getPersona(persona.id)).revision).toBe(1)
})

test('模型资源包不能通过相对路径读取包外文件', async () => {
  const root = await directory(),
    data = await directory()
  await mkdir(join(root, 'model'))
  await writeFile(join(root, 'model', 'test.model.json'), '{}')
  await writeFile(join(root, 'secret.json'), 'private')
  const assets = await new Assets(data, () => root).initialize()
  const asset = await assets.register(join(root, 'model', 'test.model.json'), 'external', true)
  await expect(
    assets.pathForUrl(`dsakiko-media://asset/${asset.id}/..%2Fsecret.json`)
  ).rejects.toHaveProperty('problem.code', 'invalid_asset')
})

test('Agent 真正循环调用时间工具，格式修复保留工具结果且不重复执行', async () => {
  const calls = [],
    toolRecords = []
  const responses = [
    'broken JSON',
    JSON.stringify({ type: 'tool', name: 'get_current_time', arguments: {} }),
    JSON.stringify({ type: 'final', lines: [line()] })
  ]
  const agent = new Agent(
    () => ({
      baseUrl: 'https://example.invalid/v1',
      model: 'test',
      language: 'ja',
      requestTimeoutMs: 1000
    }),
    async (_url, options) => {
      calls.push(JSON.parse(options.body))
      return {
        ok: true,
        json: async () => ({ choices: [{ message: { content: responses.shift() } }] })
      }
    }
  )
  const result = await agent.run(
    {
      characters: [
        {
          id: 'a',
          displayName: 'A',
          description: '角色',
          performances: { motions: [], expressions: [] }
        }
      ],
      userPersona: { displayName: '我', description: '用户' },
      history: [],
      mode: 'single-character',
      model: { model: 'test', contextTokenBudget: 12000, temperature: 0.8 },
      input: { message: { text: '几点了？' } }
    },
    {
      onIntermediate: async () => {},
      onToolActivity: async (record) => {
        toolRecords.push(record)
      }
    },
    new AbortController().signal
  )
  expect(calls).toHaveLength(3)
  expect(toolRecords.map((record) => record.status)).toEqual(['running', 'succeeded'])
  expect(toolRecords[1].result.iso).toMatch(/^\d{4}-/)
  expect(calls[2].messages.at(-1).content).toContain('get_current_time 工具结果')
  expect(result.lines[0].text).toBe('你好')
})

test('发送确认保护新草稿，同一草稿重试复用编号', () => {
  const drafts = new Drafts()
  drafts.update('a', '第一条')
  const first = drafts.prepare('a')
  expect(drafts.prepare('a').requestId).toBe(first.requestId)
  drafts.update('a', '第二条')
  drafts.acknowledge('a', first.requestId)
  expect(drafts.get('a')).toBe('第二条')
})

test('旧供应商字典正确导入，公开设置不泄漏密钥', async () => {
  const root = await directory(),
    data = await directory()
  await writeFile(
    join(root, 'd_sakiko_config.json'),
    JSON.stringify({
      llm_setting: {
        llm_api_provider: 'deepseek',
        llm_api_model: { deepseek: 'deepseek/test' },
        llm_api_key: { deepseek: 'fixture-key' },
        llm_api_base_url: {}
      }
    })
  )
  const settings = await new Settings(data, { resourceRoot: root }).initialize()
  const result = await settings.importLegacy()
  expect(result.model).toBe('test')
  expect(result.baseUrl).toBe('https://api.deepseek.com')
  expect(result.apiKey).toBe('')
  expect(result.hasApiKey).toBe(true)
  expect(settings.get().apiKey).toBe('fixture-key')
})

test('重启保留未完成轮的文本，将任务与工具标为中断', async () => {
  const store = new JsonStore(await directory())
  await store.save({
    id: 'restart',
    revision: 0,
    characterIds: ['a'],
    turns: [
      {
        id: 'turn',
        metadata: { state: 'running' },
        messages: [
          { kind: 'user', text: '保留输入' },
          { kind: 'tool', text: '时间', toolCall: { status: 'running' } }
        ]
      }
    ]
  })
  const performance = new Performance({ autoTick: false })
  const conversations = await new Conversations({ store, performance }).initialize()
  const snapshot = conversations.allSnapshots()[0]
  expect(snapshot.activity).toBe('idle')
  expect(snapshot.chat.turns[0].metadata.state).toBe('interrupted')
  expect(snapshot.chat.turns[0].messages[0].text).toBe('保留输入')
  expect(snapshot.chat.turns[0].messages[1].toolCall.status).toBe('interrupted')
  expect(performance.sequences.size).toBe(0)
})

async function conversationsFixture(
  agent,
  speech = { cancel: async () => {}, setPriority: async () => {} },
  hasVoice = false
) {
  const performance = new Performance({ autoTick: false })
  performance.observeAll((update) => performance.onUpdate?.(update))
  const catalog = {
    resolveCapabilities: async (id) => ({
      id,
      displayName: id,
      description: '角色',
      presentation: null,
      voice: hasVoice ? {} : null,
      performances: { motions: [], expressions: [] }
    })
  }
  const conversations = await new Conversations({
    store: new JsonStore(await directory()),
    catalog,
    agent,
    speech,
    performance,
    getSettings: () => ({ baseUrl: 'test', model: 'test', language: 'ja', voiceEnabled: hasVoice })
  }).initialize()
  const id = await conversations.createChat({ characterIds: ['a'] })
  return { conversations, performance, id }
}

test('受理去重、生成期间禁止追加，停止后丢弃 Agent 迟到结果', async () => {
  let finish
  const agent = {
    run: () =>
      new Promise((resolve) => {
        finish = resolve
      })
  }
  const { conversations, performance, id } = await conversationsFixture(agent)
  const turn = await conversations.createTurn(id, { text: '你好' }, { requestId: 'r1' })
  expect(await conversations.createTurn(id, { text: '你好' }, { requestId: 'r1' })).toBe(turn)
  await expect(
    conversations.createTurn(id, { text: '下一句' }, { requestId: 'r2' })
  ).rejects.toHaveProperty('problem.code', 'chat_busy')
  await vi.waitFor(() => expect(finish).toBeTypeOf('function'))
  await conversations.stopTurn(id, turn)
  finish({ status: 'completed', lines: [line('迟到的台词')] })
  await new Promise((resolve) => setTimeout(resolve, 20))
  expect(conversations.allSnapshots()[0].chat.turns[0].messages).toHaveLength(1)
  expect(conversations.allSnapshots()[0].activity).toBe('idle')
  performance.dispose()
})

test('停止覆盖在途合成提交，不让晚收到的任务继续运行', async () => {
  let submitted
  const speech = {
    submit: () =>
      new Promise((resolve) => {
        submitted = resolve
      }),
    cancel: vi.fn(async () => {}),
    setPriority: vi.fn(async () => {})
  }
  const { conversations, performance, id } = await conversationsFixture(
    { run: async () => ({ status: 'completed', lines: [line()] }) },
    speech,
    true
  )
  const turn = await conversations.createTurn(id, { text: '你好' }, { requestId: 'r1' })
  await vi.waitFor(() => expect(submitted).toBeTypeOf('function'))
  expect(conversations.allSnapshots()[0].activity).toBe('presenting')
  await conversations.stopTurn(id, turn)
  submitted('late-task')
  await vi.waitFor(() => expect(speech.cancel).toHaveBeenCalledWith(['late-task']))
  expect(conversations.allSnapshots()[0].activity).toBe('idle')
  expect(conversations.allSnapshots()[0].chat.turns[0].messages[1].text).toBe('你好')
  performance.dispose()
})

test('台词首次发布就是等待状态，轮到演出才逐字显示，后台完成后保留完整历史', async () => {
  const { conversations, performance, id } = await conversationsFixture({
    run: async () => ({ status: 'completed', lines: [line('第一句话'), line('第二句话')] })
  })
  let now = 0
  performance.now = () => now
  const snapshots = []
  conversations.observe(id, (snapshot) => snapshots.push(snapshot))
  await conversations.createTurn(id, { text: '开始' }, { requestId: 'visibility' })
  await vi.waitFor(() => expect(conversations.allSnapshots()[0].activity).toBe('presenting'))
  const firstPublished = snapshots.find((snapshot) =>
    snapshot.chat.turns[0]?.messages.some((entry) => entry.kind === 'character')
  )
  expect(firstPublished.display.map((display) => display.mode)).toEqual(['pending', 'pending'])
  now = 600
  performance.tick()
  let snapshot = conversations.allSnapshots()[0]
  expect(snapshot.display.map((display) => display.mode)).toEqual(['progress', 'pending'])
  expect(snapshot.display[0].revealedCharacters).toBeGreaterThan(0)
  now = 1800
  performance.tick()
  snapshot = conversations.allSnapshots()[0]
  expect(snapshot.display.map((display) => display.mode)).toEqual(['full', 'progress'])
  now = 3000
  performance.tick()
  await vi.waitFor(() => expect(conversations.allSnapshots()[0].activity).toBe('idle'))
  expect(conversations.allSnapshots()[0].display.map((display) => display.mode)).toEqual([
    'full',
    'full'
  ])
  performance.dispose()
})
