import { mkdtemp, rm } from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { afterEach, expect, test, vi } from 'vitest'
import { WebSocket } from 'ws'
import { createCoreBackend } from '../../src/backend/create-core-backend.js'
import { Performance } from '../../src/backend/performance/interface.js'
import { startHttpServer } from '../../src/server/http-server.js'
import { createBackendClient } from '../../src/renderer/src/backend/client.js'
const cleanups = []
afterEach(async () => {
  for (const cleanup of cleanups.splice(0).reverse()) await cleanup()
})
const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms))
export async function until(predicate) {
  for (let index = 0; index < 300; index += 1) {
    if (await predicate()) return
    await wait(5)
  }
  throw new Error('等待条件超时')
}
function response(turnId) {
  return {
    responseId: 'response-' + turnId,
    protocol: {},
    appendParts: [
      {
        id: 'part-' + turnId,
        kind: 'dialogue',
        lines: [
          {
            id: 'line-' + turnId,
            speakerId: 'sakiko',
            formId: 'default',
            text: '你好',
            translation: null,
            emotion: null,
            performance: { motion: 'auto', expression: 'auto' }
          }
        ]
      }
    ]
  }
}
async function fixture(run) {
  const directory = await mkdtemp(path.join(os.tmpdir(), 'dsakiko-core-'))
  cleanups.push(() => rm(directory, { recursive: true, force: true }))
  const agent = {
    inspectContext: async () => ({ fit: 'fits' }),
    run: vi.fn(
      run ??
        (async (request, observer) => {
          await observer.onResponse(response(request.turnId))
          return { status: 'completed', usage: null }
        })
    )
  }
  const backend = await createCoreBackend({
    appDataRoot: path.join(directory, 'app'),
    resourceLibraryRoot: path.join(directory, 'library'),
    cacheRoot: path.join(directory, 'cache'),
    agent,
    scanReminders: false,
    performance: new Performance({ readingMsPerCharacter: 10, minimumReadingMs: 20 })
  })
  cleanups.push(() => backend.close())
  await backend.characters.save({
    id: 'sakiko',
    revision: 0,
    defaultFormId: 'default',
    forms: [
      {
        id: 'default',
        displayName: '祥子',
        description: '',
        avatar: null,
        themeColor: null,
        models: [],
        defaultModelId: null,
        voice: null
      }
    ],
    knowledgeMappings: [],
    extensions: {}
  })
  await backend.settings.saveConnection(
    {
      id: 'local',
      revision: 0,
      name: 'test',
      provider: 'openai-compatible',
      protocol: 'chat',
      baseUrl: 'http://127.0.0.1:9999/v1',
      attachmentTransport: 'inline',
      models: [{ id: 'test', declared: { contextTokens: 10000 }, overrides: {} }]
    },
    { kind: 'replace', secret: 'private-test-secret' }
  )
  const settings = await backend.settings.get()
  settings.generation.connectionId = 'local'
  settings.generation.modelId = 'test'
  await backend.settings.save(settings)
  const chatId = await backend.conversations.createChat({
    title: 'test',
    characterIds: ['sakiko'],
    mode: 'single-character'
  })
  return { backend, chatId, agent, directory }
}
test('无客户端继续推进；持久受理身份防止重复执行', async () => {
  const { backend, chatId, agent, directory } = await fixture()
  const submission = { text: '你好', attachments: [] }
  const turnId = await backend.conversations.createTurn(chatId, submission, { requestId: 'first' })
  expect(await backend.conversations.createTurn(chatId, submission, { requestId: 'first' })).toBe(
    turnId
  )
  await expect(
    backend.conversations.createTurn(
      chatId,
      { ...submission, text: 'different' },
      { requestId: 'first' }
    )
  ).rejects.toMatchObject({ problem: { code: 'request_conflict' } })
  await until(
    async () =>
      (await backend.conversations.getSnapshot(chatId)).executions[0]?.state === 'finished'
  )
  expect(agent.run).toHaveBeenCalledTimes(1)
  expect((await backend.conversations.getSnapshot(chatId)).admission.canGenerate).toBe(true)
  await backend.close()
  const restarted = await createCoreBackend({
    appDataRoot: path.join(directory, 'app'),
    resourceLibraryRoot: path.join(directory, 'library'),
    cacheRoot: path.join(directory, 'cache'),
    agent,
    scanReminders: false
  })
  cleanups.push(() => restarted.close())
  expect((await restarted.conversations.getSnapshot(chatId)).chat.turns).toHaveLength(1)
  expect((await restarted.conversations.getSnapshot(chatId)).executions).toEqual([])
})
test('保存失败保留内存结果，retrySave 不重跑模型', async () => {
  const { backend, chatId, agent } = await fixture()
  const originalSave = backend.store.save.bind(backend.store)
  let count = 0
  backend.store.save = async (value) => {
    count += 1
    if (count > 1) throw new Error('disk failed')
    return originalSave(value)
  }
  await backend.conversations.createTurn(
    chatId,
    { text: 'hi', attachments: [] },
    { requestId: 'save-failure' }
  )
  await until(
    async () => (await backend.conversations.getSnapshot(chatId)).persistence.state === 'failed'
  )
  await until(async () => !(await backend.conversations.getSnapshot(chatId)).activeTurnId)
  expect((await backend.conversations.getSnapshot(chatId)).chat.turns[0].messages).toHaveLength(2)
  backend.store.save = originalSave
  await backend.conversations.retrySave(chatId)
  expect(agent.run).toHaveBeenCalledTimes(1)
  expect((await backend.store.load(chatId)).chat.turns[0].messages).toHaveLength(2)
})
test('多客户端回答同一交互，只有一个答案进入自动轮', async () => {
  const { backend, chatId, agent } = await fixture(async (request, observer, signal) => {
    if (request.input.message.source.kind === 'manual') {
      const responseId = 'tool-response'
      await observer.onResponse({
        responseId,
        protocol: {},
        appendParts: [
          {
            id: 'call-part',
            kind: 'tool-call',
            callId: 'call-one',
            toolName: 'ask_user',
            arguments: { question: '选哪个？' },
            protocol: {}
          }
        ]
      })
      const outcome = await request.tools
        .find((item) => item.name === 'ask_user')
        .execute(
          { question: '选哪个？' },
          { turnId: request.turnId, assistantMessageId: responseId, callId: 'call-one' },
          signal
        )
      await observer.onToolResult({
        id: 'tool-result',
        kind: 'tool-result',
        assistantMessageId: responseId,
        callId: 'call-one',
        origin: 'tool',
        outcome,
        extensions: {}
      })
    }
    await observer.onResponse(response(request.turnId))
    return { status: 'completed', usage: null }
  })
  await backend.conversations.setTools(chatId, { enabled: ['ask_user'] })
  const turnId = await backend.conversations.createTurn(
    chatId,
    { text: '问我', attachments: [] },
    { requestId: 'question' }
  )
  await until(async () => (await backend.interactions.list()).pending.length === 1)
  const requestId = turnId + ':call-one'
  const results = await Promise.allSettled([
    backend.interactions.complete({ requestId, status: 'succeeded', value: 'A' }),
    backend.interactions.complete({ requestId, status: 'succeeded', value: 'B' })
  ])
  expect(results.filter((item) => item.status === 'fulfilled')).toHaveLength(1)
  await until(async () => (await backend.conversations.getSnapshot(chatId)).chat.turns.length === 2)
  await until(async () => !(await backend.conversations.getSnapshot(chatId)).activeTurnId)
  expect(agent.run).toHaveBeenCalledTimes(2)
})
test('真实 HTTP/WS 与前端封装；凭据隔离和路由白名单', async () => {
  const { backend, chatId } = await fixture()
  const server = await startHttpServer(backend)
  cleanups.push(() => server.close())
  const client = createBackendClient({ ...server, WebSocket })
  cleanups.push(() => client.close())
  const snapshots = []
  const stop = client.conversations.observe(chatId, (snapshot) => snapshots.push(snapshot))
  cleanups.push(stop)
  await until(() => snapshots.length > 0)
  const turnId = await client.conversations.createTurn(
    chatId,
    { text: 'http', attachments: [] },
    { requestId: 'http' }
  )
  await until(() =>
    snapshots.some((snapshot) =>
      snapshot.executions.some((item) => item.turnId === turnId && item.state === 'finished')
    )
  )
  expect(JSON.stringify(await client.settings.listConnections())).not.toContain(
    'private-test-secret'
  )
  const denied = await fetch(server.baseUrl + '/api/v1/call/settings/get', {
    method: 'POST',
    body: '{}'
  })
  expect((await denied.json()).problem.code).toBe('unauthorized')
  const privateCall = await fetch(server.baseUrl + '/api/v1/call/settings/resolveConnection', {
    method: 'POST',
    headers: { Authorization: 'Bearer ' + server.token },
    body: JSON.stringify({ args: [] })
  })
  expect((await privateCall.json()).problem.code).toBe('method_not_found')
})
test('停止保留当前句，逻辑结束前拒绝下一轮；不依赖客户端回执', async () => {
  const { backend, chatId } = await fixture(async (request, observer, signal) => {
    const update = response(request.turnId)
    update.appendParts[0].lines[0].text = '这段文字保证停止时当前句尚未结束。'
    await observer.onResponse(update)
    await new Promise((resolve, reject) => {
      signal.addEventListener('abort', () => reject(new DOMException('stop', 'AbortError')), {
        once: true
      })
    })
  })
  const turnId = await backend.conversations.createTurn(
    chatId,
    { text: 'go', attachments: [] },
    { requestId: 'stop' }
  )
  await until(
    async () => (await backend.conversations.getSnapshot(chatId)).presentation.lines.length === 1
  )
  await backend.conversations.stopTurn(chatId, turnId)
  expect((await backend.conversations.getSnapshot(chatId)).executions[0].state).toBe('terminated')
  await expect(
    backend.conversations.createTurn(
      chatId,
      { text: 'next', attachments: [] },
      { requestId: 'blocked' }
    )
  ).rejects.toMatchObject({ problem: { code: 'chat_busy' } })
  await until(async () => (await backend.conversations.getSnapshot(chatId)).admission.canGenerate)
})
test('初始保存失败不启动模型；并发存档更新不能覆盖较新版本', async () => {
  const { backend, chatId, agent } = await fixture()
  const save = backend.store.save.bind(backend.store)
  backend.store.save = async () => {
    throw new Error('disk failed')
  }
  await expect(
    backend.conversations.createTurn(
      chatId,
      { text: 'hi', attachments: [] },
      { requestId: 'not-admitted' }
    )
  ).rejects.toThrow('disk failed')
  expect(agent.run).not.toHaveBeenCalled()
  expect((await backend.conversations.getSnapshot(chatId)).chat.turns).toHaveLength(0)
  backend.store.save = save
  const loaded = (await backend.store.load(chatId)).chat
  const results = await Promise.allSettled([
    save({ ...loaded, title: 'A' }),
    save({ ...loaded, title: 'B' })
  ])
  expect(results.filter((item) => item.status === 'fulfilled')).toHaveLength(1)
  expect(results.find((item) => item.status === 'rejected').reason.problem.code).toBe(
    'revision_conflict'
  )
})
test('重生成请求重试及删除历史后的旧请求均不重跑', async () => {
  const { backend, chatId, agent } = await fixture()
  const input = { text: 'hi', attachments: [] }
  const first = await backend.conversations.createTurn(chatId, input, { requestId: 'original' })
  await until(async () => (await backend.conversations.getSnapshot(chatId)).admission.canGenerate)
  const second = await backend.conversations.recreateTurn(chatId, first, {
    requestId: 'regenerate'
  })
  expect(await backend.conversations.recreateTurn(chatId, first, { requestId: 'regenerate' })).toBe(
    second
  )
  await until(async () => (await backend.conversations.getSnapshot(chatId)).admission.canGenerate)
  await backend.conversations.rollback(chatId, null)
  expect(await backend.conversations.createTurn(chatId, input, { requestId: 'original' })).toBe(
    first
  )
  expect((await backend.conversations.getSnapshot(chatId)).chat.turns).toHaveLength(0)
  expect(agent.run).toHaveBeenCalledTimes(2)
})
test('WebSocket 断线重连读取最新快照，不停止后端任务', async () => {
  const { backend, chatId } = await fixture()
  const server = await startHttpServer(backend)
  cleanups.push(() => server.close())
  const sockets = []
  class CapturedSocket extends WebSocket {
    constructor(url) {
      super(url)
      sockets.push(this)
    }
  }
  const client = createBackendClient({ ...server, WebSocket: CapturedSocket })
  cleanups.push(() => client.close())
  const snapshots = []
  cleanups.push(client.conversations.observe(chatId, (value) => snapshots.push(value)))
  await until(() => snapshots.length > 0)
  sockets[0].close()
  await backend.conversations.createTurn(
    chatId,
    { text: 'offline', attachments: [] },
    { requestId: 'offline' }
  )
  await until(
    () =>
      sockets.length >= 2 && snapshots.some((value) => value.executions[0]?.state === 'finished')
  )
})
test('音频失败按无声放行，不终止轮次组', async () => {
  const { backend, chatId } = await fixture()
  backend.characters.resolveVoice = async () => ({ voice: {}, selected: 'default', problems: [] })
  backend.speech.submit = async () => 'fake-task'
  backend.speech.waitForResult = async () => ({
    status: 'failed',
    problem: { code: 'tts_failed', message: 'failed', retryable: false }
  })
  await backend.conversations.setSpeechOptions(chatId, { enabled: true })
  await backend.conversations.createTurn(
    chatId,
    { text: 'hi', attachments: [] },
    { requestId: 'tts' }
  )
  await until(async () => !(await backend.conversations.getSnapshot(chatId)).activeTurnId)
  const snapshot = await backend.conversations.getSnapshot(chatId)
  expect(snapshot.executions[0].state).toBe('finished')
  expect(snapshot.audioProblems).toHaveLength(1)
})
test('世界书工具注入固定调用来源，不允许模型自报范围或身份', async () => {
  const { backend, chatId } = await fixture()
  const query = vi.fn(async () => [])
  const tools = await backend.tools.resolve(
    chatId,
    { enabled: [] },
    { hostCapabilities: [] },
    query
  )
  const tool = tools[0]
  const parameters = {
    perspectiveCharacterId: 'sakiko',
    kind: 'event',
    text: '问题',
    tokenBudget: 100
  }
  const context = { turnId: 'turn', assistantMessageId: 'assistant', callId: 'call' }
  const signal = new AbortController().signal
  await tool.execute(parameters, context, signal)
  expect(query).toHaveBeenCalledExactlyOnceWith(parameters, { kind: 'tool-query', context }, signal)
  expect(tool.parametersSchema.additionalProperties).toBe(false)
  expect(Object.keys(tool.parametersSchema.properties)).not.toContain('origin')
})
