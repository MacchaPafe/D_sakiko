import assert from 'node:assert/strict'
import { test } from 'node:test'
import { generateText, Output, tool } from 'ai'
import { createModel } from '../providers.mjs'
import { runAgent, validatedSchema } from '../agent.mjs'
import { automaticAgent, complete } from '../operations.mjs'
import { createTimeTool, demoCharacters, replySchema } from '../schema.mjs'
import {
  anthropicReply,
  chatReply,
  deferred,
  finalText,
  finalValue,
  googleReply,
  scriptedFetch,
  toolCall
} from './fixtures.mjs'

function harness(
  responses,
  { provider = 'deepseek', visibility = 'visible', execute, observer } = {}
) {
  const transport = scriptedFetch(responses)
  const model = createModel(
    {
      provider,
      model: 'deepseek-v4-flash',
      apiKey: 'synthetic-test-key',
      ...(provider === 'openai-compatible' ? { baseURL: 'https://example.invalid/v1' } : {})
    },
    transport.fetcher
  )
  const events = []
  let executions = 0
  const definition = createTimeTool(visibility)
  definition.execute = async (input, signal) => {
    executions += 1
    events.push('execute')
    if (execute) return execute(input, signal)
    return { iso: '2026-10-07T00:00:00.000Z' }
  }
  const controller = new AbortController()
  const options = {
    model,
    characters: demoCharacters,
    tools: [definition],
    prompt: '问候我。',
    signal: controller.signal,
    observer: observer || {
      async onIntermediate(messages) {
        events.push(...messages.map((message) => message.text))
      },
      async onToolActivity(record) {
        events.push(`${record.callId}:${record.status}`)
      }
    }
  }
  return { ...transport, options, events, controller, definition, count: () => executions }
}

const exchange = () => [chatReply('', '先查时间。', [toolCall()]), chatReply(finalText, '再问候。')]

test('四种厂商适配器把统一补全转换成各自的请求协议', async (t) => {
  const cases = [
    { provider: 'deepseek', body: chatReply('连接成功。'), path: '/chat/completions' },
    { provider: 'openai-compatible', body: chatReply('连接成功。'), path: '/v1/chat/completions' },
    {
      provider: 'anthropic',
      body: anthropicReply([{ type: 'text', text: '连接成功。' }]),
      path: '/v1/messages'
    },
    { provider: 'google', body: googleReply([{ text: '连接成功。' }]), path: ':generateContent' }
  ]
  for (const item of cases) {
    await t.test(item.provider, async () => {
      const h = harness([item.body], { provider: item.provider })
      assert.equal(await complete(h.options.model, h.options.signal), '连接成功。')
      const request = h.requests[0]
      assert.ok(request.url.endsWith(item.path), request.url)
      if (item.provider === 'anthropic') {
        assert.equal(request.headers.get('x-api-key'), 'synthetic-test-key')
        assert.ok(request.body.messages)
      } else if (item.provider === 'google') {
        assert.equal(request.headers.get('x-goog-api-key'), 'synthetic-test-key')
        assert.ok(request.body.contents)
      } else {
        assert.equal(request.headers.get('authorization'), 'Bearer synthetic-test-key')
        assert.notEqual(request.body.stream, true)
      }
    })
  }
})

test('自定义厂商和模型必须显式配置，不猜测服务协议', () => {
  assert.throws(() => createModel({ provider: 'other', model: 'demo', apiKey: 'test' }), /不支持/)
  assert.throws(() => createModel({ provider: 'deepseek', apiKey: 'test' }), /模型/)
  assert.throws(
    () => createModel({ provider: 'openai-compatible', model: 'demo', apiKey: 'test' }),
    /baseURL/
  )
})

test('ToolLoopAgent 自动执行原生工具并通过 Output.object 校验最终台词', async () => {
  const h = harness(exchange())
  const result = await automaticAgent({ ...h.options, definition: h.definition })
  assert.equal(result.executions, 1)
  assert.equal(result.steps, 2)
  assert.equal(result.lines[0].kind, 'character')
  assert.equal(h.requests[1].body.messages.at(-1).role, 'tool')
  assert.ok(h.requests[1].body.response_format)
})

for (const phase of ['onStepEnd', 'onToolExecutionStart']) {
  test(`SDK 的 ${phase} 异常不会阻止工具执行，不能直接充当业务接收确认`, async () => {
    const h = harness([chatReply('', '', [toolCall()])])
    let notified = false
    const result = await generateText({
      model: h.options.model,
      prompt: '查询时间。',
      maxRetries: 0,
      telemetry: { isEnabled: false },
      tools: {
        get_current_time: tool({
          inputSchema: validatedSchema(h.definition.parametersSchema),
          execute: h.definition.execute
        })
      },
      [phase]: async () => {
        notified = true
        throw new Error('接收失败')
      }
    })
    assert.equal(notified, true)
    assert.equal(h.count(), 1)
    assert.equal(result.toolResults.length, 1)
  })
}

test('已创建的模型保持连接快照，修改配置不会让在途调用换凭据', async () => {
  const transport = scriptedFetch([chatReply('连接成功。')])
  const connection = {
    provider: 'deepseek',
    model: 'deepseek-v4-flash',
    apiKey: 'original-key',
    baseURL: 'https://first.example.invalid/v1'
  }
  const model = createModel(connection, transport.fetcher)
  connection.apiKey = 'replacement-key'
  connection.baseURL = 'https://second.example.invalid/v1'
  await complete(model, new AbortController().signal)
  assert.ok(transport.requests[0].url.startsWith('https://first.example.invalid/'))
  assert.equal(transport.requests[0].headers.get('authorization'), 'Bearer original-key')
})

test('完整 reasoning → running → execute → succeeded → reasoning；最终台词只返回一次', async () => {
  const h = harness(exchange())
  const result = await runAgent(h.options)
  assert.equal(result.status, 'completed')
  assert.equal(result.lines.length, 1)
  assert.deepEqual(h.events, [
    '先查时间。',
    'time-1:running',
    'execute',
    'time-1:succeeded',
    '再问候。'
  ])
  const history = h.requests[1].body.messages
  const assistant = history.find((message) => message.role === 'assistant')
  assert.equal(assistant.reasoning_content, '先查时间。')
  assert.equal(assistant.tool_calls[0].id, 'time-1')
  assert.equal(history.at(-1).tool_call_id, 'time-1')
  assert.equal(h.requests[0].body.tools[0].function.name, 'get_current_time')
})

test('等待 reasoning 和 running 被接受后才执行工具，不并行发送 Observer', async () => {
  const reasoningAccepted = deferred()
  const runningAccepted = deferred()
  const reasoningEntered = deferred()
  const runningEntered = deferred()
  let busy = false
  const h = harness(exchange(), {
    observer: {
      async onIntermediate() {
        assert.equal(busy, false)
        busy = true
        reasoningEntered.resolve()
        await reasoningAccepted.promise
        busy = false
      },
      async onToolActivity(record) {
        assert.equal(busy, false)
        if (record.status === 'running') {
          busy = true
          runningEntered.resolve()
          await runningAccepted.promise
          busy = false
        }
      }
    }
  })
  const pending = runAgent(h.options)
  await reasoningEntered.promise
  assert.equal(h.count(), 0)
  reasoningAccepted.resolve()
  await runningEntered.promise
  assert.equal(h.count(), 0)
  runningAccepted.resolve()
  assert.equal((await pending).status, 'completed')
})

test('隐藏工具执行和回传结果，但不提交聊天工具记录', async () => {
  const h = harness(exchange(), { visibility: 'hidden' })
  await runAgent(h.options)
  assert.equal(h.count(), 1)
  assert.deepEqual(h.events, ['先查时间。', 'execute', '再问候。'])
  assert.equal(h.requests[1].body.messages.at(-1).role, 'tool')
})

for (const phase of ['reasoning', 'running', 'succeeded']) {
  test(`Observer 在 ${phase} 拒绝时停止，不重新请求或执行工具`, async () => {
    const failure = new Error('接收者写入失败')
    const h = harness(exchange(), {
      observer: {
        async onIntermediate() {
          if (phase === 'reasoning') throw failure
        },
        async onToolActivity(record) {
          if (record.status === phase) throw failure
        }
      }
    })
    await assert.rejects(runAgent(h.options), (error) => error === failure)
    assert.equal(h.requests.length, 1)
    assert.equal(h.count(), phase === 'succeeded' ? 1 : 0)
  })
}

test('格式修复保留已执行工具和 reasoning，工具只执行一次', async () => {
  const h = harness([
    chatReply('', '先查时间。', [toolCall()]),
    chatReply('{"lines":[]}', '第一次生成。'),
    chatReply(finalText, '修复完成。')
  ])
  const result = await runAgent(h.options)
  assert.equal(result.status, 'completed')
  assert.equal(h.count(), 1)
  assert.equal(h.requests[2].body.tools, undefined)
  assert.equal(h.requests[2].body.tool_choice, undefined)
  assert.ok(h.requests[2].body.messages.some((message) => message.role === 'tool'))
  assert.equal(h.events.filter((event) => event === '第一次生成。').length, 1)
  assert.equal(h.events.at(-1), '修复完成。')
})

test('格式修复期间即使模型违规返回工具也不执行', async () => {
  const h = harness([chatReply('invalid'), chatReply('', '', [toolCall('new-call')])])
  await assert.rejects(runAgent(h.options), /禁止重新调用工具/)
  assert.equal(h.count(), 0)
})

for (const [name, call] of [
  ['不存在的工具', toolCall('bad', 'delete_files')],
  ['不合法参数', toolCall('bad', 'get_current_time', '{"unexpected":true}')],
  ['损坏的参数 JSON', toolCall('bad', 'get_current_time', '{')]
]) {
  test(`${name}在任何工具副作用前被拒绝`, async () => {
    const h = harness([chatReply('', '', [toolCall('good'), call])])
    await assert.rejects(runAgent(h.options))
    assert.equal(h.count(), 0)
  })
}

test('相同 callId 和参数只执行一次；不同 callId 表示新的调用', async () => {
  const h = harness([
    chatReply('', '', [toolCall()]),
    chatReply('', '', [toolCall()]),
    chatReply('', '', [toolCall('time-2')]),
    chatReply(finalText)
  ])
  await runAgent(h.options)
  assert.equal(h.count(), 2)
  assert.equal(h.events.filter((event) => event === 'time-1:running').length, 1)
})

test('相同 callId 不能换工具执行', async () => {
  const h = harness([
    chatReply('', '', [toolCall()]),
    chatReply('', '', [toolCall('time-1', 'other')])
  ])
  h.options.tools.push({ ...createTimeTool(), name: 'other' })
  await assert.rejects(runAgent(h.options), /callId/)
  assert.equal(h.count(), 1)
})

test('工具失败交付 failed，并通过原生错误结果让模型继续', async () => {
  const h = harness(exchange(), {
    execute: async () => {
      throw new Error('测试工具失败')
    }
  })
  assert.equal((await runAgent(h.options)).status, 'completed')
  assert.ok(h.events.includes('time-1:failed'))
  assert.equal(h.count(), 1)
  assert.match(h.requests[1].body.messages.at(-1).content, /工具执行失败/)
})

test('取消在途 HTTP 请求，不提交迟到结果或执行工具', async () => {
  const entered = deferred()
  const h = harness([
    async (request) => {
      entered.resolve()
      await new Promise((resolve, reject) => {
        request.signal.addEventListener('abort', () => reject(request.signal.reason), {
          once: true
        })
      })
    }
  ])
  const pending = runAgent(h.options)
  await entered.promise
  h.controller.abort()
  assert.deepEqual(await pending, { status: 'cancelled' })
  assert.deepEqual(h.events, [])
  assert.equal(h.count(), 0)
})

test('已经取消的调用不发请求', async () => {
  const h = harness(exchange())
  h.controller.abort()
  assert.deepEqual(await runAgent(h.options), { status: 'cancelled' })
  assert.equal(h.requests.length, 0)
})

test('取消信号传给工具，工具迟到完成后不继续模型循环', async () => {
  const entered = deferred()
  const release = deferred()
  let toolSignal
  const h = harness(exchange(), {
    execute: async (_input, signal) => {
      toolSignal = signal
      entered.resolve()
      await release.promise
      return { completed: true }
    }
  })
  const pending = runAgent(h.options)
  await entered.promise
  h.controller.abort()
  assert.equal(toolSignal.aborted, true)
  release.resolve()
  assert.deepEqual(await pending, { status: 'cancelled' })
  assert.equal(h.requests.length, 1)
  assert.equal(h.count(), 1)
  assert.ok(h.events.includes('time-1:interrupted'))
  assert.ok(!h.events.includes('time-1:succeeded'))
})

test('超时与用户取消区分，HTTP 失败关闭默认重试', async () => {
  const h = harness([
    async (request) => {
      await new Promise((resolve, reject) => {
        request.signal.addEventListener('abort', () => reject(request.signal.reason), {
          once: true
        })
      })
    }
  ])
  await assert.rejects(runAgent({ ...h.options, timeoutMs: 20 }), /超时/)
  const unavailable = harness([
    async () => new Response('{"error":{"message":"unavailable"}}', { status: 503 })
  ])
  await assert.rejects(runAgent(unavailable.options))
  assert.equal(unavailable.requests.length, 1)
})

test('格式修复次数和模型请求步数都有边界', async () => {
  const h = harness([chatReply('invalid'), chatReply('invalid'), chatReply('invalid')])
  await assert.rejects(runAgent(h.options), /修复次数/)
  assert.equal(h.requests.length, 3)
  const loop = harness([chatReply('', '', [toolCall('1')]), chatReply('', '', [toolCall('2')])])
  await assert.rejects(runAgent({ ...loop.options, maxSteps: 2 }), /步数/)
  assert.equal(loop.count(), 2)
})

test('Schema 合法但角色或演出越界时仍触发业务校验', async () => {
  for (const change of [
    { speakerId: 'unknown' },
    { performance: { motion: 'unknown', expression: 'auto' } }
  ]) {
    const invalid = { lines: [{ ...finalValue.lines[0], ...change }] }
    assert.ok(replySchema.safeParse(invalid).success)
    const h = harness([chatReply(JSON.stringify(invalid)), chatReply(finalText)])
    assert.equal((await runAgent(h.options)).status, 'completed')
    assert.equal(h.requests.length, 2)
  }
})

test('Anthropic 工具循环保留 thinking signature 和 tool_use 分组', async () => {
  const h = harness(
    [
      anthropicReply([
        { type: 'thinking', thinking: '先查时间。', signature: 'synthetic-signature' },
        { type: 'tool_use', id: 'time-1', name: 'get_current_time', input: {} }
      ]),
      anthropicReply([{ type: 'text', text: finalText }])
    ],
    { provider: 'anthropic' }
  )
  assert.equal((await runAgent(h.options)).status, 'completed')
  const messages = h.requests[1].body.messages
  const assistant = messages.find((message) => message.role === 'assistant')
  assert.equal(assistant.content[0].signature, 'synthetic-signature')
  assert.equal(messages.at(-1).content[0].type, 'tool_result')
})

test('Google 工具循环保留 thoughtSignature 和 functionResponse', async () => {
  const h = harness(
    [
      googleReply([
        { text: '先查时间。', thought: true },
        { functionCall: { name: 'get_current_time', args: {} }, thoughtSignature: 'c3ludGhldGlj' }
      ]),
      googleReply([{ text: finalText }])
    ],
    { provider: 'google' }
  )
  assert.equal((await runAgent(h.options)).status, 'completed')
  const contents = h.requests[1].body.contents
  const assistant = contents.find((message) => message.role === 'model')
  assert.equal(assistant.parts.find((part) => part.functionCall).thoughtSignature, 'c3ludGhldGlj')
  assert.equal(contents.at(-1).parts[0].functionResponse.name, 'get_current_time')
})

test('Output.object 的严格校验失败与保留 reasoning 的项目循环分别验证', async () => {
  const h = harness([chatReply('{"lines":[]}', '已产生 reasoning。')])
  await assert.rejects(
    generateText({
      model: h.options.model,
      prompt: '输出台词。',
      output: Output.object({ schema: replySchema }),
      maxRetries: 0,
      telemetry: { isEnabled: false }
    })
  )
  const controlled = harness([
    chatReply('{"lines":[]}', '已产生 reasoning。'),
    chatReply(finalText)
  ])
  await runAgent(controlled.options)
  assert.deepEqual(controlled.events, ['已产生 reasoning。'])
})
