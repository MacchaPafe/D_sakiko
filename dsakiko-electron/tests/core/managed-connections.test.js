import { generateKeyPairSync, createHash } from 'node:crypto'
import { mkdtemp, writeFile, readFile, rm, unlink } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { createRequire } from 'node:module'
import { afterEach, expect, test, vi } from 'vitest'
import { generateText, jsonSchema } from 'ai'
import { SignJWT } from 'jose'
import { Settings } from '../../src/backend/settings/interface.js'
import { createCoreBackend } from '../../src/backend/create-core-backend.js'
import { authorize, issuer, resource, planScope } from '../../src/backend/settings/chatgpt-oauth.js'
import { createModel, reasoningOptions } from '../../src/backend/llm/adapters.js'
import { subscriptionFetch } from '../../src/backend/llm/chatgpt-responses.js'

const requireVertex = createRequire(import.meta.resolve('@ai-sdk/google-vertex'))
const { GoogleAuth } = requireVertex('google-auth-library')
const key = generateKeyPairSync('rsa', { modulusLength: 2048 })
const privateKey = key.privateKey.export({ type: 'pkcs8', format: 'pem' })
const dirs = []
async function fixture() {
  const directory = await mkdtemp(join(tmpdir(), 'dsakiko-llm-'))
  dirs.push(directory)
  return new Settings({ directory, assets: { registerOwnerReader() {} } })
}
afterEach(async () => {
  vi.restoreAllMocks()
  await Promise.all(dirs.splice(0).map((dir) => rm(dir, { recursive: true, force: true })))
})
function definition(provider, id, extra) {
  return {
    id: provider,
    revision: 0,
    name: provider,
    provider,
    models: [{ id, declared: {}, overrides: {} }],
    ...extra
  }
}

test('Vertex 导入后删除源文件，重启仍可用原项目认证并回传工具 ID 和思考签名', async () => {
  const original = await fixture()
  const file = join(dirs[0], 'download.json')
  await writeFile(
    file,
    JSON.stringify({
      type: 'service_account',
      project_id: 'project-test',
      client_email: 'agent@project-test.iam.gserviceaccount.com',
      private_key: privateKey,
      token_uri: 'https://invalid.example'
    })
  )
  const imported = original.vertexDrafts.stage(JSON.parse(await readFile(file, 'utf8')))
  expect(JSON.stringify(imported)).not.toContain('PRIVATE KEY')
  const modelId = 'gemini-4-argon'
  await original.saveConnection(definition('vertex', modelId, { vertex: imported.vertex }), {
    kind: 'vertex-import',
    ticket: imported.ticket
  })
  await unlink(file)
  const settings = new Settings({ directory: dirs[0], assets: { registerOwnerReader() {} } })
  const connection = await settings.resolveConnection({ connectionId: 'vertex', modelId })
  expect(JSON.stringify(await settings.listConnections())).not.toContain('PRIVATE KEY')
  const auth = vi
    .spyOn(GoogleAuth.prototype, 'getClient')
    .mockResolvedValue({ getAccessToken: async () => ({ token: 'mock-google-token' }) })
  const bodies = []
  const transport = async (url, options) => {
    expect(String(url)).toBe(
      'https://aiplatform.googleapis.com/v1beta1/projects/project-test/locations/global/publishers/google/models/gemini-4-argon:generateContent'
    )
    expect(new Headers(options.headers).get('authorization')).toBe('Bearer mock-google-token')
    bodies.push(JSON.parse(options.body))
    const parts =
      bodies.length === 1
        ? [
            {
              functionCall: { name: 'lookup', id: 'call-123', args: {} },
              thoughtSignature: 'signature'
            }
          ]
        : [{ text: '完成' }]
    return Response.json({
      candidates: [{ content: { role: 'model', parts }, finishReason: 'STOP' }],
      usageMetadata: { promptTokenCount: 10, candidatesTokenCount: 3, totalTokenCount: 13 }
    })
  }
  const model = createModel(
    connection,
    settings.credential(connection.credentialScope),
    {},
    transport
  )
  const tools = { lookup: { inputSchema: jsonSchema({ type: 'object', properties: {} }) } }
  const messages = [{ role: 'user', content: '查询' }]
  const options = {
    model,
    tools,
    messages,
    maxRetries: 0,
    providerOptions: reasoningOptions(connection, { enabled: true, effort: 'medium' })
  }
  const first = await generateText(options)
  expect(first.toolCalls[0].toolCallId).toBe('call-123')
  messages.push(...first.response.messages, {
    role: 'tool',
    content: [
      {
        type: 'tool-result',
        toolCallId: 'call-123',
        toolName: 'lookup',
        output: { type: 'text', value: 'ok' }
      }
    ]
  })
  expect((await generateText(options)).text).toBe('完成')
  const parts = bodies[1].contents.flatMap((item) => item.parts)
  expect(parts).toContainEqual({
    functionCall: { name: 'lookup', id: 'call-123', args: {} },
    thoughtSignature: 'signature'
  })
  expect(parts.find((p) => p.functionResponse).functionResponse.id).toBe('call-123')
  expect(bodies[1].generationConfig.thinkingConfig).toEqual({
    includeThoughts: true,
    thinkingLevel: 'MEDIUM'
  })
  expect(auth).toHaveBeenCalled()
  expect(reasoningOptions(connection, { enabled: false })).toEqual({})
  expect(() => reasoningOptions(connection, { enabled: true, budgetTokens: 1024 })).toThrow()
})

test('复用旧版注册、验证登录身份、刷新模型和同一登录会话，退出后保留连接', async () => {
  const settings = await fixture()
  const accounts = settings.accounts
  accounts.legacyFile = join(dirs[0], 'legacy.json')
  const clientId = 'oaiapp_example',
    hostId = 'urn:uuid:01234567-89ab-cdef-0123-456789abcdef'
  await writeFile(
    accounts.file,
    JSON.stringify({
      hostId,
      profiles: [
        { id: 'native', hostId, clientId: 'other-client' },
        { id: 'previous-import', hostId, clientId, subject: 'subject' }
      ]
    })
  )
  expect(await accounts.discover()).toEqual([])
  await writeFile(
    accounts.legacyFile,
    JSON.stringify({
      version: 1,
      host_id: hostId,
      accounts: [
        {
          issuer,
          subject: 'subject',
          client_id: clientId,
          email: 'test@example.com',
          refresh_token: 'legacy-secret'
        }
      ]
    })
  )
  const [profile] = await accounts.discover()
  expect(profile).toMatchObject({ id: 'previous-import', fromLegacy: true })
  expect(await accounts.list()).toMatchObject([
    { id: 'native', fromLegacy: false },
    { id: profile.id, fromLegacy: true }
  ])
  expect(await accounts.discover()).toHaveLength(1)
  expect(await readFile(accounts.file, 'utf8')).not.toContain('legacy-secret')
  let nonce,
    challenge,
    redirect,
    refreshes = 0,
    subject = 'subject'
  async function openBrowser(url) {
    const params = new URL(url).searchParams
    expect(params.get('client_id')).toBe(clientId)
    expect(params.get('ext_agent_host_id')).toBe(hostId)
    nonce = params.get('nonce')
    challenge = params.get('code_challenge')
    redirect = params.get('redirect_uri')
    const callback = new URL(redirect)
    callback.searchParams.set('state', 'wrong')
    expect((await fetch(callback)).status).toBe(400)
    callback.searchParams.set('state', params.get('state'))
    callback.searchParams.set('code', 'example-code')
    expect((await fetch(callback)).ok).toBe(true)
  }
  const models = [
    {
      slug: 'gpt-demo',
      display_name: '测试模型',
      visibility: 'list',
      supported_reasoning_levels: [{ effort: 'low' }],
      input_modalities: ['text']
    },
    { slug: 'invisible', visibility: 'hidden' }
  ]
  accounts.transport = async (url, options) => {
    if (url === issuer + '/.well-known/openid-configuration')
      return Response.json({
        issuer,
        jwks_uri: issuer + '/keys',
        revocation_endpoint: issuer + '/revoke'
      })
    if (url === issuer + '/keys')
      return Response.json({
        keys: [{ ...key.publicKey.export({ format: 'jwk' }), alg: 'RS256', kid: 'demo' }]
      })
    if (url === issuer + '/revoke') return Response.json({})
    if (url === resource + '/models') return Response.json({ models })
    expect(url).toBe(issuer + '/api/accounts/oauth/token')
    if (options.body.get('grant_type') === 'refresh_token') {
      refreshes++
      return Response.json({ access_token: 'rotated', refresh_token: 'refresh2', expires_in: 3600 })
    }
    expect(options.body.get('redirect_uri')).toBe(redirect)
    expect(createHash('sha256').update(options.body.get('code_verifier')).digest('base64url')).toBe(
      challenge
    )
    const token = await new SignJWT({ nonce })
      .setProtectedHeader({ alg: 'RS256', kid: 'demo' })
      .setIssuer(issuer)
      .setSubject(subject)
      .setAudience(clientId)
      .setExpirationTime('5m')
      .sign(key.privateKey)
    return Response.json({
      id_token: token,
      access_token: 'access',
      refresh_token: 'refresh1',
      scope: planScope,
      expires_in: 3600
    })
  }
  expect(await accounts.login(profile.id, openBrowser)).toMatchObject({ connected: true })
  subject = 'different'
  await expect(accounts.login(profile.id, openBrowser)).rejects.toThrow('原有')
  expect(await accounts.models(profile.id)).toHaveLength(1)
  const saved = await settings.saveConnection(
    definition('chatgpt', 'gpt-demo', { accountId: profile.id }),
    { kind: 'keep' }
  )
  const resolved = await settings.resolveConnection({ connectionId: saved.id, modelId: 'gpt-demo' })
  expect(resolved.capabilities.reasoningEfforts).toEqual(['low'])
  const access = settings.credential(resolved.credentialScope)
  expect(await Promise.all([access('access'), access('access')])).toEqual(['rotated', 'rotated'])
  expect(refreshes).toBe(1)
  await accounts.logout(profile.id)
  expect((await settings.listConnections())[0]).toMatchObject({
    accountId: profile.id,
    credentialConfigured: false,
    models: [{ id: 'gpt-demo' }]
  })
  await expect(access()).rejects.toThrow()
})

test('ChatGPT 订阅使用真实 SDK 编码工具，等待完整响应，限额和中断都不接受为成功', async () => {
  const connection = { provider: 'chatgpt', modelId: 'gpt-demo', capabilities: {} }
  const access = vi.fn(async (rejected) => (rejected ? 'new' : 'old'))
  const payload = {
    id: 'resp-1',
    created_at: 1,
    model: 'gpt-demo',
    status: 'completed',
    output: [
      {
        type: 'message',
        id: 'msg1',
        role: 'assistant',
        content: [{ type: 'output_text', text: 'OK', annotations: [] }]
      }
    ],
    usage: { input_tokens: 3, output_tokens: 1, total_tokens: 4 }
  }
  const send = vi.fn(async (url, opts) => {
    expect(String(url)).toBe(resource + '/responses')
    const body = JSON.parse(opts.body)
    expect(body).toMatchObject({
      stream: true,
      store: false,
      tools: [{ type: 'namespace', tools: [{ name: 'lookup' }] }]
    })
    expect(body).not.toHaveProperty('max_output_tokens')
    if (new Headers(opts.headers).get('authorization') === 'Bearer old')
      return Response.json({ error: { code: 'invalid_token' } }, { status: 401 })
    return new Response(
      'data: ' + JSON.stringify({ type: 'response.completed', response: payload }) + '\n\n'
    )
  })
  const result = await generateText({
    model: createModel(connection, access, {}, send),
    tools: { lookup: { inputSchema: jsonSchema({ type: 'object', properties: {} }) } },
    prompt: 'test',
    maxRetries: 0
  })
  expect(result.text).toBe('OK')
  expect(access.mock.calls).toEqual([[], ['old']])
  for (const event of [
    { type: 'response.output_text.delta', delta: 'partial' },
    {
      type: 'response.failed',
      response: { error: { code: 'subscription_sharing_usage_limit_exceeded' } }
    }
  ]) {
    const transport = subscriptionFetch(
      access,
      async () => new Response('data: ' + JSON.stringify(event) + '\n\n')
    )
    await expect(transport(resource + '/responses', { body: '{"input":[]}' })).rejects.toThrow()
  }
})

test('首次浏览器登录使用动态注册，返回的 client ID 用于兑换，取消会关闭回调监听', async () => {
  const hostId = 'urn:uuid:01234567-89ab-cdef-0123-456789abcdef'
  let callbackUrl
  async function firstLogin(url) {
    const params = new URL(url).searchParams
    expect(params.get('client_id')).toBe('dynamic_agent_client')
    expect(params.get('agent_name_hint')).toBe('数字小祥')
    const callback = new URL(params.get('redirect_uri'))
    callbackUrl = callback.href
    callback.searchParams.set('client_id', 'oaiapp_issued')
    callback.searchParams.set('state', params.get('state'))
    callback.searchParams.set('code', 'code')
    await fetch(callback)
  }
  const auth = await authorize({ hostId }, firstLogin, AbortSignal.timeout(5000))
  expect(auth.exchange).toMatchObject({ client_id: 'oaiapp_issued', redirect_uri: callbackUrl })
  const controller = new AbortController()
  await expect(
    authorize(
      { hostId },
      (url) => {
        callbackUrl = new URL(url).searchParams.get('redirect_uri')
        controller.abort()
      },
      controller.signal
    )
  ).rejects.toMatchObject({ problem: { code: 'cancelled' } })
  await expect(fetch(callbackUrl)).rejects.toThrow()
})

test('订阅退出后已有对话可以改用 Vertex，保留原来的用户消息和回复', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'dsakiko-switch-'))
  dirs.push(directory)
  const used = []
  const backend = await createCoreBackend({
    appDataRoot: join(directory, 'app'),
    cacheRoot: join(directory, 'cache'),
    resourceLibraryRoot: join(directory, 'library'),
    scanReminders: false,
    agent: {
      inspectContext: async () => ({ fit: 'fits' }),
      run: async (request, observer) => {
        used.push(request.connection.provider)
        await observer.onResponse({
          responseId: 'reply-' + request.turnId,
          protocol: {},
          appendParts: [
            {
              id: 'part-' + request.turnId,
              kind: 'dialogue',
              lines: [
                {
                  id: 'line-' + request.turnId,
                  speakerId: 'sakiko',
                  formId: 'default',
                  text: '测试回复',
                  translation: null,
                  emotion: null,
                  performance: { motion: 'auto', expression: 'auto' }
                }
              ]
            }
          ]
        })
        return { status: 'completed', usage: null }
      }
    }
  })
  try {
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
    const accounts = backend.settings.accounts
    accounts.transport = async (url) =>
      Response.json(
        url.endsWith('openid-configuration')
          ? { issuer, revocation_endpoint: issuer + '/revoke' }
          : {}
      )
    await writeFile(
      accounts.file,
      JSON.stringify({
        profiles: [
          {
            id: 'profile',
            hostId: 'test',
            clientId: 'client',
            tokens: { refresh_token: 'offline-test' },
            models: [{ id: 'gpt-demo', efforts: ['low'] }]
          }
        ]
      })
    )
    await backend.settings.saveConnection(
      definition('chatgpt', 'gpt-demo', { accountId: 'profile' }),
      { kind: 'keep' }
    )
    await backend.settings.saveConnection(
      definition('vertex', 'gemini-4-argon', {
        vertex: {
          project: 'project-test',
          location: 'global',
          email: 'agent@test.iam.gserviceaccount.com'
        }
      }),
      { kind: 'replace', secret: privateKey }
    )
    const { chatId } = await backend.conversations.createChatWithTurn(
      {
        title: '切换',
        characterIds: ['sakiko'],
        mode: 'single-character',
        settings: { generation: { connectionId: 'chatgpt', modelId: 'gpt-demo' } }
      },
      { text: '第一轮', attachments: [] },
      { requestId: 'first' }
    )
    const finished = async () => !(await backend.conversations.getSnapshot(chatId)).activeTurnId
    await expect.poll(finished).toBe(true)
    const [preview] = await backend.conversations.listChats()
    expect(preview.lastReply).toBe('测试回复')
    expect(preview.showTitle).toBeUndefined()
    const before = (await backend.store.load(chatId)).chat.turns
    expect(before[0].messages.length).toBe(2)
    await accounts.logout('profile')
    await backend.conversations.patchSettings(chatId, {
      generation: { connectionId: 'vertex', modelId: 'gemini-4-argon' }
    })
    await backend.conversations.createTurn(
      chatId,
      { text: '第二轮', attachments: [] },
      { requestId: 'second' }
    )
    await expect.poll(finished).toBe(true)
    const after = (await backend.store.load(chatId)).chat.turns
    expect(after).toHaveLength(2)
    expect(after[0]).toEqual(before[0])
    expect(used).toEqual(['chatgpt', 'vertex'])
  } finally {
    await backend.close()
  }
})
