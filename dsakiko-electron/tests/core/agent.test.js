import http from 'node:http'
import { afterEach, expect, test } from 'vitest'
import { Agent } from '../../src/backend/llm/interface.js'
import { defaultSettings } from '../../src/backend/core/defaults.js'
const cleanups = []
afterEach(async () => {
  for (const cleanup of cleanups.splice(0).reverse()) await cleanup()
})
test('实际 AI SDK 兼容协议、固定模板、工具串行执行与非流式台词校验', async () => {
  const requests = []
  let toolRuns = 0
  const server = http.createServer(async (request, response) => {
    const chunks = []
    for await (const chunk of request) chunks.push(chunk)
    requests.push(JSON.parse(Buffer.concat(chunks).toString()))
    const message =
      requests.length === 1
        ? {
            role: 'assistant',
            content: null,
            tool_calls: [
              {
                id: 'call-1',
                type: 'function',
                function: { name: 'echo', arguments: '{"value":"one"}' }
              }
            ]
          }
        : {
            role: 'assistant',
            content: JSON.stringify({
              lines: [
                {
                  speakerId: 'actor',
                  formId: 'default',
                  text: '你好',
                  translation: null,
                  emotion: null,
                  performance: { motion: 'auto', expression: 'auto' }
                }
              ]
            })
          }
    response.writeHead(200, { 'Content-Type': 'application/json' })
    response.end(
      JSON.stringify({
        id: 'chatcmpl-' + requests.length,
        object: 'chat.completion',
        created: 1,
        model: 'test',
        choices: [
          { index: 0, message, finish_reason: requests.length === 1 ? 'tool_calls' : 'stop' }
        ],
        usage: { prompt_tokens: 20, completion_tokens: 10, total_tokens: 30 }
      })
    )
  })
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve))
  cleanups.push(() => new Promise((resolve) => server.close(resolve)))
  const agent = new Agent({ credential: () => 'test', assets: {} })
  const input = {
    turnId: 'turn',
    mode: 'single-character',
    characters: [
      {
        id: 'actor',
        formId: 'default',
        displayName: '角色',
        description: '{{ not_a_template }}',
        performances: { motions: [], expressions: [] }
      }
    ],
    userPersona: null,
    history: [],
    input: {
      kind: 'message',
      message: { kind: 'user', source: { kind: 'manual' }, text: 'hi', attachments: [] }
    },
    scenario: null,
    // 此用例的模拟模型不支持推理选项，避免依赖用户可调整的产品默认值。
    options: {
      ...structuredClone(defaultSettings.generation),
      reasoning: { enabled: false, effort: null }
    },
    connection: {
      provider: 'openai-compatible',
      protocol: 'chat',
      baseUrl: 'http://127.0.0.1:' + server.address().port + '/v1',
      modelId: 'test',
      credentialScope: 'scope',
      attachmentTransport: 'inline',
      capabilities: { contextTokens: 10000 }
    },
    supplementalContext: [],
    tools: [
      {
        name: 'echo',
        description: 'echo',
        mode: 'sync',
        parametersSchema: {
          type: 'object',
          properties: { value: { type: 'string' } },
          required: ['value'],
          additionalProperties: false
        },
        execute: async (value) => {
          toolRuns += 1
          return { status: 'succeeded', value }
        }
      }
    ]
  }
  expect((await agent.inspectContext(input)).fit).toBe('fits')
  expect(requests).toHaveLength(0)
  const updates = []
  const results = []
  await agent.run(
    input,
    {
      onResponse: async (value) => updates.push(value),
      onToolResult: async (value) => results.push(value)
    },
    new AbortController().signal
  )
  expect(toolRuns).toBe(1)
  expect(results).toHaveLength(1)
  expect(requests).toHaveLength(2)
  expect(requests[0].messages[0].content).toContain('{{ not_a_template }}')
  expect(requests[1].messages.some((message) => message.role === 'tool')).toBe(true)
  expect(updates.at(-1).appendParts[0].lines[0].text).toBe('你好')
})
