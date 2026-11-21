import { afterEach, expect, test } from 'vitest'
import { mkdtemp, mkdir, writeFile, readFile, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { createCoreBackend } from '../../src/backend/create-core-backend.js'
import {
  markPythonToolPlaceholders,
  PythonChatImporter,
  pythonChatId
} from '../../src/backend/storage/python-import-interface.js'
import { chatSettings } from '../../src/backend/core/defaults.js'
import { fingerprint } from '../../src/backend/core/support.js'
import { scanLegacyDirectory } from '../../src/backend/setup/legacy.js'
const cleanup = []
afterEach(async () => {
  for (const close of cleanup.splice(0).reverse()) await close()
})
function timeline() {
  return {
    id: 'python-original',
    revision: 8,
    title: '原有标题',
    characterIds: ['anon'],
    mode: 'single-character',
    userPersona: null,
    meta: {
      settings: chatSettings(),
      reminders: [{ id: 'do-not-run' }],
      extensions: {
        acceptedRequests: { old: {} },
        python: {
          resources: { audio: '../reference_audio/test.wav' },
          characterNames: { anon: '爱音' }
        }
      }
    },
    turns: [
      {
        id: 'turn',
        input: { kind: 'message', messageId: 'user' },
        meta: { requestId: 'old', extensions: {} },
        messages: [
          {
            id: 'user',
            kind: 'user',
            content: { text: '你好', attachments: [] },
            source: { kind: 'manual' },
            extensions: {}
          },
          {
            id: 'assistant',
            kind: 'assistant',
            protocol: { opaque: 'keep' },
            extensions: {},
            parts: [
              { id: 'reasoning', kind: 'reasoning', text: '历史思考' },
              { id: 'call', kind: 'tool-call', callId: 'call-id', name: 'lookup', arguments: {} },
              {
                id: 'dialogue',
                kind: 'dialogue',
                lines: [
                  {
                    id: 'line',
                    speakerId: 'anon',
                    formId: null,
                    text: 'こんにちは',
                    translation: '你好呀',
                    audio: { asset: { id: 'audio' }, durationMs: 1000 }
                  }
                ]
              }
            ]
          },
          {
            id: 'result',
            kind: 'tool-result',
            assistantMessageId: 'assistant',
            callId: 'call-id',
            outcome: { kind: 'success' },
            extensions: {}
          }
        ]
      }
    ]
  }
}
async function fixture() {
  const root = await mkdtemp(path.join(tmpdir(), 'sakiko-python-format-'))
  cleanup.push(() => rm(root, { recursive: true, force: true }))
  const backend = await createCoreBackend({
    appDataRoot: path.join(root, 'app'),
    resourceLibraryRoot: path.join(root, 'library'),
    cacheRoot: path.join(root, 'cache'),
    scanReminders: false
  })
  cleanup.push(() => backend.close())
  const source = path.join(root, 'python')
  await mkdir(path.join(source, 'reference_audio'), { recursive: true })
  await writeFile(path.join(source, 'reference_audio/test.wav'), 'audio-fixture')
  const original = timeline()
  await writeFile(
    path.join(source, 'reference_audio/all_conversation.json'),
    JSON.stringify({ conversation_schema_version: 1, chat_list: [original] })
  )
  return { root, source, original, backend }
}
test('新格式经过真实扫描和导入保留历史关联，迁移资源并在重试时去重', async () => {
  const { backend, source, original } = await fixture()
  const scan = await backend.setup.scanLegacy(
    (await backend.setup.grantLegacySource(source)).sourceId
  )
  expect(scan.chatCount).toBe(1)
  const plan = await backend.setup.createPlan({ kind: 'legacy', scanId: scan.scanId })
  await backend.setup.start(plan.id)
  await expect.poll(async () => (await backend.setup.getJob(plan.id)).status).toBe('succeeded')
  const saved = (await backend.store.load(pythonChatId(original))).chat
  expect(saved).toMatchObject({
    title: original.title,
    characterIds: ['anon'],
    revision: 1,
    meta: { reminders: [] }
  })
  expect(saved.turns[0].messages[1].protocol).toEqual({ opaque: 'keep' })
  expect(saved.turns[0].messages[2].assistantMessageId).toBe('assistant')
  expect(saved.turns[0].messages[1].parts[2].lines[0].formId).toBeNull()
  expect(saved.meta.extensions.acceptedRequests).toBeUndefined()
  const audio = saved.turns[0].messages[1].parts[2].lines[0].audio.asset
  expect(audio.id).not.toBe('audio')
  expect(Buffer.from(await backend.assets.read(audio)).toString()).toBe('audio-fixture')
  // 已有索引首次补齐预览，不能为了刷新摘要而增加聊天版本。
  const oldIndex = JSON.parse(await readFile(backend.store.manifest, 'utf8'))
  oldIndex.entries.forEach((entry) => delete entry.lastReplyTranslation)
  await writeFile(backend.store.manifest, JSON.stringify(oldIndex))
  expect((await backend.store.list())[0]).toMatchObject({
    lastReply: 'こんにちは',
    lastReplyTranslation: '你好呀',
    revision: saved.revision
  })
  expect(
    JSON.parse(await readFile(backend.store.manifest, 'utf8')).entries[0].lastReplyTranslation
  ).toBe('你好呀')
  const next = await backend.setup.createPlan({ kind: 'legacy', scanId: scan.scanId })
  await backend.setup.start(next.id)
  await expect.poll(async () => (await backend.setup.getJob(next.id)).status).toBe('succeeded')
  expect(await backend.store.list()).toHaveLength(1)
})
test('显式修复保留空占位 ID，保护有效记录，并以源 ID 去重', async () => {
  const { backend, source, original } = await fixture()
  const scan = await scanLegacyDirectory(source, [], [])
  const id = 'python-' + fingerprint(`python-chat:${fingerprint(original)}`).slice(0, 32)
  const empty = await backend.store.save({
    id,
    revision: 0,
    title: '旧版对话 1',
    mode: 'single-character',
    characterIds: [],
    userPersona: null,
    turns: [],
    meta: {
      settings: chatSettings(),
      reminders: [],
      extensions: { pythonImport: { source: scan.identity, chatId: null } }
    }
  })
  const importer = new PythonChatImporter(backend)
  expect((await importer.read(scan, {}, {})).chats).toHaveLength(0)
  const result = await importer.read(scan, {}, {}, null, { repairEmpty: true })
  expect(result.chats[0]).toMatchObject({ id, revision: empty.revision, characterIds: ['anon'] })
  expect(result.warnings).toContainEqual({
    code: 'legacy_chat_media_unavailable',
    chatId: id,
    assetId: 'audio'
  })
  expect(result.chats[0].turns[0].messages[1].parts[2].lines[0].audio).toBeNull()
  await backend.store.save(result.chats[0])
  scan.chats[0].title = 'Python 后续修改'
  expect((await importer.read(scan, {}, {}, null, { repairEmpty: true })).chats).toHaveLength(0)
  expect((await backend.store.load(id)).chat.title).toBe(original.title)
})
test('未知 schema 和错误消息结构不会报告可导入聊天', async () => {
  const { source, original } = await fixture()
  const file = path.join(source, 'reference_audio/all_conversation.json')
  await writeFile(file, JSON.stringify({ conversation_schema_version: 2, chat_list: [original] }))
  let scan = await scanLegacyDirectory(source, [], [])
  expect(scan.chats).toEqual([])
  expect(scan.warnings[0].code).toBe('legacy_chat_version_unsupported')
  original.turns[0].messages[1].parts = null
  await writeFile(file, JSON.stringify({ conversation_schema_version: 1, chat_list: [original] }))
  scan = await scanLegacyDirectory(source, [], [])
  expect(scan.chats).toEqual([])
  expect(scan.warnings[0].code).toBe('legacy_chat_invalid')
})

test('单条损坏聊天不阻断其余记录，并从 Python 目录还原图片附件和小剧场设置', async () => {
  const { backend, source, original } = await fixture()
  original.mode = 'scripted-dialogue'
  original.meta.extensions.python.settings = {
    theater: {
      situation: '排练结束后',
      character_0: { talk_style: '自然', interaction_details: '与对方讨论练习' }
    }
  }
  const attachment = 'chat_attachments/session/picture[1].png'
  original.meta.extensions.python.resources.picture = attachment
  original.turns[0].messages[0].content.attachments.push({
    asset: { id: 'picture' },
    name: 'picture[1].png',
    mediaType: 'image/png'
  })
  await mkdir(path.join(source, 'reference_audio/chat_attachments/session'), { recursive: true })
  await writeFile(path.join(source, 'reference_audio', attachment), 'image-fixture')
  const flat = {
    chat_id: 'flat',
    message_list: [{ character_name: 'User', text: '旧格式', attachments: [{ path: attachment }] }]
  }
  await writeFile(
    path.join(source, 'reference_audio/all_conversation.json'),
    JSON.stringify({ conversation_schema_version: 1, chat_list: [null, original, flat] })
  )
  const scan = await backend.setup.scanLegacy(
    (await backend.setup.grantLegacySource(source)).sourceId
  )
  expect(scan.chatCount).toBe(2)
  expect(scan.warnings).toEqual([
    expect.objectContaining({ code: 'legacy_chat_invalid', position: 1 })
  ])
  const plan = await backend.setup.createPlan({ kind: 'legacy', scanId: scan.scanId })
  await backend.setup.start(plan.id)
  await expect.poll(async () => (await backend.setup.getJob(plan.id)).status).toBe('succeeded')
  const { chat } = await backend.store.load(pythonChatId(original))
  expect(chat.meta.settings.scenario).toEqual({
    situation: '排练结束后',
    characters: { anon: { speakingStyle: '自然', interaction: '与对方讨论练习' } }
  })
  const [image] = chat.turns[0].messages[0].content.attachments
  expect(Buffer.from(await backend.assets.read(image.asset)).toString()).toBe('image-fixture')
  const { chat: flatChat } = await backend.store.load(pythonChatId(flat))
  expect(flatChat.title).toBe('旧版对话 3')
  expect(flatChat.turns[0].messages[0].content.attachments[0].asset).toEqual(image.asset)
})

test('旧版工具占位保留在历史中，只从回放中跳过；重复导入可补标', async () => {
  const { backend } = await fixture()
  const placeholder = {
    character_name: '爱音',
    text: '...',
    translation: '',
    audio_path: 'NO_AUDIO'
  }
  const original = {
    chat_id: 'tool-placeholder',
    name: '过渡台词',
    type: 1,
    prompt_config: { character_name: '爱音' },
    message_list: [
      { character_name: 'User', text: '请查一下' },
      placeholder,
      { ...placeholder, text: '…' },
      { ...placeholder, translation: '请等一下' },
      { ...placeholder, audio_path: '' },
      placeholder,
      { ...placeholder, text: '查到了。' }
    ],
    meta: {
      tool_call_records: [1, 2, 3, 4].map((message_index) => ({ message_index }))
    }
  }
  const importer = new PythonChatImporter(backend)
  const scan = { identity: 'legacy-backup', roles: [], media: {}, chats: [original] }
  const {
    chats: [draft]
  } = await importer.read(scan, {}, {})
  const turn = draft.turns[0]
  const lines = turn.messages.flatMap((message) => message.parts?.[0].lines ?? [])
  expect(lines.map((line) => Boolean(line.extensions.pythonToolPlaceholder))).toEqual([
    true,
    false,
    false,
    false,
    false,
    false
  ])

  // 模拟之前已导入且原始关联尚未迁移的存档。
  delete lines[0].extensions.pythonToolPlaceholder
  const saved = await backend.store.save(draft)
  const { replayUpdates } = await importer.read(scan, {}, {})
  expect(replayUpdates).toHaveLength(1)
  await backend.conversations.update(saved.id, (chat) =>
    markPythonToolPlaceholders(chat, replayUpdates[0].lineIds)
  )
  expect((await importer.read(scan, {}, {})).replayUpdates).toEqual([])

  const target = { kind: 'turn', turnId: turn.id }
  const { guides } = await backend.conversations.getReplayMaterials(saved.id, target)
  expect(guides.map((guide) => guide.text)).toEqual(['…', '...', '...', '...', '查到了。'])
  expect(guides[0].pauseBeforeMs).toBeUndefined()
  expect(guides[1].pauseBeforeMs).toBeGreaterThan(0)
  const stored = (await backend.store.load(saved.id)).chat
  expect(stored.turns[0].messages[1].parts[0].lines[0].text).toBe('...')
})
