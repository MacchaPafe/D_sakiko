import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { afterEach, expect, test, vi } from 'vitest'
import { createCoreBackend } from '../../src/backend/create-core-backend.js'
import { Performance } from '../../src/backend/performance/interface.js'
import { selectPersonaCharacter } from '../../src/renderer/src/features/personas/source.js'

const cleanups = []
afterEach(async () => {
  for (const cleanup of cleanups.splice(0).reverse()) await cleanup()
})

function form(id, avatar = null) {
  return {
    id,
    displayName: id,
    description: id + ' 的设定',
    avatar,
    themeColor: null,
    models: [],
    defaultModelId: null,
    voice: null
  }
}
function character(id, forms) {
  return {
    id,
    revision: 0,
    defaultFormId: forms[0].id,
    forms,
    knowledgeMappings: [],
    extensions: {}
  }
}
function persona(formId = 'second') {
  return { id: 'me', revision: 0, source: { kind: 'character', characterId: 'identity', formId } }
}
async function fixture() {
  const directory = await mkdtemp(path.join(tmpdir(), 'dsakiko-persona-'))
  cleanups.push(() => rm(directory, { recursive: true, force: true }))
  const agent = {
    inspectContext: async () => ({ fit: 'fits' }),
    run: vi.fn(async (request, observer) => {
      await observer.onResponse({
        responseId: 'response-' + request.turnId,
        protocol: {},
        appendParts: [
          {
            id: 'part-' + request.turnId,
            kind: 'dialogue',
            lines: [
              {
                id: 'line-' + request.turnId,
                speakerId: 'ai',
                formId: 'default',
                text: '好',
                translation: null,
                emotion: null,
                performance: { motion: 'auto', expression: 'auto' }
              }
            ]
          }
        ]
      })
      return { status: 'completed', usage: null }
    })
  }
  const backend = await createCoreBackend({
    appDataRoot: path.join(directory, 'app'),
    resourceLibraryRoot: path.join(directory, 'library'),
    cacheRoot: path.join(directory, 'cache'),
    scanReminders: false,
    agent,
    performance: new Performance({ readingMsPerCharacter: 1, minimumReadingMs: 1 })
  })
  cleanups.push(() => backend.close())
  await backend.characters.save(character('ai', [form('default')]))
  await backend.characters.save(character('identity', [form('first'), form('second')]))
  return { backend, agent }
}
function createChat(backend) {
  return backend.conversations.createChat({
    title: '测试',
    characterIds: ['ai'],
    mode: 'single-character',
    userPersonaId: 'me'
  })
}
async function avatar(backend, text) {
  return (
    await backend.assets.importAsset(
      { kind: 'bytes', name: text + '.png', mediaType: 'image/png', bytes: Buffer.from(text) },
      'source',
      'avatar'
    )
  ).asset
}

test('对话身份绑定非默认形态，内容更新可见且默认值变化不会改绑', async () => {
  const { backend } = await fixture()
  const catalog = backend.characters
  const saved = await catalog.savePersona(persona())
  expect(await catalog.resolvePersona('me')).toMatchObject({
    sourceCharacterId: 'identity',
    sourceFormId: 'second',
    displayName: 'second',
    description: 'second 的设定'
  })
  const definition = await catalog.get('identity')
  definition.forms.push(form('third'))
  definition.defaultFormId = 'third'
  definition.forms[1].displayName = '新的名字'
  definition.forms[1].description = '新的设定'
  await catalog.save(definition)
  expect(await catalog.resolvePersona('me')).toMatchObject({
    sourceFormId: 'second',
    displayName: '新的名字',
    description: '新的设定'
  })
  expect(await catalog.getPersona('me')).toEqual(saved)
  expect((await catalog.list()).find((item) => item.id === 'identity')).toMatchObject({
    defaultFormId: 'third'
  })
  expect(await catalog.listPersonas()).toEqual([
    { id: 'me', displayName: '新的名字', avatar: null, problem: null }
  ])
})

test('保存拒绝旧角色引用、错误类型和不存在的形态，失败不更改定义', async () => {
  const { backend } = await fixture()
  const saved = await backend.characters.savePersona(persona())
  const sources = [
    [{ kind: 'character', characterId: 'identity' }, 'invalid_input'],
    [{ kind: 'character', characterId: 'identity', formId: null }, 'invalid_input'],
    [{ kind: 'character', characterId: 'identity', formId: '' }, 'invalid_input'],
    [{ kind: 'character', characterId: 'identity', formId: 2 }, 'invalid_input'],
    [{ kind: 'character', characterId: 'missing', formId: 'second' }, 'character_missing'],
    [{ kind: 'character', characterId: 'ai', formId: 'second' }, 'form_missing'],
    [
      { kind: 'character', characterId: 'identity', formId: 'second', description: '复制内容' },
      'invalid_input'
    ]
  ]
  for (const [source, code] of sources) {
    await expect(backend.characters.savePersona({ ...saved, source })).rejects.toHaveProperty(
      'problem.code',
      code
    )
    expect(await backend.characters.getPersona('me')).toEqual(saved)
  }
})

test('来源形态或角色删除后保留失效身份，不自动切换默认形态', async () => {
  const { backend } = await fixture()
  const catalog = backend.characters
  await catalog.savePersona(persona())
  let definition = await catalog.get('identity')
  definition.forms = [definition.forms[0]]
  definition = await catalog.save(definition)
  await expect(catalog.resolvePersona('me')).rejects.toHaveProperty('problem.code', 'form_missing')
  await expect(createChat(backend)).rejects.toHaveProperty('problem.code', 'form_missing')
  expect(await catalog.listPersonas()).toMatchObject([
    { id: 'me', displayName: null, avatar: null, problem: { code: 'form_missing' } }
  ])
  await catalog.delete('identity', definition.revision)
  await expect(catalog.resolvePersona('me')).rejects.toHaveProperty(
    'problem.code',
    'character_missing'
  )
  expect(await catalog.listPersonas()).toMatchObject([
    { id: 'me', problem: { code: 'character_missing' } }
  ])
  expect(await catalog.getPersona('me')).not.toBeNull()
})

test('自定义身份快照的两个来源编号均为 null', async () => {
  const { backend } = await fixture()
  const source = { kind: 'custom', displayName: '我', description: '自定义', avatar: null }
  await backend.characters.savePersona({ id: 'me', revision: 0, source })
  expect(await backend.characters.resolvePersona('me')).toEqual({
    personaId: 'me',
    sourceCharacterId: null,
    sourceFormId: null,
    displayName: '我',
    description: '自定义',
    avatar: null
  })
})

test('聊天固定形态与头像，来源变化或删除不影响分支和归档导入', async () => {
  const { backend } = await fixture()
  const oldAvatar = await avatar(backend, 'old')
  const newAvatar = await avatar(backend, 'new')
  let definition = await backend.characters.get('identity')
  definition.forms[1].avatar = oldAvatar
  definition = await backend.characters.save(definition)
  const savedPersona = await backend.characters.savePersona(persona())
  const id = await createChat(backend)
  const snapshot = (await backend.store.load(id)).chat.userPersona
  expect(snapshot).toMatchObject({ sourceFormId: 'second', avatar: oldAvatar })
  definition.forms[1] = { ...definition.forms[1], description: '新设定', avatar: newAvatar }
  definition = await backend.characters.save(definition)
  const newer = await createChat(backend)
  expect((await backend.store.load(newer)).chat.userPersona).toMatchObject({
    description: '新设定',
    avatar: newAvatar
  })
  await backend.characters.deletePersona('me', savedPersona.revision)
  await backend.characters.delete('identity', definition.revision)
  await backend.assets.releaseOwner('source')
  expect((await backend.assets.collectUnused()).deleted).not.toContainEqual(oldAvatar)
  expect((await backend.store.load(id)).chat.userPersona).toEqual(snapshot)
  const branch = await backend.conversations.forkChat(id, null)
  expect((await backend.store.load(branch)).chat.userPersona).toEqual(snapshot)
  const archive = await backend.conversations.exportChats([id])
  const imported = await backend.conversations.importChats(archive)
  const restored = (await backend.store.load(imported.chatIds[0])).chat.userPersona
  expect(restored).toMatchObject({
    personaId: snapshot.personaId,
    sourceCharacterId: 'identity',
    sourceFormId: 'second',
    description: snapshot.description
  })
  expect(Buffer.from(await backend.assets.read(restored.avatar)).toString()).toBe('old')
  await backend.conversations.deleteChat(id)
  expect((await backend.assets.collectUnused()).deleted).not.toContainEqual(oldAvatar)
  expect(Buffer.from(await backend.assets.read(snapshot.avatar)).toString()).toBe('old')
})

test('继续对话和重生成仍把创建时的人格文本交给模型', async () => {
  const { backend, agent } = await fixture()
  await backend.characters.savePersona(persona())
  await backend.settings.saveConnection(
    {
      id: 'local',
      revision: 0,
      name: 'test',
      provider: 'openai-compatible',
      protocol: 'chat',
      baseUrl: 'http://127.0.0.1:9999/v1',
      attachmentTransport: 'inline',
      models: [{ id: 'test', declared: { contextTokens: 10000 }, overrides: {} }]
    },
    { kind: 'replace', secret: 'test' }
  )
  await backend.settings.update({ generation: { connectionId: 'local', modelId: 'test' } })
  const id = await createChat(backend)
  const definition = await backend.characters.get('identity')
  await backend.characters.delete('identity', definition.revision)
  const turnId = await backend.conversations.createTurn(
    id,
    { text: '你好', attachments: [] },
    { requestId: 'first' }
  )
  await expect
    .poll(async () => (await backend.conversations.getSnapshot(id)).admission.canGenerate)
    .toBe(true)
  await backend.conversations.recreateTurn(id, turnId, { requestId: 'again' })
  await expect
    .poll(async () => (await backend.conversations.getSnapshot(id)).admission.canGenerate)
    .toBe(true)
  expect(agent.run).toHaveBeenCalledTimes(2)
  for (const [request] of agent.run.mock.calls)
    expect(request.userPersona).toEqual({ displayName: 'second', description: 'second 的设定' })
})

test('表单创建或切换角色时填入默认形态，编辑已有身份保留非默认形态', async () => {
  const { backend } = await fixture()
  const summaries = await backend.characters.list()
  const identity = summaries.find((item) => item.id === 'identity')
  const ai = summaries.find((item) => item.id === 'ai')
  const initial = selectPersonaCharacter({ kind: 'custom' }, identity)
  expect(initial).toEqual({ kind: 'character', characterId: 'identity', formId: 'first' })
  await backend.characters.savePersona({ id: 'me', revision: 0, source: initial })
  const existing = persona().source
  expect(selectPersonaCharacter(existing, identity)).toEqual(existing)
  expect(selectPersonaCharacter(existing, ai)).toEqual({
    kind: 'character',
    characterId: 'ai',
    formId: 'default'
  })
  expect(selectPersonaCharacter(existing, undefined)).toEqual({
    kind: 'character',
    characterId: '',
    formId: ''
  })
})
