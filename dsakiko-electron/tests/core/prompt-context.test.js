import { expect, test } from 'vitest'
import { Agent } from '../../src/backend/llm/interface.js'
import { characterContext } from '../../src/backend/llm/context.js'
import { Conversations } from '../../src/backend/conversation/interface.js'
import { defaultSettings } from '../../src/backend/core/defaults.js'

function request(version = 'v2', formId = 'black') {
  const forms = [
    { id: 'black', displayName: '黑祥', description: '{{ literal }} 黑祥的设定' },
    { id: 'white', displayName: '白祥', description: '白祥的设定' }
  ]
  return {
    turnId: 'turn',
    mode: 'single-character',
    userPersona: null,
    characters: [
      {
        id: 'sakiko',
        name: '祥子',
        formId,
        forms,
        ...Object.fromEntries(
          Object.entries(forms.find((form) => form.id === formId)).filter(([key]) => key !== 'id')
        ),
        modelVersion: version,
        performances: {
          motions: [{ id: 'nod', description: '点头' }],
          expressions: [{ id: 'smile', description: '微笑' }],
          presets: []
        }
      }
    ],
    history: [],
    input: { kind: 'message', message: { kind: 'user', text: '你好', attachments: [] } },
    options: {
      ...structuredClone(defaultSettings.generation),
      language: 'Japanese',
      translationLanguage: 'Chinese'
    },
    scenario: null,
    connection: { capabilities: {} },
    supplementalContext: [],
    tools: []
  }
}
const agent = new Agent({ credential: () => '', assets: {} })
function contextMessages(messages) {
  return messages.filter(
    (message) =>
      typeof message.content === 'string' && message.content.startsWith('[character_context]')
  )
}
function turn(input, text = '你好') {
  return {
    turnId: 'old',
    characterContext: characterContext(input),
    messages: [{ kind: 'user', text, attachments: [] }]
  }
}
function example(messages) {
  return JSON.parse(
    messages
      .at(-1)
      .content.split('\n')
      .find((line) => line.startsWith('{"lines":'))
  )
}

test('固定 system；形态往返不重复资料；控制消息仅本轮末尾出现', async () => {
  const black = request()
  const first = await agent.messages(black)
  expect(contextMessages(first)).toHaveLength(1)
  expect(first[0].content).toContain('黑祥的设定')
  expect(first[0].content).toContain('白祥的设定')
  expect(first[0].content).toContain('{{ literal }}')
  const white = request('v3', 'white')
  white.history = [turn(black)]
  const second = await agent.messages(white)
  expect(contextMessages(second)).toHaveLength(1)
  expect(second.at(-1).content).toContain('当前形态：白祥')
  expect(second.at(-1).content).toContain('nod')
  black.history = [...white.history, turn(white, '回去吧')]
  const third = await agent.messages(black)
  expect(contextMessages(third)).toHaveLength(1)
  expect(third.at(-1).content).not.toContain('nod')
  expect(third.at(-1).content).toContain('当前形态：黑祥')
  expect(agent.system(black)).toBe(agent.system(white))
  expect(agent.system(white)).not.toContain('祥子')
  expect(
    third.filter(
      (message) =>
        typeof message.content === 'string' && message.content.startsWith('[runtime_controls]')
    )
  ).toHaveLength(1)
  expect(third.slice(0, first.length - 1)).toEqual(first.slice(0, -1))
})

test('人物编辑追加完整更新，压缩和存档恢复后仍恢复最新全部形态', async () => {
  const original = request()
  const changed = request()
  changed.characters[0].forms[1].description = '更新后的白祥设定'
  changed.history = [turn(original)]
  const messages = await agent.messages(changed)
  expect(contextMessages(messages)).toHaveLength(2)
  expect(contextMessages(messages)[1].content).toContain('更新后的白祥设定')
  const chat = {
    turns: [
      {
        id: 'a',
        meta: { characterContext: characterContext(original) },
        messages: [{ id: 'u1', kind: 'user', text: '之前', source: {}, attachments: [] }]
      },
      {
        id: 'b',
        meta: { characterContext: characterContext(changed) },
        messages: [{ id: 'summary', kind: 'abstract', text: '累计记忆' }]
      }
    ]
  }
  const history = Conversations.prototype.history.call(
    Conversations.prototype,
    JSON.parse(JSON.stringify(chat))
  )
  changed.history = history
  const restored = await agent.messages(changed)
  expect(contextMessages(restored)).toHaveLength(1)
  expect(restored[0].content).toContain('更新后的白祥设定')
  expect(JSON.stringify(restored)).not.toContain('之前')
})

test('示例遵守实际翻译和 v2/v3 协议；角色名称映射到冻结的内部身份', async () => {
  for (const version of ['v2', 'v3']) {
    const input = request(version, 'white')
    const output = example(await agent.messages(input))
    expect(output.lines[0]).toMatchObject({
      speaker: '祥子',
      text: 'また会えて嬉しい。',
      translation: '很高兴又见到你。'
    })
    expect(Boolean(output.lines[0].performance)).toBe(version === 'v3')
    expect(agent.lines(JSON.stringify(output), input)[0]).toMatchObject({
      speakerId: 'sakiko',
      formId: 'white'
    })
    output.lines[0].speaker = '白祥'
    expect(() => agent.lines(JSON.stringify(output), input)).toThrow()
  }
  const input = request('v2')
  input.options.translationLanguage = null
  const output = example(await agent.messages(input))
  expect(output.lines[0].translation).toBeNull()
  output.lines[0].performance = { motion: 'old-v3-motion', expression: 'old-v3-expression' }
  expect(agent.lines(JSON.stringify(output), input)[0].performance).toEqual({
    motion: 'auto',
    expression: 'auto'
  })
})

test('历史保留旧 v3 选择且不向 LLM 发送内部角色和形态编号', async () => {
  const input = request('v2')
  input.history = [
    {
      ...turn(input),
      messages: [
        {
          kind: 'assistant',
          parts: [
            {
              kind: 'dialogue',
              lines: [
                {
                  id: 'line-id',
                  speakerId: 'sakiko',
                  formId: 'white',
                  text: '旧台词',
                  translation: null,
                  emotion: 'like',
                  performance: { motion: 'old-motion', expression: 'old-expression' }
                }
              ]
            }
          ]
        }
      ]
    }
  ]
  const messages = await agent.messages(input)
  const history = messages.find((message) => message.role === 'assistant').content[0].text
  expect(JSON.parse(history).lines[0]).toMatchObject({
    speaker: '祥子',
    performance: { motion: 'old-motion', expression: 'old-expression' }
  })
  expect(history).not.toContain('speakerId')
  expect(history).not.toContain('formId')
})

test('小剧场可以混用 v2/v3，逐角色决定输出字段，示例可通过真实解析器', async () => {
  const input = request('v2')
  input.mode = 'scripted-dialogue'
  input.input = { kind: 'scenario' }
  input.scenario = {
    situation: '放学后的练习室',
    characters: { sakiko: { speakingStyle: '简洁', interaction: '讨论排练' } }
  }
  input.characters.push({
    ...structuredClone(request('v3').characters[0]),
    id: 'anon',
    name: '爱音'
  })
  const messages = await agent.messages(input)
  const controls = messages.at(-1).content
  expect(controls).toContain('约 15 条')
  expect(controls).toContain('放学后的练习室')
  expect(controls).toContain('简洁')
  const output = example(messages)
  expect(output.lines[0].performance).toBeUndefined()
  expect(output.lines[1].performance).toEqual({ motion: 'auto', expression: 'auto' })
  expect(agent.lines(JSON.stringify(output), input).map((line) => line.speakerId)).toEqual([
    'sakiko',
    'anon'
  ])
  output.lines[1].performance.motion = 'invalid'
  expect(() => agent.lines(JSON.stringify(output), input)).toThrow()
  output.lines[1].performance.motion = 'nod'
  output.lines[1].translation = null
  expect(() => agent.lines(JSON.stringify(output), input)).toThrow()
})
