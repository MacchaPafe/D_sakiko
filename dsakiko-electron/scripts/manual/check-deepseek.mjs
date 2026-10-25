// 仅在显式传入 --run-live 时调用收费 API；不注册到 package.json 或 Vitest。
import assert from 'node:assert/strict'
import { mkdtemp, readFile, writeFile, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { parseEnv } from 'node:util'
import { randomUUID } from 'node:crypto'
import { Agent } from '../../src/backend/llm/interface.js'
import { Settings } from '../../src/backend/settings/interface.js'
import { characterContext } from '../../src/backend/llm/context.js'
import { createDefaultSettings } from '../../src/backend/settings/definition.js'

assert(
  process.argv.includes('--run-live'),
  '真实测试需要显式传入 --run-live；最多发送三次 API 请求。'
)
const root = fileURLToPath(new URL('../../../', import.meta.url))
const secret = parseEnv(await readFile(path.join(root, '.env'), 'utf8')).DEEPSEEK_API_KEY
assert(secret, '.env 缺少 DEEPSEEK_API_KEY。')
const directory = await mkdtemp(path.join(tmpdir(), 'dsakiko-deepseek-check-'))

try {
  const settings = new Settings({ directory, assets: { registerOwnerReader() {} } })
  await settings.saveConnection(
    {
      id: 'live-check',
      revision: 0,
      name: '单次测试',
      provider: 'deepseek',
      protocol: 'chat-completions',
      baseUrl: 'https://api.deepseek.com/v1',
      attachmentTransport: 'inline',
      models: [
        {
          id: 'deepseek-flash',
          declared: {
            reasoning: { status: 'supported' },
            reasoningEfforts: ['low'],
            contextTokens: 100000
          },
          overrides: {}
        }
      ]
    },
    { kind: 'replace', secret }
  )
  const connection = await settings.resolveConnection({
    connectionId: 'live-check',
    modelId: 'deepseek-flash'
  })
  const requests = []
  const agent = new Agent({
    credential: (scope) => settings.credential(scope),
    assets: {},
    fetch: async (url, init) => {
      assert.equal(new URL(url).hostname, 'api.deepseek.com')
      assert(requests.length < 3, '已达到本次测试的三次请求上限。')
      requests.push(JSON.parse(init.body))
      return fetch(url, init)
    }
  })
  const marker = 'CHECK-' + randomUUID().slice(0, 8)
  let toolRuns = 0
  const request = {
    turnId: 'first',
    mode: 'single-character',
    characters: [
      {
        id: 'test',
        formId: 'default',
        name: '测试员',
        displayName: '测试员',
        description: '简短回答，准确复述测试结果。',
        modelVersion: 'v2',
        performances: { motions: [], expressions: [] }
      }
    ],
    userPersona: null,
    scenario: null,
    history: [],
    supplementalContext: [],
    connection,
    input: {
      kind: 'message',
      message: {
        kind: 'user',
        source: { kind: 'manual' },
        text: '请先且仅调用一次 read_test_value 工具，随后用一句中文完整复述工具返回的测试字符串。',
        attachments: []
      }
    },
    options: {
      ...createDefaultSettings().generation,
      language: 'Chinese',
      translationLanguage: null,
      timeoutMs: 90000,
      maxOutputTokens: 2048,
      reasoning: { enabled: true, effort: 'low' },
      limits: { maxSteps: 3, maxRequestRetries: 0, maxFormatRepairs: 0 }
    },
    tools: [
      {
        name: 'read_test_value',
        description: '读取当前测试字符串。',
        mode: 'sync',
        parametersSchema: { type: 'object', properties: {}, additionalProperties: false },
        execute: async () => {
          toolRuns += 1
          return { status: 'succeeded', value: marker }
        }
      }
    ]
  }
  const turns = []
  const usages = []
  for (let index = 0; index < 2; index += 1) {
    const turn = {
      turnId: request.turnId,
      characterContext: characterContext(request),
      messages: [structuredClone(request.input.message)]
    }
    const result = await agent.run(request, {
      onResponse: async (update) =>
        turn.messages.push({
          kind: 'assistant',
          parts: update.appendParts,
          protocol: update.protocol
        }),
      onToolResult: async (value) => turn.messages.push({ ...value, toolName: 'read_test_value' })
    })
    assert.equal(result.status, 'completed')
    const lines = turn.messages
      .filter((item) => item.kind === 'assistant')
      .flatMap((item) => item.parts)
      .filter((part) => part.kind === 'dialogue')
      .flatMap((part) => part.lines)
    assert(
      lines.some((line) => line.text.includes(marker)),
      '最终台词未完整包含工具结果。'
    )
    turns.push(turn)
    usages.push(result.usage)
    // 通过磁盘 JSON 恢复后继续下一轮，验证运行对象没有掩盖存储格式问题。
    const archive = path.join(directory, 'history.json')
    await writeFile(archive, JSON.stringify(turns))
    request.history = JSON.parse(await readFile(archive, 'utf8'))
    request.turnId = 'second'
    request.input = {
      kind: 'message',
      message: {
        ...request.input.message,
        text: '不要再次调用工具。请直接完整复述上一轮得到的测试字符串。'
      }
    }
  }
  assert.equal(toolRuns, 1)
  assert.equal(requests.length, 3)
  for (const body of requests) {
    assert.equal(
      body.messages.filter(
        (message) =>
          message.role === 'user' &&
          typeof message.content === 'string' &&
          message.content.startsWith('[runtime_controls]')
      ).length,
      1
    )
  }
  const firstAssistant = requests[1].messages.find(
    (message) => message.role === 'assistant' && message.tool_calls?.length
  )
  assert(firstAssistant?.reasoning_content?.length > 0, '工具循环未回传 DeepSeek 推理。')
  assert(
    requests[2].messages.some(
      (message) => message.role === 'assistant' && message.reasoning_content?.length > 0
    ),
    '存档恢复后未回传历史推理。'
  )
  assert(!JSON.stringify(turns).includes(secret), '存档中不应出现凭据。')
  assert(!JSON.stringify(turns).includes('[runtime_controls]'), '存档中不应出现临时控制消息。')
  console.log(
    JSON.stringify(
      {
        passed: true,
        model: connection.modelId,
        requests: requests.length,
        turns: turns.length,
        toolRuns,
        restoredReasoning: true,
        transientControls: true,
        usages
      },
      null,
      2
    )
  )
} catch (error) {
  // 不打印 SDK 原始异常对象，避免其中的请求头或完整请求泄露凭据。
  console.error(
    JSON.stringify({
      passed: false,
      code: error.problem?.code ?? error.code ?? 'check_failed',
      message: error.problem?.message ?? '单次测试未通过断言，请检查本次运行。'
    })
  )
  process.exitCode = 1
} finally {
  await rm(directory, { recursive: true, force: true })
}
