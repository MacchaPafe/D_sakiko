import { expect, test } from 'vitest'
import { historyRows } from '../../src/renderer/src/features/home/history.js'
import { lastDialogue, lastDialogueEntry } from '../../src/shared/chat-history.js'
import {
  fitHistorySize,
  readHistorySize
} from '../../src/renderer/src/features/home/history-size.js'
function snapshot(messages) {
  return { chat: { turns: [{ id: 't', messages }] } }
}
test('旧版内部抽签仅转换展示，结构化事件同样显示主题和结果，普通用户文本保留', () => {
  const internal =
    '【系统内部事件触发：抽签结束】抽签主题：去哪？。用户已点击抽签，结果是：公园。请你自然地继续对话。'
  const messages = [
    { id: 'legacy', kind: 'user', source: { kind: 'manual' }, content: { text: internal } },
    {
      id: 'new',
      kind: 'user',
      source: { kind: 'async' },
      results: [
        { toolName: 'start_lottery', request: { title: '去哪？' }, result: { option: '公园' } }
      ]
    },
    { id: 'user', kind: 'user', source: { kind: 'manual' }, content: { text: '我们来抽签吧' } }
  ]
  const rows = historyRows(snapshot(messages))
  expect(rows[0]).toMatchObject({ kind: 'system', text: '抽签结果 · 去哪？：公园' })
  expect(rows[1].text).toBe(rows[0].text)
  expect(rows[2]).toMatchObject({ kind: 'user', text: '我们来抽签吧' })
  expect(messages[0].content.text).toBe(internal)
})
test('揭晓前隐藏整轮结果、角色台词和摘要，揭晓后立即可见', () => {
  const turn = snapshot([
    {
      kind: 'user',
      source: { kind: 'async' },
      results: [{ toolName: 'start_lottery', request: { title: '饮料' }, result: { option: '茶' } }]
    },
    {
      kind: 'assistant',
      parts: [{ kind: 'dialogue', lines: [{ id: 'l', speakerId: 'actor', text: '我也喜欢茶。' }] }]
    }
  ])
  turn.chat.meta = { lotteries: [{ deliveredTurnId: 't', revealAt: 2000 }] }
  expect(historyRows(turn, 1999).map((row) => row.text)).toEqual(['正在抽签'])
  expect(lastDialogue(turn.chat, 1999)).toBeNull()
  expect(historyRows(turn, 2000).map((row) => row.text)).toEqual([
    '抽签结果 · 饮料：茶',
    '我也喜欢茶。'
  ])
  expect(lastDialogue(turn.chat, 2000).text).toBe('我也喜欢茶。')
})
test('工具成对折叠且不跨台词合并，末句摘要跳过工具和思考', () => {
  const call = (id) => ({
    id,
    kind: 'assistant',
    parts: [{ kind: 'tool-call', callId: id, toolName: 'test' }]
  })
  const result = (id) => ({
    id: id + 'result',
    kind: 'tool-result',
    assistantMessageId: id,
    callId: id,
    outcome: { status: 'succeeded', value: id }
  })
  const line = {
    id: 'line',
    speakerId: 'role',
    text: '最後のセリフ',
    translation: '最后的台词',
    audio: { asset: { id: 'recording' }, durationMs: 500 }
  }
  const s = snapshot([
    call('a'),
    result('a'),
    call('b'),
    result('b'),
    { id: 'say', kind: 'assistant', parts: [{ kind: 'dialogue', lines: [line] }] },
    call('c'),
    result('c')
  ])
  const rows = historyRows(s)
  expect(rows.map((r) => r.kind)).toEqual(['tools', 'assistant', 'tools'])
  expect(rows[0].tools.map((t) => t.outcome.value)).toEqual(['a', 'b'])
  expect(rows[1]).toMatchObject({
    text: line.text,
    translation: line.translation,
    audio: line.audio
  })
  expect(lastDialogue(s.chat)).toEqual(line)
})

test('缺少语音或只有空白译文的角色台词仍可显示正文', () => {
  const line = { id: 'line', speakerId: 'role', text: 'またね。', translation: '  ' }
  const rows = historyRows(
    snapshot([{ kind: 'assistant', parts: [{ kind: 'dialogue', lines: [line] }] }])
  )
  expect(rows).toMatchObject([{ text: line.text, translation: null, audio: null }])
})

test('中文原文配有日语译文时，中文预览仍取原文', () => {
  const line = { text: '下次见。', translation: 'またね。', spoken: { language: 'Chinese' } }
  const { chat } = snapshot([{ kind: 'assistant', parts: [{ kind: 'dialogue', lines: [line] }] }])
  chat.turns[0].meta = { generation: { language: 'Chinese', translationLanguage: 'Japanese' } }
  expect(lastDialogueEntry(chat)).toMatchObject({ line: { text: '下次见。' }, translation: null })
})
test('整轮回放对已结束的有台词轮次开放，小剧场使用第一句作为入口', () => {
  const dialogue = {
    kind: 'assistant',
    parts: [{ kind: 'dialogue', lines: [{ id: 'line', speakerId: 'role', text: '台词' }] }]
  }
  const input = {
    id: 'input',
    kind: 'user',
    source: { kind: 'manual' },
    content: { text: '你好' }
  }
  const chat = snapshot([input, dialogue])
  expect(historyRows(chat).map((row) => row.replayTurnId)).toEqual(['t', undefined])
  expect(historyRows({ ...chat, activeTurnId: 't' }).every((row) => !row.replayTurnId)).toBe(true)
  expect(historyRows(snapshot([input]))[0].replayTurnId).toBeUndefined()
  const theater = snapshot([dialogue])
  theater.chat.mode = 'scripted-dialogue'
  expect(historyRows(theater)[0].replayTurnId).toBe('t')
})
test('记录框偏好可恢复，小窗口约束不覆盖大窗口偏好', () => {
  const preference = readHistorySize({ getItem: () => '{"width":650,"height":700}' })
  expect(fitHistorySize(preference, { width: 400, height: 420 })).toEqual({
    width: 400,
    height: 420
  })
  expect(fitHistorySize(preference, { width: 900, height: 900 })).toEqual(preference)
  expect(readHistorySize({ getItem: () => '{broken' })).toEqual({ width: 360, height: 671 })
})
