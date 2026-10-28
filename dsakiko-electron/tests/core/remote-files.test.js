import path from 'node:path'
import { tmpdir } from 'node:os'
import { mkdtemp, rm, readFile, writeFile } from 'node:fs/promises'
import { afterEach, expect, test } from 'vitest'
import { MediaAssets } from '../../src/backend/resources/interface.js'
import { RemoteFiles } from '../../src/backend/remote-files/interface.js'
import { createFileAdapter, uploadUnsupported } from '../../src/backend/remote-files/adapter.js'
import {
  readAttachmentRules,
  selectAttachmentTransport
} from '../../src/backend/remote-files/policy.js'
import { Agent } from '../../src/backend/llm/interface.js'
import { Settings } from '../../src/backend/settings/interface.js'
import { defaultSettings } from '../../src/backend/core/defaults.js'

const cleanups = []
afterEach(async () => {
  for (const cleanup of cleanups.splice(0).reverse()) await cleanup()
})
const png = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a9uoAAAAASUVORK5CYII=',
  'base64'
)
const answer = JSON.stringify({
  lines: [{ speaker: '角色', text: '看到了', translation: null, emotion: 'happiness' }]
})
const observer = { onResponse: async () => {}, onToolResult: async () => {} }
function connection(provider = 'deepseek', protocol = 'chat-completions') {
  return {
    provider,
    protocol,
    baseUrl: 'https://fixture.invalid/v1',
    modelId: 'model',
    credentialScope: 'key',
    attachmentTransport: 'auto',
    capabilities: {}
  }
}
function requestFor(attachment, conn = connection()) {
  return {
    turnId: 'turn',
    mode: 'single-character',
    characters: [
      {
        id: 'actor',
        name: '角色',
        displayName: '角色',
        formId: 'default',
        modelVersion: 'v2',
        description: '',
        performances: { motions: [], expressions: [] }
      }
    ],
    userPersona: null,
    history: [],
    input: {
      kind: 'message',
      message: {
        kind: 'user',
        source: { kind: 'manual' },
        text: '描述附件',
        attachments: [attachment]
      }
    },
    scenario: null,
    supplementalContext: [],
    tools: [],
    connection: conn,
    options: {
      ...structuredClone(defaultSettings.generation),
      translationLanguage: null,
      reasoning: { enabled: false, effort: null },
      limits: { maxSteps: 2, maxRequestRetries: 0, maxFormatRepairs: 0 }
    }
  }
}
async function fixture(transport) {
  const directory = await mkdtemp(path.join(tmpdir(), 'remote-files-'))
  cleanups.push(() => rm(directory, { recursive: true, force: true }))
  const assets = new MediaAssets({
    appDataRoot: path.join(directory, 'app'),
    resourceLibraryRoot: path.join(directory, 'library'),
    cacheRoot: path.join(directory, 'cache')
  })
  await assets.initialize()
  const adapters = []
  const options = {
    directory: path.join(directory, 'remote'),
    assets,
    adapters: async () => adapters
  }
  const remoteFiles = new RemoteFiles(options)
  await remoteFiles.initialize()
  cleanups.push(() => remoteFiles.close())
  const info = await assets.importAsset(
    { kind: 'bytes', name: 'image.png', mediaType: 'image/png', bytes: png },
    'chat:first',
    'attachment'
  )
  function adapter(conn = connection(), key = 'secret') {
    const result = createFileAdapter(conn, key, transport)
    adapters.push(result)
    return result
  }
  const agent = new Agent({ assets, remoteFiles, credential: () => 'secret', fetch: transport })
  return {
    directory,
    assets,
    remoteFiles,
    options,
    adapter,
    agent,
    attachment: { asset: info.asset, name: info.name, mediaType: info.mediaType }
  }
}
function completion() {
  return Response.json({
    id: 'completion',
    object: 'chat.completion',
    created: 1,
    model: 'model',
    choices: [{ index: 0, message: { role: 'assistant', content: answer }, finish_reason: 'stop' }],
    usage: { prompt_tokens: 10, completion_tokens: 2, total_tokens: 12 }
  })
}

test('实际 SDK 上传及消息引用，历史与重启复用，检查阶段不上传', async () => {
  const calls = []
  const f = await fixture(async (url, init) => {
    calls.push({ url: String(url), init })
    return String(url).endsWith('/files') ? Response.json({ id: 'file-api-one' }) : completion()
  })
  const input = requestFor(f.attachment)
  await f.agent.inspectContext(input)
  expect(calls).toHaveLength(0)
  await f.agent.run(input, observer)
  expect(calls).toHaveLength(2)
  expect(calls[0].init.body.get('purpose')).toBe('user_data')
  const parts = JSON.parse(calls[1].init.body).messages.flatMap((item) =>
    Array.isArray(item.content) ? item.content : []
  )
  expect(parts).toContainEqual({ type: 'file', file_id: 'file-api-one' })
  const next = requestFor(f.attachment)
  next.history = [{ turnId: 'old', messages: [next.input.message] }]
  next.input.message = { ...next.input.message, attachments: [] }
  await f.agent.run(next, observer)
  const restarted = new RemoteFiles(f.options)
  await restarted.initialize()
  expect((await restarted.ensureUploaded(f.adapter(), f.attachment.asset)).reference).toEqual({
    deepseek: 'file-api-one'
  })
  expect(calls).toHaveLength(3)
  expect(await readFile(f.remoteFiles.file, 'utf8')).not.toContain('secret')
  expect(Buffer.from(await f.assets.read(f.attachment.asset))).toEqual(png)
})

test('规则只配置 DeepSeek；热读、坏文件拒绝、同请求使用快照', async () => {
  let uploads = 0
  const f = await fixture(async (url) => {
    if (String(url).endsWith('/files')) {
      uploads++
      return Response.json({ id: 'file-api-rules' })
    }
    return completion()
  })
  const rules = await readAttachmentRules(f.remoteFiles.rulesFile)
  const pdf = { mediaType: 'application/pdf', sizeBytes: 10 }
  expect(() => selectAttachmentTransport(rules, connection(), pdf, true)).toThrow()
  expect(
    selectAttachmentTransport(rules, connection('anthropic', 'anthropic-messages'), pdf, true)
      .transport
  ).toBe('file-api')
  const input = requestFor(f.attachment)
  await f.agent.inspectContext(input)
  await writeFile(
    f.remoteFiles.rulesFile,
    JSON.stringify({ version: 1, rules: [{ match: {}, enabled: false }] })
  )
  await f.agent.run(input, observer)
  expect(uploads).toBe(1)
  await expect(f.agent.run(requestFor(f.attachment), observer)).rejects.toHaveProperty(
    'problem.code',
    'unsupported_input'
  )
  await writeFile(f.remoteFiles.rulesFile, '{broken')
  await expect(f.agent.inspectContext(requestFor(f.attachment))).rejects.toHaveProperty(
    'problem.code',
    'invalid_attachment_rules'
  )
})

test('无上传实现自动内联，显式 file-api 不降级，未知允许而明确禁止拒绝', async () => {
  const calls = []
  const f = await fixture(async (url, init) => {
    calls.push({ url, body: JSON.parse(init.body) })
    return completion()
  })
  const conn = connection('openai-compatible')
  await f.agent.run(requestFor(f.attachment, conn), observer)
  expect(calls).toHaveLength(1)
  const parts = calls[0].body.messages.flatMap((item) =>
    Array.isArray(item.content) ? item.content : []
  )
  expect(parts.some((item) => item.image_url?.url.startsWith('data:image/png;base64,'))).toBe(true)
  await expect(
    f.agent.run(requestFor(f.attachment, { ...conn, attachmentTransport: 'file-api' }), observer)
  ).rejects.toHaveProperty('problem.code', 'file_upload_unavailable')
  await expect(
    f.agent.run(
      requestFor(f.attachment, {
        ...conn,
        capabilities: { imageInput: { status: 'unsupported' } }
      }),
      observer
    )
  ).rejects.toHaveProperty('problem.code', 'unsupported_input')
  expect(calls).toHaveLength(1)
})

test('并发合并，取消只结束单个等待，地址与密钥隔离，旧副本失效不影响新副本', async () => {
  let finish
  let uploads = 0
  const gate = new Promise((resolve) => {
    finish = resolve
  })
  const f = await fixture(async () => {
    uploads++
    await gate
    return Response.json({ id: `file-api-${uploads}` })
  })
  const adapter = f.adapter()
  const abort = new AbortController()
  const first = f.remoteFiles.ensureUploaded(adapter, f.attachment.asset, abort.signal)
  const second = f.remoteFiles.ensureUploaded(adapter, f.attachment.asset)
  abort.abort()
  await expect(first).rejects.toHaveProperty('name', 'AbortError')
  finish()
  const record = await second
  expect(uploads).toBe(1)
  await f.remoteFiles.ensureUploaded(f.adapter(connection(), 'different'), f.attachment.asset)
  await f.remoteFiles.ensureUploaded(
    f.adapter({ ...connection(), baseUrl: 'https://other.invalid/v1' }),
    f.attachment.asset
  )
  expect(uploads).toBe(3)
  await f.remoteFiles.invalidate(record)
  const replacement = await f.remoteFiles.ensureUploaded(adapter, f.attachment.asset)
  await f.remoteFiles.invalidate(record)
  expect((await f.remoteFiles.ensureUploaded(adapter, f.attachment.asset)).id).toBe(replacement.id)
  expect(uploads).toBe(4)
})

test('共享资源保护删除，失败持久化重试，缺少凭据不误用新账号', async () => {
  let deletes = 0
  let fail = true
  const f = await fixture(async (url, init) => {
    if (init.method !== 'DELETE') return Response.json({ id: 'file-api-delete' })
    deletes++
    if (fail) return Response.json({ error: 'temporary' }, { status: 500 })
    return Response.json({ id: 'file-api-delete', deleted: true })
  })
  const adapter = f.adapter()
  await f.remoteFiles.ensureUploaded(adapter, f.attachment.asset)
  await f.assets.addReferences('chat:second', [f.attachment.asset])
  await f.assets.releaseOwner('chat:first')
  expect((await f.remoteFiles.collectUnused())[0].status).toBe('in_use')
  expect(deletes).toBe(0)
  await f.assets.releaseOwner('chat:second')
  expect((await f.remoteFiles.collectUnused())[0].status).toBe('failed')
  expect((await f.remoteFiles.state()).records[0].status).toBe('pending-delete')
  fail = false
  expect((await new RemoteFiles(f.options).collectUnused())[0].status).toBe('deleted')
  expect(deletes).toBe(2)
  const another = await f.remoteFiles.ensureUploaded(
    f.adapter(connection(), 'old-key'),
    f.attachment.asset
  )
  const noCredentials = new RemoteFiles({ ...f.options, adapters: async () => [adapter] })
  expect((await noCredentials.collectUnused()).find((item) => item.id === another.id).status).toBe(
    'credential_unavailable'
  )
  expect(deletes).toBe(2)
})

test('Google SDK 上传返回 URI 和独立删除标识，未实现删除直接跳过', async () => {
  const calls = []
  const f = await fixture(async (url, init) => {
    calls.push({ url: String(url), method: init?.method })
    if (String(url).endsWith('/upload/v1beta/files'))
      return new Response('', {
        headers: { 'x-goog-upload-url': 'https://fixture.invalid/upload' }
      })
    if (String(url).endsWith('/upload'))
      return Response.json({
        file: {
          name: 'files/one',
          uri: 'https://fixture.invalid/file/one',
          mimeType: 'image/png',
          state: 'ACTIVE',
          expirationTime: '2099-01-01T00:00:00Z'
        }
      })
    throw Error('unexpected request')
  })
  const record = await f.remoteFiles.ensureUploaded(
    f.adapter(connection('gemini', 'google-generative-ai')),
    f.attachment.asset
  )
  expect(record.reference).toEqual({ google: 'https://fixture.invalid/file/one' })
  expect(record.deleteId).toBe('files/one')
  expect(record.expiresAt).toBe(Date.parse('2099-01-01T00:00:00Z'))
  await f.assets.releaseOwner('chat:first')
  expect((await f.remoteFiles.collectUnused())[0].status).toBe('unsupported')
  expect(calls).toHaveLength(2)
})

test('上传鉴权失败不降级，引用拒绝只标记失效，不重跑整轮生成', async () => {
  let uploads = 0
  let generates = 0
  let authentication = true
  const f = await fixture(async (url) => {
    if (String(url).endsWith('/files')) {
      uploads++
      return authentication
        ? Response.json({ error: { message: 'bad key' } }, { status: 401 })
        : Response.json({ id: 'file-api-invalid' })
    }
    generates++
    return Response.json(
      { error: { message: 'invalid file_id: file-api-invalid', code: 'invalid_file_id' } },
      { status: 400 }
    )
  })
  await expect(f.agent.run(requestFor(f.attachment), observer)).rejects.toHaveProperty(
    'problem.code',
    'file_upload_failed'
  )
  expect(generates).toBe(0)
  authentication = false
  await expect(f.agent.run(requestFor(f.attachment), observer)).rejects.toHaveProperty(
    'problem.code',
    'remote_file_rejected'
  )
  expect(generates).toBe(1)
  expect((await f.remoteFiles.state()).records.at(-1).status).toBe('invalid')
  await expect(f.agent.run(requestFor(f.attachment), observer)).rejects.toHaveProperty(
    'problem.code',
    'remote_file_rejected'
  )
  expect(uploads).toBe(3)
  expect(generates).toBe(2)
})

test('错误判定保守，新连接默认 auto，显式选择保持', async () => {
  expect(
    uploadUnsupported({ name: 'AI_UnsupportedFunctionalityError', functionality: 'file uploads' })
  ).toBe(true)
  expect(uploadUnsupported({ statusCode: 404 })).toBe(false)
  expect(
    uploadUnsupported({ name: 'AI_UnsupportedFunctionalityError', functionality: 'streaming' })
  ).toBe(false)
  const f = await fixture()
  const settings = new Settings({ directory: f.directory, assets: f.assets })
  const saved = await settings.saveConnection(
    { id: 'test', revision: 0, name: 'test', provider: 'deepseek', models: [] },
    { kind: 'replace', secret: 'key' }
  )
  expect(saved.attachmentTransport).toBe('auto')
  expect(
    (await settings.saveConnection({ ...saved, attachmentTransport: 'inline' }, { kind: 'keep' }))
      .attachmentTransport
  ).toBe('inline')
})

test('明确上传能力缺失才自动回退，普通 404 和超时不回退', async () => {
  const f = await fixture(async () => completion())
  f.remoteFiles.ensureUploaded = async () => {
    throw Object.assign(new Error('unsupported'), {
      name: 'AI_UnsupportedFunctionalityError',
      functionality: 'file uploads'
    })
  }
  const result = await f.agent.run(requestFor(f.attachment), observer)
  expect(result.attachmentDecisions[0]).toMatchObject({
    transport: 'inline',
    reason: 'upload_unsupported'
  })
  await expect(
    f.agent.run(
      requestFor(f.attachment, { ...connection(), attachmentTransport: 'file-api' }),
      observer
    )
  ).rejects.toHaveProperty('problem.code', 'file_upload_failed')
  for (const error of [
    Object.assign(new Error('route missing'), { statusCode: 404 }),
    new Error('timeout')
  ]) {
    f.remoteFiles.ensureUploaded = async () => {
      throw error
    }
    await expect(f.agent.run(requestFor(f.attachment), observer)).rejects.toHaveProperty(
      'problem.code',
      'file_upload_failed'
    )
  }
})

test('所有等待者取消后仍登记迟到结果，重启可以清理；过期引用重新上传', async () => {
  let finish
  let started
  const startedPromise = new Promise((resolve) => {
    started = resolve
  })
  const gate = new Promise((resolve) => {
    finish = resolve
  })
  let uploads = 0
  const f = await fixture(async (url, init) => {
    if (init.method === 'DELETE') return Response.json({ id: 'file-api-late', deleted: true })
    uploads++
    started()
    await gate
    return Response.json({ id: 'file-api-late', expires_at: uploads === 1 ? 1 : 4102444800 })
  })
  const adapter = f.adapter()
  const abort = new AbortController()
  const waiting = f.remoteFiles.ensureUploaded(adapter, f.attachment.asset, abort.signal)
  await startedPromise
  abort.abort()
  await expect(waiting).rejects.toHaveProperty('name', 'AbortError')
  finish()
  await Promise.all([...f.remoteFiles.flights.values()])
  expect((await f.remoteFiles.state()).records[0].reference).toEqual({ deepseek: 'file-api-late' })
  await f.remoteFiles.ensureUploaded(adapter, f.attachment.asset)
  expect(uploads).toBe(2)
  await f.assets.releaseOwner('chat:first')
  expect((await new RemoteFiles(f.options).collectUnused()).map((item) => item.status)).toEqual([
    'deleted',
    'deleted'
  ])
})

test('删除期间新资源持有者等待，显式删除失败后不再复用旧引用', async () => {
  let finish
  let started
  const gate = new Promise((resolve) => {
    finish = resolve
  })
  const deleting = new Promise((resolve) => {
    started = resolve
  })
  let uploads = 0
  const f = await fixture(async (url, init) => {
    if (init.method !== 'DELETE') return Response.json({ id: `file-api-${++uploads}` })
    started()
    await gate
    return Response.json({ id: 'file-api-1', deleted: true })
  })
  const adapter = f.adapter()
  await f.remoteFiles.ensureUploaded(adapter, f.attachment.asset)
  await f.assets.releaseOwner('chat:first')
  const collection = f.remoteFiles.collectUnused()
  await deleting
  let acquired = false
  const acquire = f.assets.addReferences('chat:new', [f.attachment.asset]).then(() => {
    acquired = true
  })
  await new Promise((resolve) => setImmediate(resolve))
  expect(acquired).toBe(false)
  finish()
  await collection
  await acquire
  const replacement = await f.remoteFiles.ensureUploaded(adapter, f.attachment.asset)
  expect(replacement.reference).toEqual({ deepseek: 'file-api-2' })
  const broken = {
    ...adapter,
    delete: async () => {
      throw Error('offline')
    }
  }
  await expect(f.remoteFiles.delete(broken, replacement)).rejects.toThrow('offline')
  expect((await f.remoteFiles.ensureUploaded(adapter, f.attachment.asset)).reference).toEqual({
    deepseek: 'file-api-3'
  })
})

test('Anthropic 通过 SDK 上传图片后引用，不依赖 DeepSeek 分支', async () => {
  const bodies = []
  const f = await fixture(async (url, init) => {
    if (String(url).endsWith('/files'))
      return Response.json({
        id: 'file_anthropic',
        type: 'file',
        filename: 'image.png',
        mime_type: 'image/png',
        size_bytes: 12,
        created_at: '2026-10-25T00:00:00Z'
      })
    bodies.push(JSON.parse(init.body))
    return Response.json({
      id: 'msg',
      type: 'message',
      role: 'assistant',
      model: 'claude-sonnet-4-5',
      content: [{ type: 'text', text: answer }],
      stop_reason: 'end_turn',
      stop_sequence: null,
      usage: { input_tokens: 2, output_tokens: 2 }
    })
  })
  const info = await f.assets.importAsset(
    {
      kind: 'bytes',
      bytes: png,
      name: 'image.png',
      mediaType: 'image/png'
    },
    'chat:first',
    'attachment'
  )
  const attachment = { asset: info.asset, name: info.name, mediaType: info.mediaType }
  await f.agent.run(
    requestFor(attachment, {
      ...connection('anthropic', 'anthropic-messages'),
      modelId: 'claude-sonnet-4-5'
    }),
    observer
  )
  const parts = bodies[0].messages.flatMap((item) =>
    Array.isArray(item.content) ? item.content : []
  )
  expect(
    parts.some((item) => item.type === 'image' && item.source?.file_id === 'file_anthropic')
  ).toBe(true)
})

test('规则按类型、大小、模型和路径组合匹配，配置未限制仍由 SDK 拒绝非法上传', async () => {
  const f = await fixture(async () => {
    throw Error('不应发送上传请求')
  })
  const rules = await readAttachmentRules(f.remoteFiles.rulesFile)
  const image = { mediaType: 'image/png', sizeBytes: 40 * 1024 * 1024 }
  expect(selectAttachmentTransport(rules, connection(), image, true)).toMatchObject({
    transport: 'file-api',
    inline: false
  })
  expect(() =>
    selectAttachmentTransport(rules, connection(), { ...image, sizeBytes: 65 * 1024 * 1024 }, true)
  ).toThrow()
  const policy = {
    version: 1,
    rules: [
      {
        match: { provider: 'deepseek', modelIds: ['model'] },
        transports: { 'file-api': { enabled: false } }
      }
    ]
  }
  expect(
    selectAttachmentTransport(policy, connection(), { ...image, sizeBytes: 2 }, true).transport
  ).toBe('inline')
  const pdf = await f.assets.importAsset(
    {
      kind: 'bytes',
      bytes: Buffer.from('%PDF-1.4\n'),
      name: 'doc.pdf',
      mediaType: 'application/pdf'
    },
    'chat:first',
    'attachment'
  )
  await writeFile(f.remoteFiles.rulesFile, JSON.stringify({ version: 1, rules: [] }))
  await expect(f.remoteFiles.ensureUploaded(f.adapter(), pdf.asset)).rejects.toThrow()
  await writeFile(f.remoteFiles.file, '{broken')
  await expect(f.remoteFiles.ensureUploaded(f.adapter(), f.attachment.asset)).rejects.toThrow()
  expect(await readFile(f.remoteFiles.file, 'utf8')).toBe('{broken')
})

test('真实后端先保存附件消息再上传；生成失败保留本地附件，删除对话不受远端删除失败影响', async () => {
  const { createCoreBackend } = await import('../../src/backend/create-core-backend.js')
  const directory = await mkdtemp(path.join(tmpdir(), 'remote-chat-'))
  cleanups.push(() => rm(directory, { recursive: true, force: true }))
  let backend
  let chatId
  let savedBeforeUpload = false
  let deleteFails = true
  backend = await createCoreBackend({
    appDataRoot: path.join(directory, 'app'),
    resourceLibraryRoot: path.join(directory, 'library'),
    cacheRoot: path.join(directory, 'cache'),
    scanReminders: false,
    fetch: async (url, init) => {
      if (init.method === 'DELETE')
        return deleteFails
          ? Response.json({ error: 'temporary' }, { status: 500 })
          : Response.json({ id: 'file-api-chat', deleted: true })
      if (String(url).endsWith('/files')) {
        const loaded = await backend.store.load(chatId)
        savedBeforeUpload = loaded.chat.turns[0].messages[0].content.attachments.length === 1
        return Response.json({ id: 'file-api-chat' })
      }
      return Response.json({ error: { message: 'model unavailable' } }, { status: 400 })
    }
  })
  cleanups.push(() => backend.close())
  await backend.characters.save({
    id: 'actor',
    revision: 0,
    defaultFormId: 'default',
    forms: [
      {
        id: 'default',
        displayName: '角色',
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
      id: 'connection',
      revision: 0,
      name: 'test',
      provider: 'deepseek',
      baseUrl: 'https://fixture.invalid/v1',
      models: [{ id: 'model', declared: {}, overrides: {} }]
    },
    { kind: 'replace', secret: 'secret' }
  )
  await backend.settings.update({
    generation: {
      connectionId: 'connection',
      modelId: 'model',
      reasoning: { enabled: false },
      limits: { maxRequestRetries: 0 }
    }
  })
  chatId = await backend.conversations.createChat({
    title: '附件测试',
    characterIds: ['actor'],
    mode: 'single-character'
  })
  const draft = await backend.imports.createDraft()
  const info = await backend.imports.importAsset(
    { kind: 'bytes', name: 'image.png', mediaType: 'image/png', bytes: png },
    'attachment',
    draft.draftId
  )
  await backend.conversations.createTurn(
    chatId,
    {
      text: '测试',
      attachments: [{ asset: info.asset, name: info.name, mediaType: info.mediaType }]
    },
    { requestId: 'submit' }
  )
  await backend.imports.commitDraft(draft.draftId, { kind: 'chat', id: chatId })
  await (
    await backend.conversations.state(chatId)
  ).active?.done
  expect(savedBeforeUpload).toBe(true)
  const loaded = await backend.store.load(chatId)
  expect(loaded.chat.turns[0].messages[0].content.attachments[0].asset).toEqual(info.asset)
  expect(JSON.stringify(loaded.chat)).not.toContain('file-api-chat')
  expect(Buffer.from(await backend.assets.read(info.asset))).toEqual(png)
  await backend.conversations.deleteChat(chatId)
  await backend.remoteFiles.collectUnused()
  expect((await backend.store.index()).entries).toHaveLength(0)
  expect((await backend.remoteFiles.state()).records[0].status).toBe('pending-delete')
  deleteFails = false
  await backend.remoteFiles.collectUnused()
  expect((await backend.remoteFiles.state()).records[0].status).toBe('deleted')
})

test('上传等待超时报 timeout，底层共享任务迟到成功仍持久保存', async () => {
  let finish
  const gate = new Promise((resolve) => {
    finish = resolve
  })
  const f = await fixture(async () => {
    await gate
    return Response.json({ id: 'file-api-timeout' })
  })
  const input = requestFor(f.attachment)
  input.options.timeoutMs = 30
  const run = f.agent.run(input, observer)
  await expect(run).rejects.toHaveProperty('problem.code', 'timeout')
  finish()
  await Promise.all([...f.remoteFiles.flights.values()])
  expect((await f.remoteFiles.state()).records[0].status).toBe('ready')
  expect(Buffer.from(await f.assets.read(f.attachment.asset))).toEqual(png)
})

test('对话请求仅发送图片，即使底层适配器支持文档也不上传 PDF', async () => {
  const calls = []
  const f = await fixture(async (url, init) => {
    calls.push({ url: String(url), body: JSON.parse(init.body) })
    return completion()
  })
  const info = await f.assets.importAsset(
    {
      kind: 'bytes',
      bytes: Buffer.from('%PDF-1.4\n'),
      name: 'work.pdf',
      mediaType: 'application/pdf'
    },
    'chat:first',
    'attachment'
  )
  await f.agent.run(
    requestFor({ asset: info.asset, name: info.name, mediaType: info.mediaType }),
    observer
  )
  expect(calls).toHaveLength(1)
  expect(calls[0].url).toContain('/chat/completions')
  expect(JSON.stringify(calls[0].body)).not.toContain('application/pdf')
})
