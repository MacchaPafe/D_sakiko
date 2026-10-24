import { createCoreBackend } from '../../src/backend/create-core-backend.js'
import { mkdtemp, readdir, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { afterEach, expect, test, vi } from 'vitest'
import { MediaAssets } from '../../src/backend/resources/interface.js'
import { ResourceImports } from '../../src/backend/resources/imports.js'
import { extractAssetReferences } from '../../src/backend/resources/references.js'
import { ChatStore } from '../../src/backend/storage/interface.js'
import { CharacterCatalog } from '../../src/backend/characters/interface.js'
import { Settings } from '../../src/backend/settings/interface.js'
import { Speech } from '../../src/backend/speech/interface.js'
import { Performance } from '../../src/backend/performance/interface.js'
import { writeJson } from '../../src/backend/core/files.js'

const cleanups = []
afterEach(async () => {
  for (const cleanup of cleanups.splice(0).reverse()) await cleanup()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  vi.useRealTimers()
})
async function fixture() {
  const directory = await mkdtemp(path.join(tmpdir(), 'dsakiko-owners-'))
  cleanups.push(() => rm(directory, { recursive: true, force: true }))
  const roots = {
    appDataRoot: path.join(directory, 'app'),
    resourceLibraryRoot: path.join(directory, 'library'),
    cacheRoot: path.join(directory, 'cache')
  }
  const assets = new MediaAssets(roots)
  const imports = new ResourceImports(assets)
  const store = new ChatStore({ directory: path.join(roots.appDataRoot, 'chats'), assets })
  const characters = new CharacterCatalog({ directory: roots.appDataRoot, assets })
  const settings = new Settings({ directory: roots.appDataRoot, assets })
  await assets.initialize()
  return { directory, roots, assets, imports, store, characters, settings }
}
function source(name = 'image.png') {
  return { kind: 'bytes', name, mediaType: 'image/png', bytes: Buffer.from(name) }
}
function chat(references = []) {
  return {
    id: 'chat',
    revision: 0,
    title: '聊天',
    characterIds: [],
    mode: 'single-character',
    turns: [],
    meta: { reminders: [] },
    extensions: { resources: references }
  }
}
function character(avatar) {
  return {
    id: 'sakiko',
    revision: 0,
    defaultFormId: 'default',
    forms: [
      { id: 'default', displayName: '祥子', avatar, models: [], defaultModelId: null, voice: null }
    ],
    knowledgeMappings: [],
    extensions: {}
  }
}
function ownersOf(state, asset) {
  return Object.entries(state.owners)
    .filter(([, refs]) => refs.includes(asset.id))
    .map(([owner]) => owner)
}
async function imported(assets, purpose = 'chat-image') {
  return (await assets.importAsset(source(), 'test-source', purpose)).asset
}

test('引用提取遵循 AssetRef 约定、去重，不把业务实体编号当作资源', () => {
  const value = {
    id: 'message',
    resources: [{ id: 'image' }, { id: 'image' }],
    nested: { asset: { id: 'audio' } }
  }
  value.cycle = value
  expect(extractAssetReferences(value)).toEqual([{ id: 'image' }, { id: 'audio' }])
})

test('并发增量不丢失引用，集合操作幂等且不影响其他 owner', async () => {
  const { assets } = await fixture()
  const a = await imported(assets)
  const b = await imported(assets)
  await Promise.all([
    assets.addReferences('draft:a', [a, a]),
    assets.addReferences('draft:a', [b]),
    assets.addReferences('other', [a])
  ])
  expect(new Set((await assets.state()).owners['draft:a'])).toEqual(new Set([a.id, b.id]))
  await assets.removeReferences('draft:a', [a, { id: 'not-registered' }])
  expect((await assets.state()).owners['draft:a']).toEqual([b.id])
  await assets.replaceReferences('draft:a', [a])
  await assets.releaseOwner('draft:a')
  await assets.releaseOwner('draft:a')
  expect((await assets.state()).owners.other).toEqual([a.id])
  await expect(assets.addReferences('__proto__', [a])).rejects.toThrow()
  await expect(assets.addReferences('bad', [{ id: 'missing' }])).rejects.toThrow()
  expect(await assets.read(a)).toBeInstanceOf(Uint8Array)
})

test('角色、身份、设置和新聊天各自持有完整资源，草稿交接不影响共享资源', async () => {
  const { assets, imports, characters, settings, store } = await fixture()
  const { draftId } = await imports.createDraft()
  const info = await imports.importAsset(source(), 'avatar', draftId)
  const saved = await characters.save(character(info.asset))
  await imports.commitDraft(draftId, { kind: 'character', id: saved.id })
  expect((await assets.state()).owners['draft:' + draftId]).toBeUndefined()
  const persona = await characters.savePersona({
    id: 'me',
    revision: 0,
    source: { kind: 'custom', displayName: '我', description: '', avatar: info.asset }
  })
  await store.save({ ...chat(), userPersona: await characters.resolvePersona('me') })
  await characters.deletePersona('me', persona.revision)
  await characters.delete(saved.id, saved.revision)
  expect(ownersOf(await assets.state(), info.asset)).toEqual(['chat:chat'])
  const audio = await imported(assets, 'audio')
  const configured = await settings.set('bgm.audio', audio)
  expect((await assets.state()).owners['settings:global']).toEqual([audio.id])
  await settings.set('bgm.audio', null, { expectedRevision: configured.revision })
  expect((await assets.state()).owners['settings:global']).toEqual([])
})

test('主动导入模型独立保留；草稿过期只释放引用，不删除资源', async () => {
  const { assets, imports } = await fixture()
  const { draftId } = await imports.createDraft()
  const model = await imports.importAsset(source('weights.bin'), 'model', draftId)
  await imports.discardDraft(draftId)
  expect(ownersOf(await assets.state(), model.asset)).toEqual(['library:' + model.libraryItemId])
  expect(await imports.listLibraryItems()).toHaveLength(1)
  await imports.removeLibraryItem(model.libraryItemId)
  expect(ownersOf(await assets.state(), model.asset)).toEqual([])
  expect(await assets.read(model.asset)).toBeInstanceOf(Uint8Array)
  const second = await imports.createDraft()
  const image = await imports.importAsset(source(), 'chat-image', second.draftId)
  vi.spyOn(Date, 'now').mockReturnValue(Date.now() + 31 * 60 * 1000)
  await assets.maintain()
  expect(ownersOf(await assets.state(), image.asset)).toEqual([])
  await expect(imports.renewDraft(second.draftId)).rejects.toHaveProperty(
    'problem.code',
    'lease_expired'
  )
})

test('仅保留当前聊天版本，重复图片仍持有；备份与未知文件不清理', async () => {
  const { assets, store } = await fixture()
  const image = await imported(assets)
  const first = await store.save(chat([image, image]))
  const second = await store.save({ ...first, extensions: { resources: [image] } })
  await writeFile(path.join(store.directory, 'manual.backup'), 'backup')
  await writeFile(path.join(store.directory, 'unrelated.json'), '{}')
  await writeFile(path.join(store.directory, 'aborted.123.json'), '{}')
  await store.cleanupVersions()
  expect((await assets.state()).owners['chat:chat']).toEqual([image.id])
  expect(await readdir(store.directory)).toEqual([
    'chat.2.json',
    'index.json',
    'manual.backup',
    'unrelated.json'
  ])
  await store.delete(second.id, second.revision)
  expect((await assets.state()).owners['chat:chat']).toBeUndefined()
  expect(await readdir(store.directory)).toEqual(['index.json', 'manual.backup', 'unrelated.json'])
})

test('提交失败保留旧引用，提交后交接失败保留保护日志且不误报保存失败', async () => {
  const { assets, roots, store } = await fixture()
  const a = await imported(assets)
  const b = await imported(assets)
  const first = await store.save(chat([a]))
  await expect(
    assets.commitReferences('chat:chat', { ...first, extensions: { resources: [b] } }, async () => {
      throw new Error('发布前失败')
    })
  ).rejects.toThrow('发布前失败')
  expect((await assets.state()).owners['chat:chat']).toEqual([a.id])
  const settle = vi.spyOn(assets, 'settleCommit').mockRejectedValue(new Error('索引暂时不可写'))
  const second = await store.save({ ...first, extensions: { resources: [b] } })
  expect(second.revision).toBe(2)
  const pending = await assets.state()
  expect(Object.values(pending.commits)).toEqual(['chat:chat'])
  expect(ownersOf(pending, b).some((owner) => owner.startsWith('pending:'))).toBe(true)
  settle.mockRestore()
  const transient = await assets.createTemporaryOwner('test-runtime', [a])
  const restarted = new MediaAssets(roots)
  const restartedStore = new ChatStore({ directory: store.directory, assets: restarted })
  await restarted.initialize()
  const state = await restarted.state()
  expect(state.commits).toEqual({})
  expect(state.owners['chat:chat']).toEqual([b.id])
  expect(state.owners[transient]).toBeUndefined()
  expect((await restartedStore.load('chat')).chat.revision).toBe(2)
})

test('发布索引前中断时保留旧聊天，启动兜底删除未提交版本', async () => {
  const { assets, roots, store } = await fixture()
  const a = await imported(assets)
  await store.save(chat([a]))
  await writeJson(path.join(store.directory, 'chat.2.json'), {
    version: 1,
    chat: { ...chat(), revision: 2 }
  })
  const state = await assets.state()
  state.owners['pending:interrupted'] = [a.id]
  state.commits['pending:interrupted'] = 'chat:chat'
  await writeJson(assets.index, state)
  const restarted = new MediaAssets(roots)
  const nextStore = new ChatStore({ directory: store.directory, assets: restarted })
  await restarted.initialize()
  await nextStore.cleanupVersions()
  expect((await nextStore.load('chat')).chat.revision).toBe(1)
  expect(await readdir(store.directory)).toEqual(['chat.1.json', 'index.json'])
})

test('实际读取作用域结束才释放临时引用', async () => {
  const { assets } = await fixture()
  const asset = await imported(assets)
  await assets.using(asset, async (owner) => {
    await assets.releaseOwner('test-source')
    expect(ownersOf(await assets.state(), asset)).toEqual([owner])
    expect(await assets.read(asset)).toBeInstanceOf(Uint8Array)
  })
  expect(ownersOf(await assets.state(), asset)).toEqual([])
})

test('归档自包含，导入后交给聊天，导出保留期结束不影响已恢复资源', async () => {
  const { assets, store } = await fixture()
  const a = await imported(assets)
  const saved = await store.save(chat([a]))
  const archive = await store.exportArchive([saved.id])
  const drafts = await store.readArchive(archive)
  const restored = await store.save({ ...drafts.chats[0], id: 'restored', revision: 0 })
  await assets.releaseOwner(drafts.resourceOwner)
  const importedAsset = restored.extensions.resources[0]
  expect(ownersOf(await assets.state(), importedAsset)).toEqual(['chat:restored'])
  vi.spyOn(Date, 'now').mockReturnValue(Date.now() + 11 * 60 * 1000)
  await assets.maintain()
  expect(ownersOf(await assets.state(), archive)).toEqual([])
  expect(await assets.read(importedAsset)).toBeInstanceOf(Uint8Array)
})

test('场景与演出分别持有模型和音频，移除计划和关闭序列后释放', async () => {
  const { assets } = await fixture()
  const model = await imported(assets, 'model')
  const audio = await imported(assets, 'audio')
  const performance = new Performance({ assets })
  cleanups.push(() => performance.close())
  const scene = {
    background: null,
    slots: [{ id: 'slot', presentation: { model, mapping: null } }]
  }
  const sequence = await performance.createSequence(scene)
  const guides = await performance.appendGuides(sequence, [
    {
      slotId: 'slot',
      text: 'hello',
      ready: true,
      audio: { asset: audio, durationMs: 60000 },
      performance: { motion: 'auto', expression: 'auto' }
    }
  ])
  await assets.releaseOwner('test-source')
  expect(ownersOf(await assets.state(), model).some((owner) => owner.startsWith('scene:'))).toBe(
    true
  )
  expect(ownersOf(await assets.state(), audio).some((owner) => owner.startsWith('guide:'))).toBe(
    true
  )
  await performance.removeGuides(guides)
  await performance.closeSequence(sequence)
  await assets.queue.run('assets', async () => {})
  expect(ownersOf(await assets.state(), model)).toEqual([])
  expect(ownersOf(await assets.state(), audio)).toEqual([])
})

test('语音输入、驻留权重和输出独立持有，释放后查询不会重新导入结果', async () => {
  const { assets, directory } = await fixture()
  const gptWeights = await imported(assets, 'model')
  const sovitsWeights = await imported(assets, 'model')
  const referenceAudio = await imported(assets, 'audio')
  const file = path.join(directory, 'output.wav')
  await writeFile(file, 'audio')
  vi.stubGlobal(
    'fetch',
    vi.fn(
      async (url, options) =>
        new Response(
          JSON.stringify(
            options.method === 'POST'
              ? { task_id: 'run-task' }
              : { status: 'succeeded', audio_path: file, duration_ms: 10 }
          )
        )
    )
  )
  const speech = new Speech({
    assets,
    connect: async () => ({ url: 'http://localhost', token: '', runId: 'run' })
  })
  cleanups.push(() => speech.processStopped())
  const id = await speech.submit({
    priority: 1,
    text: 'hello',
    language: 'Chinese',
    voice: { gptWeights, sovitsWeights, referenceAudio }
  })
  await assets.releaseOwner('test-source')
  const result = await speech.getTask(id)
  expect(ownersOf(await assets.state(), referenceAudio)).toEqual([])
  expect(
    ownersOf(await assets.state(), gptWeights).some((owner) => owner.startsWith('speech-model:'))
  ).toBe(true)
  await assets.addReferences('chat:kept', [result.audio.asset])
  await speech.releaseTask(id)
  expect(ownersOf(await assets.state(), result.audio.asset)).toEqual(['chat:kept'])
  await expect(speech.getTask(id)).rejects.toHaveProperty('problem.code', 'task_unavailable')
  expect(speech.transport.results.size).toBe(0)
  await speech.processStopped()
  expect(ownersOf(await assets.state(), gptWeights)).toEqual([])
})

test('后台维护完成失败的提交交接，不需要重写业务数据或重启', async () => {
  const { assets, store } = await fixture()
  const image = await imported(assets)
  const settle = vi.spyOn(assets, 'settleCommit').mockRejectedValue(new Error('暂时失败'))
  await store.save(chat([image]))
  expect(Object.values((await assets.state()).commits)).toEqual(['chat:chat'])
  settle.mockRestore()
  await assets.maintain()
  expect((await assets.state()).commits).toEqual({})
  expect((await assets.state()).owners['chat:chat']).toEqual([image.id])
})

test('异常退出后仍存活的子进程引用保留，确认进程结束后维护释放', async () => {
  const { assets, roots, store } = await fixture()
  const model = await imported(assets, 'model')
  const owner = await assets.createTemporaryOwner('speech-model', [model])
  await assets.bindOwnerProcess(owner, process.pid)
  const restarted = new MediaAssets(roots)
  new ChatStore({ directory: store.directory, assets: restarted })
  await restarted.initialize()
  expect((await restarted.state()).owners[owner]).toEqual([model.id])
  vi.spyOn(process, 'kill').mockImplementation(() => {
    throw Object.assign(new Error('gone'), { code: 'ESRCH' })
  })
  await restarted.maintain()
  expect((await restarted.state()).owners[owner]).toBeUndefined()
})

test('取消等待不提前释放运行输入，迟到成功不会导入无人接收的音频', async () => {
  const { assets } = await fixture()
  const gptWeights = await imported(assets, 'model')
  const sovitsWeights = await imported(assets, 'model')
  const referenceAudio = await imported(assets, 'audio')
  let respond
  let requested
  const started = new Promise((resolve) => {
    requested = resolve
  })
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url, options) => {
      if (options.method === 'POST') return new Response(JSON.stringify({ task_id: 'run-task' }))
      requested()
      return new Promise((resolve) => {
        respond = resolve
      })
    })
  )
  const speech = new Speech({
    assets,
    connect: async () => ({ url: 'http://localhost', token: '', runId: 'run' })
  })
  cleanups.push(() => speech.processStopped())
  const id = await speech.submit({
    priority: 1,
    text: 'hello',
    language: 'Chinese',
    voice: { gptWeights, sovitsWeights, referenceAudio }
  })
  await assets.releaseOwner('test-source')
  const controller = new AbortController()
  const waiter = speech.waitForResult(id, controller.signal)
  const rejected = expect(waiter).rejects.toHaveProperty('name', 'AbortError')
  await started
  controller.abort()
  await rejected
  await speech.releaseTask(id)
  expect(
    ownersOf(await assets.state(), referenceAudio).some((owner) =>
      owner.startsWith('speech-input:')
    )
  ).toBe(true)
  const terminal = Promise.allSettled([...speech.waiting.values()])
  respond(
    new Response(
      JSON.stringify({ status: 'succeeded', audio_path: '/unused.wav', duration_ms: 10 })
    )
  )
  await terminal
  expect(ownersOf(await assets.state(), referenceAudio)).toEqual([])
  expect(Object.keys((await assets.state()).assets)).toHaveLength(3)
})

test('关闭后端取消独立重合成的等待，并释放运行引用', async () => {
  const { assets, roots } = await fixture()
  const gptWeights = await imported(assets, 'model')
  const sovitsWeights = await imported(assets, 'model')
  const referenceAudio = await imported(assets, 'audio')
  let waiting
  const started = new Promise((resolve) => {
    waiting = resolve
  })
  const speech = {
    submit: vi.fn(async () => 'task'),
    cancel: vi.fn(async () => {}),
    releaseTask: vi.fn(async () => {}),
    waitForResult: vi.fn(async (id, signal) => {
      waiting()
      return new Promise((resolve, reject) => {
        function abort() {
          reject(new DOMException('关闭', 'AbortError'))
        }
        signal.addEventListener('abort', abort, { once: true })
        if (signal.aborted) abort()
      })
    })
  }
  const backend = await createCoreBackend({ ...roots, speech, scanReminders: false })
  cleanups.push(() => backend.close())
  const definition = character(null)
  definition.forms[0].voice = {
    gptWeights,
    sovitsWeights,
    defaultReference: { audio: referenceAudio, text: '参考', language: 'Chinese' },
    emotions: {}
  }
  await backend.characters.save(definition)
  const chatId = await backend.conversations.createChat({
    title: 'test',
    characterIds: ['sakiko'],
    mode: 'single-character'
  })
  const loaded = await backend.store.load(chatId)
  loaded.chat.turns.push({
    id: 'turn',
    meta: {},
    messages: [
      {
        id: 'message',
        kind: 'assistant',
        parts: [
          {
            id: 'part',
            kind: 'dialogue',
            lines: [
              {
                id: 'line',
                speakerId: 'sakiko',
                formId: 'default',
                text: '你好',
                emotion: null,
                audio: null,
                spoken: { text: '你好', language: 'Chinese', pronunciationOverrides: [] },
                performance: { motion: 'auto', expression: 'auto' }
              }
            ]
          }
        ]
      }
    ]
  })
  await backend.store.save(loaded.chat)
  await backend.conversations.regenerateAudio(chatId, {
    turnId: 'turn',
    messageId: 'message',
    partId: 'part',
    lineId: 'line'
  })
  await started
  await backend.close()
  expect(speech.cancel).toHaveBeenCalledWith(['task'])
  expect(speech.releaseTask).toHaveBeenCalledWith('task')
  const state = await backend.assets.state()
  expect(Object.keys(state.temporaryOwners)).toEqual([])
})
