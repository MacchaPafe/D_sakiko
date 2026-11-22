import { expect, test } from 'vitest'
import { mkdtemp, mkdir, readFile, rm, writeFile } from 'node:fs/promises'
import { existsSync } from 'node:fs'
import { execFileSync } from 'node:child_process'
import { resolve, join } from 'node:path'
import { tmpdir } from 'node:os'
import { zipSync, strToU8, unzipSync, strFromU8 } from 'fflate'
import { encodeArchive, decodeArchive } from '../../src/backend/storage/portable-archive.js'
import { chatSettings } from '../../src/backend/core/defaults.js'

function fixture() {
  const stored = new Map()
  function put(id, bytes, purpose) {
    const info = {
      asset: { id },
      name: id,
      kind: 'file',
      purpose,
      mediaType: 'application/octet-stream'
    }
    stored.set(id, { info, bytes })
    return info
  }
  put('recording', Buffer.from('recording'), 'audio')
  put('document', Buffer.from('notes'), 'workspace')
  const assets = {
    read: (ref) => stored.get(ref.id).bytes,
    inspect: (ref) => stored.get(ref.id).info,
    importAsset: (source, owner, purpose) => put(crypto.randomUUID(), source.bytes, purpose)
  }
  function line(id) {
    return {
      id,
      speakerId: 's',
      formId: 'default',
      text: '同一句',
      translation: '訳',
      emotion: 'like',
      performance: { motion: 'auto', expression: 'auto' },
      spoken: { text: '同一句', language: 'ja', pronunciationOverrides: [] },
      audio: { asset: { id: 'recording' }, durationMs: 120 },
      extensions: {}
    }
  }
  const chat = {
    id: 'example',
    revision: 1,
    title: '档案',
    characterIds: ['s'],
    mode: 'single-character',
    meta: {
      settings: chatSettings(),
      reminders: [],
      workspaceFiles: [{ name: '资料.txt', asset: { id: 'document' }, mediaType: 'text/plain' }],
      extensions: { characterNames: { s: '祥子' } }
    },
    turns: [
      {
        id: 'turn',
        input: { kind: 'message', messageId: 'u' },
        meta: { extensions: {}, replayScene: { slots: [], background: null } },
        messages: [
          {
            id: 'u',
            kind: 'user',
            source: { kind: 'manual' },
            content: {
              text: '问',
              attachments: [
                { asset: { id: 'document' }, name: '资料.txt', mediaType: 'text/plain' }
              ]
            },
            extensions: {}
          },
          {
            id: 'a',
            kind: 'assistant',
            parts: [
              { id: 'r', kind: 'reasoning', text: '思考内容' },
              { id: 'c', kind: 'tool-call', callId: 'call', toolName: 'lookup', arguments: {} }
            ],
            protocol: {},
            extensions: {}
          },
          {
            id: 'result',
            kind: 'tool-result',
            assistantMessageId: 'a',
            callId: 'call',
            outcome: { status: 'succeeded', value: 'ok' }
          },
          {
            id: 'reply',
            kind: 'assistant',
            protocol: { version: 1 },
            extensions: {},
            parts: [{ id: 'p', kind: 'dialogue', lines: [line('first'), line('second')] }]
          },
          {
            id: 'trailing',
            kind: 'assistant',
            protocol: {},
            extensions: {},
            parts: [{ id: 'tail', kind: 'reasoning', text: '轮末思考' }]
          }
        ]
      }
    ]
  }
  return { assets, chat }
}

test('旧 Python ZIP 转换为独立的对话与轮次；同名台词不合并', async () => {
  const { assets } = fixture()
  const data = {
    format: 'd_sakiko.chat_backup',
    version: 1,
    chats: [
      {
        chat: {
          name: '旧档',
          type: 1,
          message_list: [
            { character_name: 'User', text: '问题' },
            { character_name: '祥子', text: '一样' },
            { character_name: '祥子', text: '一样' }
          ]
        }
      }
    ]
  }
  const zip = zipSync({ 'manifest.json': strToU8(JSON.stringify(data)) })
  const { chats } = await decodeArchive(assets, zip, 'test', [])
  expect(chats[0].turns).toHaveLength(1)
  expect(new Set(chats[0].turns[0].messages.map((item) => item.id)).size).toBe(3)
})

const python = resolve('../.venv/bin/python')
const helper = resolve('tests/integration/python-archive-bridge.py')
test.skipIf(!existsSync(python))(
  'Electron → Python 保存、进程重启、编辑、分叉 → Electron 保留对应历史与资源',
  async () => {
    const { assets, chat } = fixture()
    const root = await mkdtemp(join(tmpdir(), 'dsakiko-roundtrip-'))
    const work = join(root, 'work')
    await mkdir(work)
    try {
      const zip = await encodeArchive(assets, [chat], true)
      await writeFile(join(root, 'electron.zip'), zip)
      for (const action of ['import', 'edit'])
        execFileSync(python, [helper, root, action], {
          cwd: work,
          env: { ...process.env, PYTHONPATH: resolve('../GPT_SoVITS') }
        })
      async function restored(name) {
        const { chats } = await decodeArchive(assets, await readFile(join(root, name)), 'test', [])
        return chats[0]
      }
      const original = await restored('unchanged.zip')
      expect(original.turns[0].messages.map((item) => item.id)).toEqual([
        'u',
        'a',
        'result',
        'reply',
        'trailing'
      ])
      expect(original.turns[0].messages[1].parts.map((part) => part.kind)).toEqual([
        'reasoning',
        'tool-call'
      ])
      const lines = original.turns[0].messages[3].parts[0].lines
      expect(lines.map((item) => item.id)).toEqual(['first', 'second'])
      expect(Buffer.from(assets.read(lines[0].audio.asset)).toString()).toBe('recording')
      expect(Buffer.from(assets.read(original.meta.workspaceFiles[0].asset)).toString()).toBe(
        'notes'
      )
      expect(original.turns[0].messages[0].content.attachments[0].asset).toEqual(
        original.meta.workspaceFiles[0].asset
      )
      const complete = await restored('whole-turn.zip')
      expect(complete.turns[0].messages.at(-1).id).toBe('trailing')
      const edited = await restored('edited.zip')
      expect(edited.turns.map((turn) => turn.messages[0].content.text)).toEqual([
        '改过的问题',
        '继续聊'
      ])
      const branch = await restored('fork.zip')
      expect(branch.turns).toHaveLength(1)
      expect(branch.turns[0].messages.at(-1).parts[0].lines.map((item) => item.id)).toEqual([
        'first'
      ])
      const without = strFromU8(
        unzipSync(await encodeArchive(assets, [chat], false))['manifest.json']
      )
      expect(JSON.parse(without).resources.audio).toEqual({})
    } finally {
      await rm(root, { recursive: true, force: true })
    }
  },
  30000
)
