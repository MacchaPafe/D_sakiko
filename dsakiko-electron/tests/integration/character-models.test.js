import { afterEach, expect, test, vi } from 'vitest'
import { mkdtemp, mkdir, readFile, writeFile, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join, dirname } from 'node:path'
import { CharacterCatalog } from '../../src/backend/characters/catalog.js'
import { Assets } from '../../src/backend/resources/assets.js'
import { JsonStore } from '../../src/backend/storage/files.js'
import { Conversations } from '../../src/backend/conversation/conversations.js'
import { Performance } from '../../src/renderer/src/performance/performance.js'

const cleanups = []
afterEach(async () => {
  for (const cleanup of cleanups.splice(0).reverse()) await cleanup()
})

async function fixture() {
  const root = await mkdtemp(join(tmpdir(), 'dsakiko-models-'))
  cleanups.push(() => rm(root, { recursive: true, force: true }))
  const resources = join(root, 'resources'),
    data = join(root, 'data')
  const v2 = { model: 'model.moc', motions: { IDLE: [{ file: 'idle.mtn' }] } }
  const v3 = {
    FileReferences: {
      Moc: 'model.moc3',
      Motions: { wave: [{ File: 'wave.motion3.json' }] },
      Expressions: [{ Name: 'smile', File: 'smile.exp3.json' }]
    }
  }
  for (const id of ['a', 'b']) {
    const folder = join(resources, 'live2d_related', id)
    const entries = {
      'name.txt': id,
      'character_description.txt': '测试角色',
      'live2D_model/3.model.json': JSON.stringify(v2),
      'extra_model/春季常服/spring.model3.json': JSON.stringify(v3),
      'extra_model/损坏/3.model.json': '{invalid'
    }
    for (const [path, content] of Object.entries(entries)) {
      await mkdir(dirname(join(folder, path)), { recursive: true })
      await writeFile(join(folder, path), content)
    }
  }
  const legacyPath = join(resources, 'd_sakiko_config.json')
  const legacy = JSON.stringify({
    character_setting: {
      l2d_json_paths_dict: { a: '../live2d_related/a/extra_model/春季常服/spring.model3.json' }
    }
  })
  await writeFile(legacyPath, legacy)
  const assets = await new Assets(data, () => resources).initialize()
  const catalog = new CharacterCatalog(data, assets, () => resources)
  await catalog.refresh()
  const a = (await catalog.list()).find((item) => item.id === 'a')
  const b = (await catalog.list()).find((item) => item.id === 'b')
  return { resources, data, assets, catalog, legacyPath, legacy, a, b }
}

test('发现双版本装扮，独立保存优先于旧配置，重启恢复动作和表情能力', async () => {
  const { catalog, assets, resources, data, legacyPath, legacy, a } = await fixture()
  expect(a.models).toHaveLength(3)
  expect(a.models.find((model) => model.id === a.selectedModelId).version).toBe('v3')
  expect((await catalog.resolveCapabilities('a')).performances.expressions[0].id).toBe('smile')
  const selected = a.models.find((model) => model.version === 'v2')
  await catalog.setModel('a', selected.id)
  const restarted = new CharacterCatalog(data, assets, () => resources)
  await restarted.refresh()
  expect((await restarted.get('a')).selectedModelId).toBe(selected.id)
  expect((await restarted.resolveCapabilities('a')).performances).toEqual({
    motions: [{ id: 'IDLE:0', description: 'IDLE' }],
    expressions: []
  })
  expect(await readFile(legacyPath, 'utf8')).toBe(legacy)
  expect((await catalog.get('b')).selectedModelId).not.toBe(selected.id)
  const exposed = JSON.stringify(await restarted.list())
  expect(exposed).not.toContain(resources)
})

test('拒绝其他角色、任意路径和损坏入口；保存的文件消失时不偷偷改选', async () => {
  const { catalog, a, b, resources, data } = await fixture()
  for (const id of [
    b.models[0].id,
    '../../settings.json',
    a.models.find((model) => model.problem).id
  ]) {
    await expect(catalog.setModel('a', id)).rejects.toHaveProperty(
      'problem.code',
      'model_unavailable'
    )
  }
  expect((await catalog.get('a')).selectedModelId).toBe(a.selectedModelId)
  await catalog.setModel('a', a.selectedModelId)
  const before = await readFile(join(data, 'character-models/a.json'), 'utf8')
  await rm(join(resources, 'live2d_related/a/extra_model/春季常服/spring.model3.json'))
  await expect(catalog.setModel('a', a.selectedModelId)).rejects.toThrow()
  expect(await readFile(join(data, 'character-models/a.json'), 'utf8')).toBe(before)
  await catalog.refresh()
  const refreshed = await catalog.get('a')
  expect(refreshed.selectedModelId).toBe(a.selectedModelId)
  expect(refreshed.presentation).toBeNull()
  expect(refreshed.problems[0].code).toBe('model_unavailable')
  await catalog.setModel('a', a.models.find((model) => model.version === 'v2').id)
  expect((await catalog.get('a')).problems).toEqual([])
})

async function conversationsFixture(agent) {
  const fixtureData = await fixture()
  const surface = { stop: vi.fn(), showScene: vi.fn(), play: vi.fn(() => new Promise(() => {})) }
  const clock = { now: 0 }
  const performance = new Performance({ surface, now: () => clock.now, autoTick: false })
  performance.observeAll((update) => performance.onUpdate?.(update))
  const conversations = await new Conversations({
    catalog: fixtureData.catalog,
    store: new JsonStore(join(fixtureData.data, 'chats')),
    agent,
    speech: { setPriority: async () => {}, cancel: async () => {} },
    performance,
    getSettings: () => ({ baseUrl: 'test', model: 'test', language: 'ja', voiceEnabled: false })
  }).initialize()
  cleanups.push(async () => {
    await conversations.interruptAll()
    performance.dispose()
  })
  return { ...fixtureData, conversations, performance, surface, clock }
}

test('换装更新同角色的所有空闲舞台，后台生成与演出期间拒绝，其他角色仍可换装', async () => {
  let finish
  const agent = {
    run: vi.fn(
      () =>
        new Promise((resolve) => {
          finish = resolve
        })
    )
  }
  const { conversations, a, b, catalog, surface, performance, clock } =
    await conversationsFixture(agent)
  const first = await conversations.createChat({ characterIds: ['a'] })
  const second = await conversations.createChat({ characterIds: ['a', 'b'] })
  await conversations.selectConversation(first)
  await conversations.selectConversation(second)
  const alternative = a.models.find((model) => model.version === 'v2')
  await conversations.setCharacterModel('a', alternative.id)
  const chosenAsset = (await catalog.get('a')).presentation.model
  expect(surface.showScene.mock.lastCall[0].slots[0].presentation.model).toEqual(chosenAsset)
  await conversations.selectConversation(first)
  expect(surface.showScene.mock.lastCall[0].slots[0].presentation.model).toEqual(chosenAsset)
  await conversations.createTurn(first, { text: '你好' }, { requestId: 'turn' })
  await conversations.selectConversation(null)
  await expect(conversations.setCharacterModel('a', a.selectedModelId)).rejects.toHaveProperty(
    'problem.code',
    'chat_busy'
  )
  await conversations.setCharacterModel('b', b.models.find((model) => model.version === 'v3').id)
  expect(conversations.allSnapshots().find((state) => state.chat.id === first).activity).toBe(
    'generating'
  )
  expect(agent.run.mock.calls[0][0].characters[0].performances.motions[0].id).toBe('IDLE:0')
  finish({
    status: 'completed',
    lines: [
      {
        kind: 'character',
        speakerId: 'a',
        text: '你好',
        performance: { motion: 'auto', expression: 'auto' },
        translation: null,
        emotion: null
      }
    ]
  })
  await vi.waitFor(() => expect(conversations.allSnapshots()[0].activity).toBe('presenting'))
  await expect(conversations.setCharacterModel('a', a.selectedModelId)).rejects.toHaveProperty(
    'problem.code',
    'chat_busy'
  )
  clock.now = 10000
  performance.tick()
  await vi.waitFor(() => expect(conversations.allSnapshots()[0].activity).toBe('idle'))
  await conversations.setCharacterModel('a', a.selectedModelId)
  expect((await catalog.get('a')).selectedModelId).toBe(a.selectedModelId)
})

test('发送已经受理但还未写入忙碌状态时，换装不能绕过保护', async () => {
  const { conversations, performance, catalog, a } = await conversationsFixture({
    run: () => new Promise(() => {})
  })
  const chat = await conversations.createChat({ characterIds: ['a'] })
  const create = performance.createSequence.bind(performance)
  let release, entered
  const waiting = new Promise((resolve) => {
    entered = resolve
  })
  performance.createSequence = async (scene) => {
    entered()
    await new Promise((resolve) => {
      release = resolve
    })
    return create(scene)
  }
  const turn = conversations.createTurn(chat, { text: '你好' }, { requestId: 'racing' })
  await waiting
  expect(conversations.allSnapshots()[0].activity).toBe('idle')
  const change = conversations.setCharacterModel(
    'a',
    a.models.find((model) => model.version === 'v2').id
  )
  const rejection = expect(change).rejects.toHaveProperty('problem.code', 'chat_busy')
  release()
  await turn
  await rejection
  expect((await catalog.get('a')).selectedModelId).toBe(a.selectedModelId)
})
