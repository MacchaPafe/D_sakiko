import { expect, test } from 'vitest'
import { Agent } from '../../src/backend/llm/interface.js'
import { characterContext } from '../../src/backend/llm/context.js'
import { Conversations } from '../../src/backend/conversation/interface.js'
import { defaultSettings } from '../../src/backend/core/defaults.js'
import { trimHistory } from '../../src/backend/llm/context-budget.js'

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

test('已知大窗口覆盖兜底预算、90% 阈值跟随；未知窗口保留 300000/270000', async () => {
  const input = request()
  const unknown = await agent.inspectContext(input)
  expect(unknown).toMatchObject({
    modelLimit: null,
    effectiveBudget: 300000,
    thresholdTokens: 270000
  })
  input.connection.capabilities = { contextTokens: 1000000, outputTokens: 2000 }
  expect(await agent.inspectContext(input)).toMatchObject({
    effectiveBudget: 1000000,
    thresholdTokens: 900000,
    reservedOutputTokens: 2000
  })
  input.connection.capabilities.inputTokens = 20
  expect(await agent.inspectContext(input)).toMatchObject({
    fit: 'over-budget',
    thresholdTokens: 20
  })
  input.connection.capabilities = { contextTokens: 128000 }
  expect(await agent.inspectContext(input)).toMatchObject({
    effectiveBudget: 128000,
    thresholdTokens: 115200
  })
})

test('兜底整轮移除工具链，保留摘要、连续后缀、当时人物快照与原存档', async () => {
  const input = request()
  const initial = await agent.inspectContext(input)
  input.connection.capabilities.contextTokens =
    initial.estimatedInputTokens + input.options.maxOutputTokens + 350
  input.history = [
    {
      ...turn(input),
      turnId: 'summary',
      messages: [{ kind: 'abstract', text: '必须保留的累计记忆' }]
    },
    {
      ...turn(input),
      turnId: 'old',
      messages: [
        { kind: 'user', text: '旧输入'.repeat(3000), attachments: [] },
        {
          kind: 'assistant',
          parts: [{ kind: 'tool-call', callId: 'call', toolName: 'echo', arguments: {} }]
        },
        {
          kind: 'tool-result',
          callId: 'call',
          toolName: 'echo',
          outcome: { status: 'succeeded', value: '旧结果' }
        }
      ]
    },
    { ...turn(input, '最近的输入'), turnId: 'recent' }
  ]
  const archived = input.history
  const before = structuredClone(archived)
  const result = await trimHistory(input, (value) => agent.inspectContext(value))
  expect(result.removedTurnIds).toEqual(['old'])
  expect(input.history.map((item) => item.turnId)).toEqual(['summary', 'recent'])
  expect(archived).toEqual(before)
  const messages = await agent.messages(input)
  expect(JSON.stringify(messages)).toContain('必须保留的累计记忆')
  expect(JSON.stringify(messages)).toContain('黑祥的设定')
  expect(JSON.stringify(messages)).not.toContain('旧结果')
  input.connection.capabilities.contextTokens = 10
  const impossible = await trimHistory(input, (value) => agent.inspectContext(value))
  expect(impossible.inspection.fit).toBe('over-budget')
  expect(input.history[0].messages[0].kind).toBe('abstract')
})
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

test('固定 system；仅注入当前人格，同人格不重复资料，切换人格追加覆盖；控制消息仅本轮末尾出现', async () => {
  const black = request()
  const first = await agent.messages(black)
  expect(contextMessages(first)).toHaveLength(1)
  expect(first[0].content).toContain('### 祥子\n{{ literal }} 黑祥的设定')
  expect(first[0].content).not.toContain('白祥的设定')
  expect(first[0].content).not.toContain('### 祥子（黑祥）')
  expect(first.at(-1).content).toContain('角色名：祥子。被问及自己的名字时，以「祥子」自然回答。')
  expect(first.at(-1).content).not.toContain('黑祥')

  const sameBlack = request()
  sameBlack.history = [turn(black)]
  expect(contextMessages(await agent.messages(sameBlack))).toHaveLength(1)

  const white = request('v3', 'white')
  white.history = [turn(black)]
  const second = await agent.messages(white)
  expect(contextMessages(second)).toHaveLength(2)
  expect(contextMessages(second)[1].content).toContain('### 祥子\n白祥的设定')
  expect(contextMessages(second)[1].content).not.toContain('黑祥的设定')
  expect(second.at(-1).content).toContain('nod')
  expect(second.at(-1).content).not.toContain('白祥')

  black.history = [...white.history, turn(white, '回去吧')]
  const third = await agent.messages(black)
  expect(contextMessages(third)).toHaveLength(3)
  expect(third.at(-1).content).not.toContain('nod')
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

test('导入历史缺少人物快照时，续聊和切换人格只在对应输入之前追加介绍', async () => {
  const imported = {
    turnId: 'python',
    messages: [
      { kind: 'user', text: '导入的旧问题', attachments: [] },
      {
        kind: 'assistant',
        parts: [
          {
            kind: 'dialogue',
            lines: [{ speakerId: 'sakiko', speakerLabel: '祥子', text: '导入的旧台词' }]
          }
        ]
      }
    ]
  }
  const black = request()
  black.history = [imported]
  const first = await agent.messages(black)
  expect(first[0].content).toEqual([{ type: 'text', text: '导入的旧问题' }])
  expect(first[1].content[0].text).toContain('导入的旧台词')
  expect(first[2].content).toContain('黑祥的设定')
  const white = request('v2', 'white')
  white.history = [imported, turn(black)]
  const switched = await agent.messages(white)
  expect(switched.slice(0, 4)).toEqual(first.slice(0, 4))
  expect(contextMessages(switched)).toHaveLength(2)
  expect(switched[4].content).toContain('白祥的设定')
  expect(switched[5].content).toEqual([{ type: 'text', text: white.input.message.text }])
})

test('摘要实际请求按当轮自称展示人物和台词，保留导入发言、旧摘要与工具结果', async () => {
  let prompt
  const summarizer = new Agent({
    assets: {},
    credential: () => 'test',
    generate: async (options) => {
      prompt = options.prompt
      return { text: '测试摘要' }
    }
  })
  const black = request()
  black.characters[0].forms[0].description = '正在准备演出'
  const blackTurn = turn(black)
  const white = request('v2', 'white')
  white.characters[0].selfNameMode = 'form'
  white.characters[0].forms[1].description = '正在社团排练'
  const whiteTurn = turn(white)
  for (const [entry, label, formId] of [
    [blackTurn, '祥子', 'black'],
    [whiteTurn, '白祥', 'white']
  ]) {
    entry.messages.push({
      kind: 'assistant',
      parts: [
        { kind: 'reasoning', text: '私有推理' },
        {
          kind: 'dialogue',
          lines: [{ speakerId: 'sakiko', speakerLabel: label, formId, text: '开始吧' }]
        }
      ]
    })
  }
  const imported = Conversations.prototype.history.call(Conversations.prototype, {
    meta: { extensions: { characterNames: { sakiko: '祥子' } } },
    turns: [
      {
        id: 'imported',
        meta: {},
        messages: [
          { kind: 'abstract', text: '旧摘要' },
          {
            kind: 'assistant',
            parts: [
              {
                kind: 'dialogue',
                lines: [{ speakerId: 'sakiko', formId: null, text: '导入的发言' }]
              }
            ]
          }
        ]
      }
    ]
  })
  whiteTurn.messages.push({
    kind: 'tool-result',
    callId: 'c',
    toolName: 'echo',
    outcome: { value: '工具结果' }
  })
  await summarizer.compress({
    history: [...imported, blackTurn, whiteTurn],
    options: { ...black.options, reasoning: { enabled: false, effort: null } },
    connection: {
      provider: 'openai-compatible',
      protocol: 'chat-completions',
      modelId: 'test',
      baseUrl: 'https://example.invalid/v1'
    }
  })
  expect(prompt).not.toMatch(/黑祥|sakiko|"black"|"white"|speakerId|formId|selfNameMode|私有推理/)
  const turns = JSON.parse(prompt)
  expect(turns[0].messages[0].text).toBe('旧摘要')
  expect(turns[0].messages[1].parts[0].lines[0]).toMatchObject({
    speaker: '祥子',
    text: '导入的发言'
  })
  expect(turns[1].characterContext).toContain('### 祥子\n正在准备演出')
  expect(turns[2].characterContext).toContain('### 祥子（白祥）\n正在社团排练')
  expect(turns[1].messages[1].parts[0].lines[0].speaker).toBe('祥子')
  expect(turns[2].messages[1].parts[0].lines[0].speaker).toBe('白祥')
  expect(turns[2].messages[2].outcome.value).toBe('工具结果')
})

test('人物编辑追加完整更新，压缩和存档恢复后仍保留最新激活人格', async () => {
  const original = request('v2', 'white')
  const changed = request('v2', 'white')
  changed.characters[0].forms[1].description = '更新后的白祥设定'
  changed.characters[0].description = '更新后的白祥设定'
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

test('同场按人格自我介绍，重名时限定署名，输出能定位到各自的人格', async () => {
  const input = request('v2', 'white')
  input.characters[0].selfNameMode = 'form'
  const actor = input.characters[0]
  const single = example(await agent.messages(input))
  expect(single.lines[0].speaker).toBe('白祥')
  input.characters.push({ ...actor, id: 'another', name: '另一位角色' })
  const messages = await agent.messages(input)
  expect(contextMessages(messages)[0].content).toContain('### 祥子（白祥）')
  expect(messages.at(-1).content).toContain(
    '当前自称：白祥。被问及自己的名字时，以「白祥」自然回答。'
  )
  const output = example(messages)
  expect(output.lines.map((line) => line.speaker)).toEqual(['祥子（白祥）', '另一位角色（白祥）'])
  expect(agent.lines(JSON.stringify(output), input).map((line) => line.speakerId)).toEqual([
    'sakiko',
    'another'
  ])
})

test('历史保留旧 v3 选择且不向 LLM 发送内部角色和人格编号', async () => {
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
