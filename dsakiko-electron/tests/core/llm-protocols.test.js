import { expect, test, vi } from 'vitest'
import { Agent } from '../../src/backend/llm/interface.js'
import { characterContext } from '../../src/backend/llm/context.js'
import {
  normalizeConnection,
  requiresStableThinkingPrefix
} from '../../src/backend/llm/connections.js'
import { defaultSettings } from '../../src/backend/core/defaults.js'

const answer = JSON.stringify({
  lines: [{ speaker: '角色', text: '测试完成', translation: null, emotion: 'happiness' }]
})

test('禁用后工具报文不变，调用被拒绝且无副作用，并交付匹配的失败结果继续回答', async () => {
  const requests = []
  const input = requestFor('openai-compatible', 'chat-completions', 'test')
  const execute = vi.fn(async () => ({ status: 'succeeded', value: 'ok' }))
  input.tools[0].execute = execute
  input.toolDefinitions = input.tools.map(({ name, description, parametersSchema }) => ({
    name,
    description,
    parametersSchema
  }))
  const agent = new Agent({
    credential: () => 'fixture',
    assets: {},
    fetch: async (_, init) => {
      const body = JSON.parse(init.body)
      requests.push(body)
      if (body.messages.at(-1).role === 'tool') return json(completion({ content: answer }))
      return json(
        completion({
          content: '',
          tool_calls: [
            {
              id: 'one',
              type: 'function',
              function: { name: 'echo', arguments: '{"value":"hello"}' }
            }
          ]
        })
      )
    }
  })
  const enabled = recorder(input)
  await agent.run(input, enabled.observer)
  expect(execute).toHaveBeenCalledTimes(1)
  input.tools = []
  const disabled = recorder(input)
  await agent.run(input, disabled.observer)
  expect(execute).toHaveBeenCalledTimes(1)
  expect(requests[0].tools).toEqual(requests[2].tools)
  expect(disabled.turn.messages.find((message) => message.kind === 'tool-result')).toMatchObject({
    callId: 'one',
    outcome: { status: 'failed', problem: { code: 'tool_disabled', retryable: false } }
  })
  expect(requests[3].messages.at(-1)).toMatchObject({ role: 'tool', tool_call_id: 'one' })
})

test('工具结果令本轮超限时不发第二次请求，不重跑工具，保留调用与结果', async () => {
  const input = requestFor('openai-compatible', 'chat-completions', 'test')
  const execute = vi.fn(async () => ({ status: 'succeeded', value: '巨大结果'.repeat(5000) }))
  input.tools[0].execute = execute
  const transport = vi.fn(async () =>
    json(
      completion({
        content: '',
        tool_calls: [
          {
            id: 'one',
            type: 'function',
            function: { name: 'echo', arguments: '{"value":"hello"}' }
          }
        ]
      })
    )
  )
  const agent = new Agent({ credential: () => 'fixture', assets: {}, fetch: transport })
  const initial = await agent.inspectContext(input)
  input.connection.capabilities.contextTokens =
    initial.estimatedInputTokens + initial.reservedOutputTokens + 300
  const saved = recorder(input)
  await expect(agent.run(input, saved.observer)).rejects.toHaveProperty(
    'problem.code',
    'context_over_budget'
  )
  expect(transport).toHaveBeenCalledTimes(1)
  expect(execute).toHaveBeenCalledTimes(1)
  expect(saved.turn.messages.at(-1)).toMatchObject({
    kind: 'tool-result',
    outcome: { status: 'succeeded' }
  })
})

function requestFor(provider, protocol, modelId) {
  return {
    turnId: 'turn-1',
    mode: 'single-character',
    characters: [
      {
        id: 'actor',
        formId: 'default',
        name: '角色',
        displayName: '角色',
        description: '用于测试',
        modelVersion: 'v2',
        performances: { motions: [], expressions: [] }
      }
    ],
    userPersona: null,
    history: [],
    scenario: null,
    supplementalContext: [],
    input: {
      kind: 'message',
      message: { kind: 'user', source: { kind: 'manual' }, text: '请调用 echo', attachments: [] }
    },
    options: {
      ...structuredClone(defaultSettings.generation),
      language: 'Chinese',
      translationLanguage: null,
      maxOutputTokens: 2048,
      reasoning: { enabled: true, effort: null },
      limits: { maxSteps: 4, maxRequestRetries: 0, maxFormatRepairs: 1 }
    },
    connection: {
      connectionId: 'connection',
      provider,
      protocol,
      modelId,
      baseUrl: 'https://fixture.invalid/v1',
      credentialScope: 'scope',
      behavior: 'generic',
      attachmentTransport: 'inline',
      capabilities: {
        contextTokens: 100000,
        reasoning: { status: 'supported' },
        reasoningEfforts: ['low', 'high']
      }
    },
    tools: [
      {
        name: 'echo',
        mode: 'sync',
        description: '返回给定值',
        parametersSchema: {
          type: 'object',
          properties: { value: { type: 'string' } },
          required: ['value'],
          additionalProperties: false
        },
        execute: async (input) => ({ status: 'succeeded', value: input })
      }
    ]
  }
}

function recorder(request) {
  const turn = {
    turnId: request.turnId,
    characterContext: characterContext(request),
    messages: [structuredClone(request.input.message)]
  }
  return {
    turn,
    observer: {
      onResponse: async (update) =>
        turn.messages.push({
          kind: 'assistant',
          parts: update.appendParts,
          protocol: update.protocol
        }),
      onToolResult: async (result) => turn.messages.push({ ...result, toolName: 'echo' })
    }
  }
}

function json(value) {
  return new Response(JSON.stringify(value), {
    status: 200,
    headers: { 'content-type': 'application/json' }
  })
}

function completion(message) {
  return {
    id: 'completion',
    object: 'chat.completion',
    created: 1,
    model: 'test',
    choices: [
      {
        index: 0,
        message: { role: 'assistant', ...message },
        finish_reason: message.tool_calls ? 'tool_calls' : 'stop'
      }
    ],
    usage: { prompt_tokens: 10, completion_tokens: 5, total_tokens: 15 }
  }
}

const cases = [
  {
    provider: 'openrouter',
    protocol: 'chat-completions',
    model: 'vendor/model',
    response: (tool) =>
      completion(
        tool
          ? {
              content: '我先查一下。',
              reasoning_details: [
                {
                  type: 'reasoning.encrypted',
                  data: 'router-encrypted',
                  id: 'reasoning-1',
                  format: 'test',
                  index: 0
                }
              ],
              tool_calls: [
                {
                  id: 'call-1',
                  type: 'function',
                  function: { name: 'echo', arguments: '{"value":"ok"}' }
                }
              ]
            }
          : { content: answer }
      ),
    verify: (body) => {
      expect(body.messages.find((item) => item.tool_calls)?.reasoning_details).toEqual([
        {
          type: 'reasoning.encrypted',
          data: 'router-encrypted',
          id: 'reasoning-1',
          format: 'test',
          index: 0
        }
      ])
    }
  },
  {
    provider: 'openai',
    protocol: 'chat-completions',
    model: 'gpt-5.5',
    response: (tool) =>
      completion(
        tool
          ? {
              content: null,
              tool_calls: [
                {
                  id: 'call-1',
                  type: 'function',
                  function: { name: 'echo', arguments: '{"value":"ok"}' }
                }
              ]
            }
          : { content: answer }
      ),
    verify: (body) => {
      expect(body.max_completion_tokens).toBe(2048)
      expect(body).not.toHaveProperty('max_tokens')
      expect(body.messages.find((item) => item.tool_calls)).not.toHaveProperty('reasoning_content')
    }
  },
  {
    provider: 'deepseek',
    protocol: 'chat-completions',
    model: 'deepseek-flash',
    response: (tool) =>
      completion(
        tool
          ? {
              content: '我先查一下。',
              reasoning_content: '需要工具',
              tool_calls: [
                {
                  id: 'call-1',
                  type: 'function',
                  function: { name: 'echo', arguments: '{"value":"ok"}' }
                }
              ]
            }
          : { content: answer, reasoning_content: '完成' }
      ),
    verify: (body) => {
      expect(body.messages.find((item) => item.tool_calls)?.reasoning_content).toBe('需要工具')
      expect(body.messages.find((item) => item.tool_calls)?.content).toBe('我先查一下。')
      expect(body.thinking).toEqual({ type: 'enabled' })
    }
  },
  {
    provider: 'anthropic-compatible',
    protocol: 'anthropic-messages',
    model: 'claude-sonnet-5-5',
    response: (tool) => ({
      id: 'message',
      type: 'message',
      role: 'assistant',
      model: 'claude-sonnet-5-5',
      content: tool
        ? [
            { type: 'thinking', thinking: '', signature: 'signature-1' },
            { type: 'redacted_thinking', data: 'redacted-1' },
            { type: 'text', text: '我先查一下。' },
            { type: 'tool_use', id: 'call-1', name: 'echo', input: { value: 'ok' } }
          ]
        : [{ type: 'text', text: answer }],
      stop_reason: tool ? 'tool_use' : 'end_turn',
      stop_sequence: null,
      usage: { input_tokens: 10, output_tokens: 5 }
    }),
    verify: (body) => {
      const content = body.messages.find((item) => item.role === 'assistant').content
      expect(content.map((part) => part.type)).toEqual([
        'thinking',
        'redacted_thinking',
        'text',
        'tool_use'
      ])
      expect(content[0]).toMatchObject({ thinking: '', signature: 'signature-1' })
      expect(content[1].data).toBe('redacted-1')
    }
  },
  {
    provider: 'responses-compatible',
    protocol: 'responses',
    model: 'gpt-5.5',
    response: (tool) => ({
      id: 'resp-1',
      object: 'response',
      created_at: 1,
      model: 'gpt-5.5',
      status: 'completed',
      output: tool
        ? [
            { type: 'reasoning', id: 'rs-1', summary: [], encrypted_content: 'encrypted-1' },
            {
              type: 'function_call',
              id: 'fc-1',
              call_id: 'call-1',
              name: 'echo',
              arguments: '{"value":"ok"}',
              status: 'completed'
            }
          ]
        : [
            {
              type: 'message',
              id: 'msg-1',
              role: 'assistant',
              phase: 'final_answer',
              status: 'completed',
              content: [{ type: 'output_text', text: answer, annotations: [] }]
            }
          ],
      usage: {
        input_tokens: 10,
        output_tokens: 5,
        total_tokens: 15,
        input_tokens_details: { cached_tokens: 0 },
        output_tokens_details: { reasoning_tokens: 3 }
      }
    }),
    verify: (body) => {
      expect(body.store).toBe(false)
      expect(body).not.toHaveProperty('previous_response_id')
      expect(body).not.toHaveProperty('conversation')
      expect(body.input.find((item) => item.type === 'reasoning')).toMatchObject({
        id: 'rs-1',
        encrypted_content: 'encrypted-1'
      })
    }
  },
  {
    provider: 'gemini',
    protocol: 'google-generative-ai',
    model: 'gemini-3-pro-preview',
    response: (tool) => ({
      candidates: [
        {
          content: {
            role: 'model',
            parts: tool
              ? [
                  { text: '我先查一下。', thoughtSignature: 'text-signature' },
                  {
                    functionCall: { id: 'call-1', name: 'echo', args: { value: 'ok' } },
                    thoughtSignature: 'tool-signature'
                  }
                ]
              : [{ text: answer }, { text: '', thoughtSignature: 'final-signature' }]
          },
          finishReason: 'STOP'
        }
      ],
      usageMetadata: { promptTokenCount: 10, candidatesTokenCount: 5, totalTokenCount: 15 }
    }),
    verify: (body) => {
      const parts = body.contents.find((item) => item.role === 'model').parts
      expect(parts.find((part) => part.functionCall)?.thoughtSignature).toBe('tool-signature')
      expect(parts.find((part) => part.text)?.thoughtSignature).toBe('text-signature')
    }
  }
]

for (const scenario of cases)
  test(`${scenario.protocol}：真实 SDK 工具循环、空推理元数据及跨轮 JSON 存档恢复`, async () => {
    const requests = []
    const agent = new Agent({
      credential: () => 'test-secret',
      assets: {},
      fetch: async (url, init) => {
        requests.push({ url: String(url), body: JSON.parse(init.body) })
        return json(scenario.response(requests.length === 1))
      }
    })
    const request = requestFor(scenario.provider, scenario.protocol, scenario.model)
    const { turn, observer } = recorder(request)
    expect((await agent.run(request, observer)).status).toBe('completed')
    expect(requests).toHaveLength(2)
    scenario.verify(requests[1].body)
    expect(JSON.stringify(turn)).not.toContain('test-secret')
    expect(JSON.stringify(turn)).not.toContain('[runtime_controls]')
    const restored = JSON.parse(JSON.stringify(turn))
    const next = {
      ...request,
      history: [restored],
      input: { kind: 'message', message: { ...request.input.message, text: '下一轮' } }
    }
    await agent.run(next, recorder(next).observer)
    scenario.verify(requests[2].body)
    const nextMessages = await agent.messages(next)
    expect(
      nextMessages.filter(
        (item) => typeof item.content === 'string' && item.content.startsWith('[runtime_controls]')
      )
    ).toHaveLength(1)
    const original = restored.messages.find((item) => item.kind === 'assistant')
    if (scenario.provider === 'anthropic-compatible') {
      next.connection = { ...next.connection, behavior: 'anthropic' }
      const filtered = await agent.messages(next)
      expect(
        filtered
          .filter((item) => item.role === 'assistant')
          .flatMap((item) => item.content)
          .some((part) => part.type === 'reasoning')
      ).toBe(false)
      expect(original.protocol.content[0].providerOptions.anthropic.signature).toBe('signature-1')
    }
    if (scenario.provider === 'gemini') {
      expect(
        restored.messages.at(-1).protocol.content.at(-1).providerOptions.google.thoughtSignature
      ).toBe('final-signature')
    }
    // 切换服务后不能泄露此前连接的签名、密文或原始推理。
    next.connection = { ...next.connection, connectionId: 'another-service', behavior: 'generic' }
    const switched = JSON.stringify(await agent.messages(next))
    expect(switched).not.toContain('signature-1')
    expect(switched).not.toContain('encrypted-1')
    expect(switched).not.toContain('tool-signature')
    expect(switched).not.toContain('router-encrypted')
    // 编辑最终台词后，原始 JSON 响应不能覆盖用户修改。
    next.connection = request.connection
    restored.messages.at(-1).parts.find((part) => part.kind === 'dialogue').lines[0].text =
      '修改后的台词'
    expect(JSON.stringify(await agent.messages(next))).toContain('修改后的台词')
    restored.messages[0].text = '修改旧输入'
    expect(JSON.stringify(await agent.messages(next))).not.toContain('encrypted-1')
  })

test('预设、协议和行为策略独立；仅官方或显式声明的代理采用前缀绑定规则', () => {
  const base = {
    provider: 'anthropic-compatible',
    protocol: 'anthropic-messages',
    modelId: 'claude-sonnet-5-5',
    baseUrl: 'https://proxy.invalid/v1'
  }
  expect(requiresStableThinkingPrefix(base)).toBe(false)
  expect(requiresStableThinkingPrefix({ ...base, behavior: 'anthropic' })).toBe(true)
  expect(requiresStableThinkingPrefix({ ...base, baseUrl: 'https://api.anthropic.com/v1' })).toBe(
    true
  )
  expect(
    requiresStableThinkingPrefix({ ...base, behavior: 'anthropic', modelId: 'claude-sonnet-4-5' })
  ).toBe(false)
  expect(normalizeConnection({ provider: 'moonshot' })).toMatchObject({
    protocol: 'chat-completions',
    behavior: 'auto'
  })
  expect(
    normalizeConnection({
      provider: 'moonshot',
      protocol: 'anthropic-messages',
      baseUrl: 'https://proxy.invalid/v1'
    }).protocol
  ).toBe('anthropic-messages')
  expect(() =>
    normalizeConnection({
      provider: 'openai-compatible',
      baseUrl: 'https://user:secret@example.com'
    })
  ).toThrow()
})

test('格式修复保留上一响应，不重复执行工具；摘要不包含协议密文', async () => {
  const requests = []
  const agent = new Agent({
    credential: () => 'test',
    assets: {},
    generate: async (options) => {
      requests.push(structuredClone({ messages: options.messages, prompt: options.prompt }))
      const text = requests.length === 1 ? '不是 JSON' : answer
      return { text, content: [{ type: 'text', text }] }
    }
  })
  const request = requestFor('openai-compatible', 'chat-completions', 'test')
  const { turn, observer } = recorder(request)
  await agent.run(request, observer)
  expect(requests[1].messages.at(-2).content[0].text).toBe('不是 JSON')
  await agent.compress({ ...request, history: [turn] })
  expect(requests.at(-1).prompt).not.toContain('projectionFingerprint')
})
