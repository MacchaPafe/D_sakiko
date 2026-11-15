import { expect, test } from 'vitest'
import {
  normalizeImportedPythonChat,
  normalizePythonToolOrder
} from '../../src/backend/storage/python-normalization.js'
function fixture() {
  const user = { id: 'u', kind: 'user', source: { kind: 'manual' }, content: { text: '问题' } }
  const reply = {
    id: 'a',
    kind: 'assistant',
    parts: [{ kind: 'dialogue', lines: [{ id: 'l', speakerId: 'role', text: '回答' }] }],
    protocol: {}
  }
  const call = {
    id: 'c',
    kind: 'assistant',
    parts: [{ id: 'p', kind: 'tool-call', callId: 'call', toolName: 'weather' }],
    protocol: {},
    extensions: { migration: { reconstructed: true } }
  }
  const result = {
    id: 'r',
    kind: 'tool-result',
    callId: 'call',
    assistantMessageId: 'c',
    outcome: { status: 'succeeded', value: '晴' },
    extensions: { pythonDisplay: { message_index: 1 } }
  }
  const chat = {
    id: 'chat',
    characterIds: ['role'],
    turns: [{ id: 't', messages: [user, reply, call, result] }],
    meta: {
      settings: { characters: {} },
      extensions: {
        pythonImport: { source: 'source' },
        python: {
          settings: { live2d_models: { 角色: 'live2d_related/role/extra/model.model3.json' } }
        }
      }
    }
  }
  const scan = {
    identity: 'source',
    root: '/source',
    roles: [
      {
        id: 'role',
        name: '角色',
        forms: [
          {
            id: 'default',
            models: [{ id: 'extra', file: 'live2d_related/role/extra/model.model3.json' }]
          }
        ]
      }
    ]
  }
  const characters = {
    get: async () => ({
      defaultFormId: 'default',
      forms: [{ id: 'default', models: [{ id: 'extra' }] }]
    })
  }
  return { chat, scan, characters, call, result, reply }
}
test('一次性整理模型和工具消息，保留身份、内容及用户输入位置', async () => {
  const { chat, scan, characters } = fixture()
  const originalMessages = structuredClone(chat.turns[0].messages)
  expect(await normalizeImportedPythonChat(chat, scan, characters)).toMatchObject({
    changed: true,
    models: 1,
    calls: 1,
    warnings: []
  })
  expect(chat.meta.settings.characters.role).toEqual({
    formId: 'default',
    modelOverrides: { default: 'extra' }
  })
  expect(chat.turns[0].messages.map((m) => m.id)).toEqual(['u', 'c', 'r', 'a'])
  expect([...chat.turns[0].messages].sort((a, b) => a.id.localeCompare(b.id))).toEqual(
    originalMessages.sort((a, b) => a.id.localeCompare(b.id))
  )
  expect(await normalizeImportedPythonChat(chat, scan, characters)).toMatchObject({
    changed: false,
    calls: 0
  })
})
test('多次调用同锚点保持原顺序，原生调用不使用旧展示索引', () => {
  const { chat, call, result } = fixture()
  const native = { ...structuredClone(call), id: 'native', extensions: {} }
  const second = {
    ...structuredClone(call),
    id: 'second',
    parts: [{ kind: 'tool-call', callId: 'second-call' }]
  }
  const secondResult = {
    ...structuredClone(result),
    id: 'second-result',
    callId: 'second-call',
    assistantMessageId: 'second'
  }
  chat.turns[0].messages.push(second, secondResult, native)
  expect(normalizePythonToolOrder(chat, [])).toBe(2)
  expect(chat.turns[0].messages.map((m) => m.id)).toEqual([
    'u',
    'c',
    'r',
    'second',
    'second-result',
    'a',
    'native'
  ])
})
test.each(['missing', 'cross-turn', 'inside', 'protocol', 'unpaired'])(
  '不猜测不安全的工具位置：%s',
  (condition) => {
    const { chat, call, result, reply } = fixture()
    if (condition === 'missing') result.extensions.pythonDisplay.message_index = 50
    if (condition === 'cross-turn') {
      chat.turns.push({ id: 'next', messages: [reply] })
      chat.turns[0].messages.splice(1, 1)
    }
    if (condition === 'inside') {
      reply.parts[0].lines.push({ id: 'l2', text: '第二句' })
      result.extensions.pythonDisplay.message_index = 2
    }
    if (condition === 'protocol') call.protocol = { opaque: 'keep' }
    if (condition === 'unpaired') result.assistantMessageId = 'other'
    const before = structuredClone(chat)
    const warnings = []
    expect(normalizePythonToolOrder(chat, warnings)).toBe(0)
    expect(chat).toEqual(before)
    expect(warnings[0].code).toBe('legacy_tool_order_unresolved')
  }
)
test('修复不覆盖本地模型选择；缺失模型明确报告', async () => {
  const first = fixture()
  first.chat.meta.settings.characters.role = {
    formId: 'default',
    modelOverrides: { default: 'user-chosen' }
  }
  expect((await normalizeImportedPythonChat(first.chat, first.scan, first.characters)).models).toBe(
    0
  )
  expect(first.chat.meta.settings.characters.role.modelOverrides.default).toBe('user-chosen')
  const next = fixture()
  next.scan.roles[0].forms[0].models = []
  const result = await normalizeImportedPythonChat(next.chat, next.scan, next.characters)
  expect(result.warnings[0].code).toBe('legacy_chat_model_unavailable')
  expect(next.chat.meta.settings.characters).toEqual({})
})
