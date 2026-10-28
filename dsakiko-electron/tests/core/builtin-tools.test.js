import { mkdtemp, rm, writeFile, symlink, readFile } from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { afterEach, expect, test, vi } from 'vitest'
import { createCoreBackend } from '../../src/backend/create-core-backend.js'
import { fileTools } from '../../src/backend/tools/files.js'
import { systemTool } from '../../src/backend/tools/system.js'
import { weatherTool } from '../../src/backend/tools/weather.js'
import { webSearchTool } from '../../src/backend/tools/web-search.js'
import { fetchJson } from '../../src/backend/tools/network.js'

const cleanups = []
afterEach(async () => {
  for (const cleanup of cleanups.splice(0).reverse()) await cleanup()
})
async function until(predicate) {
  for (let i = 0; i < 300; i++) {
    if (await predicate()) return
    await new Promise((resolve) => setTimeout(resolve, 10))
  }
  throw Error('等待后端状态超时')
}
async function fixture(run = async () => ({ status: 'completed', usage: null })) {
  const directory = await mkdtemp(path.join(os.tmpdir(), 'builtin-tools-'))
  cleanups.push(() => rm(directory, { recursive: true, force: true }))
  const agent = { inspectContext: async () => ({ fit: 'fits' }), run: vi.fn(run) }
  const options = {
    appDataRoot: path.join(directory, 'app'),
    resourceLibraryRoot: path.join(directory, 'library'),
    cacheRoot: path.join(directory, 'cache'),
    scanReminders: false,
    agent
  }
  const backend = await createCoreBackend(options)
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
      id: 'test',
      revision: 0,
      name: 'test',
      provider: 'openai-compatible',
      protocol: 'chat',
      baseUrl: 'http://127.0.0.1:9999/v1',
      attachmentTransport: 'auto',
      models: [{ id: 'test', declared: { contextTokens: 10000 }, overrides: {} }]
    },
    { kind: 'replace', secret: 'test' }
  )
  await backend.settings.update({ generation: { connectionId: 'test', modelId: 'test' } })
  const chatId = await backend.conversations.createChat({
    title: '工具测试',
    characterIds: ['actor'],
    mode: 'single-character'
  })
  return { backend, chatId, directory, agent, options }
}
async function idle(backend, chatId) {
  await until(async () => !(await backend.conversations.getSnapshot(chatId)).activeTurnId)
}
async function invoke(request, observer, signal, name, args, callId = 'call') {
  const responseId = 'response-' + request.turnId
  await observer.onResponse({
    responseId,
    protocol: {},
    appendParts: [
      { id: callId, kind: 'tool-call', callId, toolName: name, arguments: args, protocol: {} }
    ]
  })
  const outcome = await request.tools
    .find((tool) => tool.name === name)
    .execute(args, { turnId: request.turnId, assistantMessageId: responseId, callId }, signal)
  await observer.onToolResult({
    id: 'result-' + callId,
    kind: 'tool-result',
    assistantMessageId: responseId,
    callId,
    origin: 'tool',
    outcome,
    extensions: {}
  })
  return outcome
}

test('非图片入 workspace，图片保留附件；检查不复制，重复受理不复制，分支与归档文件独立', async () => {
  const { backend, chatId, agent } = await fixture()
  const upload = async (name, mediaType, text) => {
    const info = await backend.assets.importAsset(
      { kind: 'bytes', name, mediaType, bytes: Buffer.from(text) },
      'chat:upload',
      'attachment'
    )
    return { asset: info.asset, name, mediaType }
  }
  const doc = await upload('note.txt', 'text/plain', 'before')
  const image = await upload('image.png', 'image/png', 'image')
  const input = { text: '查看文件', attachments: [doc, image] }
  await backend.conversations.inspectContext(chatId, input)
  expect(await backend.conversations.listWorkspaceFiles(chatId)).toEqual([])
  const turnId = await backend.conversations.createTurn(chatId, input, { requestId: 'upload' })
  await idle(backend, chatId)
  const [file] = await backend.conversations.listWorkspaceFiles(chatId)
  expect(file.asset).not.toEqual(doc.asset)
  expect((await backend.assets.state()).owners['chat:' + chatId]).not.toContain(doc.asset.id)
  expect(await backend.assets.localPath(file.asset)).toContain(`/workspaces/${chatId}/note.txt`)
  const projection = agent.run.mock.calls[0][0].input.message
  expect(projection.attachments).toEqual([image])
  expect(projection.text).toContain('note.txt')
  expect(await backend.conversations.createTurn(chatId, input, { requestId: 'upload' })).toBe(
    turnId
  )
  expect(await backend.conversations.listWorkspaceFiles(chatId)).toHaveLength(1)
  const branch = await backend.conversations.forkChat(chatId, turnId)
  const [branchFile] = await backend.conversations.listWorkspaceFiles(branch)
  expect(branchFile.asset).not.toEqual(file.asset)
  const archive = await backend.conversations.exportChats([chatId])
  const imported = await backend.conversations.importChats(archive)
  const [importFile] = await backend.conversations.listWorkspaceFiles(imported.chatIds[0])
  expect(importFile.asset).not.toEqual(file.asset)
  await backend.assets.writeWorkspaceFile(chatId, 'note.txt', Buffer.from('after'), 'chat:test', {
    overwrite: true
  })
  expect(Buffer.from(await backend.assets.read(file.asset)).toString()).toBe('after')
  expect(Buffer.from(await backend.assets.read(branchFile.asset)).toString()).toBe('before')
  expect(Buffer.from(await backend.assets.read(importFile.asset)).toString()).toBe('before')
  const edited = { text: '修改后再次发送', attachments: [doc] }
  const replacement = await backend.conversations.editAndRecreateTurn(chatId, turnId, edited, {
    requestId: 'edit'
  })
  await idle(backend, chatId)
  expect(
    await backend.conversations.editAndRecreateTurn(chatId, turnId, edited, { requestId: 'edit' })
  ).toBe(replacement)
})

test('写工具必须来自实际调用，同名写入需覆盖，历史下载引用读取新内容', async () => {
  const results = []
  const { backend, chatId } = await fixture(async (request, observer, signal) => {
    const filename = 'output.txt'
    results.push(
      await invoke(request, observer, signal, 'export_document', { filename, content: 'one' })
    )
    await expect(
      invoke(request, observer, signal, 'export_document', { filename, content: 'bad' }, 'conflict')
    ).rejects.toHaveProperty('problem.code', 'workspace_name_conflict')
    results.push(
      await invoke(
        request,
        observer,
        signal,
        'export_document',
        { filename, content: 'two', overwrite: true },
        'overwrite'
      )
    )
    return { status: 'completed', usage: null }
  })
  await backend.conversations.setTools(chatId, { enabled: ['export_document'] })
  await expect(
    backend.conversations.writeToolFile(chatId, { filename: 'x', content: 'x' }, {})
  ).rejects.toThrow()
  await backend.conversations.createTurn(
    chatId,
    { text: '写文件', attachments: [] },
    { requestId: 'write' }
  )
  await idle(backend, chatId)
  expect(results).toHaveLength(2)
  expect(results[0].value.asset).toEqual(results[1].value.asset)
  expect(Buffer.from(await backend.assets.read(results[0].value.asset)).toString()).toBe('two')
  expect(await backend.conversations.listWorkspaceFiles(chatId)).toHaveLength(1)
})

test('工作文件拒绝路径与符号链接；读取默认不限，配置 roots 后检查真实路径并提供有界分页', async () => {
  const { backend, chatId, directory } = await fixture()
  const { assets, settings } = backend
  for (const filename of ['../escape', '/tmp/escape', 'a/b', 'a\\b', '..', 'CON.txt'])
    await expect(
      assets.writeWorkspaceFile(chatId, filename, Buffer.from('x'), 'chat:test')
    ).rejects.toThrow()
  const info = await assets.writeWorkspaceFile(
    chatId,
    'file.txt',
    Buffer.from('a'.repeat(15000) + '\nneedle'),
    'chat:test'
  )
  const renamed = await assets.writeWorkspaceFile(
    chatId,
    'file.txt',
    Buffer.from('second'),
    'chat:test'
  )
  expect(renamed.name).toBe('file (2).txt')
  const tools = fileTools({ assets, settings, write: () => {} })
  const call = (name, input) =>
    tools.find((tool) => tool.definition.name === name).bind({ chatId })(input)
  const first = (await call('read_file_content', { file_path: 'file.txt' })).value
  expect(first.content).toHaveLength(12000)
  expect(first.next).toEqual({ start_line: 1, start_column: 12000 })
  const second = (await call('read_file_content', { file_path: 'file.txt', ...first.next })).value
  expect(second.content).toBe('a'.repeat(3000) + '\nneedle')
  expect(
    (await call('grep_search_in_file', { file_path: 'file.txt', query: 'needle' })).value.matches[0]
      .line
  ).toBe(2)
  const external = path.join(directory, 'external.txt')
  await writeFile(external, 'outside')
  expect((await call('read_file_content', { file_path: external })).value.content).toBe('outside')
  await settings.update({ tools: { files: { readMode: 'roots', readableRoots: [] } } })
  await expect(call('read_file_content', { file_path: external })).rejects.toHaveProperty(
    'problem.code',
    'file_read_forbidden'
  )
  const workspace = await assets.workspaceDirectory(chatId)
  await symlink(external, path.join(workspace, 'linked.txt'))
  await expect(call('read_file_content', { file_path: 'linked.txt' })).rejects.toThrow()
  await expect(
    assets.writeWorkspaceFile(chatId, 'linked.txt', Buffer.from('bad'), 'chat:test', {
      overwrite: true
    })
  ).rejects.toThrow()
  await rm(await assets.localPath(info.asset))
  await symlink(external, path.join(workspace, info.name))
  await expect(assets.read(info.asset)).rejects.toThrow()
  expect(await readFile(external, 'utf8')).toBe('outside')
  await settings.update({ tools: { files: { readableRoots: [directory] } } })
  expect((await call('read_file_content', { file_path: external })).value.content).toBe('outside')
  await assets.writeWorkspaceFile(chatId, 'doc.pdf', Buffer.from('%PDF-1.4'), 'chat:test')
  await expect(call('read_file_content', { file_path: 'doc.pdf' })).rejects.toHaveProperty(
    'problem.code',
    'unsupported_file_format'
  )
})

test('抽签创建后重启仍可开始，多客户端重复开始只保存一个结果并自动回复一次', async () => {
  const { backend, chatId, options, agent } = await fixture(async (request, observer, signal) => {
    if (request.input.message.source.kind === 'manual')
      await invoke(request, observer, signal, 'start_lottery', {
        title: '吃什么',
        options: ['面', '饭', '汤']
      })
    return { status: 'completed', usage: null }
  })
  await backend.conversations.setTools(chatId, { enabled: ['start_lottery'] })
  await backend.conversations.createTurn(
    chatId,
    { text: '抽签', attachments: [] },
    { requestId: 'lottery' }
  )
  await idle(backend, chatId)
  const [lottery] = await backend.conversations.listLotteries(chatId)
  expect(lottery.result).toBeNull()
  await backend.close()
  const restarted = await createCoreBackend(options)
  cleanups.push(() => restarted.close())
  const draws = await Promise.all(
    Array.from({ length: 5 }, () => restarted.conversations.drawLottery(chatId, lottery.id))
  )
  expect(
    draws.every((draw) => JSON.stringify(draw.result) === JSON.stringify(draws[0].result))
  ).toBe(true)
  await until(async () => (await restarted.conversations.listLotteries(chatId))[0].deliveredTurnId)
  await until(() => agent.run.mock.calls.length === 2)
  await idle(restarted, chatId)
  expect(agent.run).toHaveBeenCalledTimes(2)
  const final = (await restarted.conversations.getSnapshot(chatId)).chat
  expect(final.turns).toHaveLength(2)
  expect(final.turns[1].messages[0].results[0].result).toEqual(draws[0].result)
  await restarted.conversations.drawLottery(chatId, lottery.id)
  await restarted.conversations.deliverLotteries(chatId)
  expect(agent.run).toHaveBeenCalledTimes(2)
})

test('Bocha 密钥不出现在公开设置，搜索限制结果数，预留厂商配置不会静默回退', async () => {
  const { backend } = await fixture()
  await backend.settings.saveToolCredential('bocha', { kind: 'replace', secret: 'private-bocha' })
  expect(JSON.stringify(await backend.settings.get())).not.toContain('private-bocha')
  expect(await backend.settings.getToolCredentialStatus('bocha')).toEqual({ configured: true })
  const transport = vi.fn(async () =>
    Response.json({
      code: 200,
      data: { webPages: { value: [{ name: '标题', url: 'https://example.com', summary: '摘要' }] } }
    })
  )
  const call = webSearchTool(backend.settings, transport).bind({})
  expect((await call({ query: '测试', top_k: 20 })).value.results[0].title).toBe('标题')
  expect(JSON.parse(transport.mock.calls[0][1].body).count).toBe(5)
  expect(transport.mock.calls[0][1].headers.Authorization).toBe('Bearer private-bocha')
  await backend.settings.update({ tools: { webSearch: { provider: 'deepseek' } } })
  await expect(call({ query: '测试' })).rejects.toHaveProperty(
    'problem.code',
    'search_provider_unavailable'
  )
  expect(transport).toHaveBeenCalledTimes(1)
})

test('天气默认 IP 只短期缓存位置，显式城市优先，硬件部分失败仍返回其余指标', async () => {
  const { backend } = await fixture()
  const transport = vi.fn(async (url) => {
    if (String(url).includes('ipwho.is'))
      return Response.json({ success: true, city: '上海', latitude: 31, longitude: 121 })
    if (String(url).includes('geocoding'))
      return Response.json({ results: [{ name: '北京', latitude: 39, longitude: 116 }] })
    return Response.json({
      timezone: 'Asia/Shanghai',
      current: { temperature_2m: 20 },
      current_units: { temperature_2m: '°C' }
    })
  })
  const weather = weatherTool(backend.settings, transport).bind({})
  expect((await weather({})).value.locationSource).toBe('ip')
  await weather({})
  expect(transport.mock.calls.filter(([url]) => String(url).includes('ipwho.is'))).toHaveLength(1)
  expect((await weather({ city: '北京' })).value).toMatchObject({
    city: '北京',
    locationSource: 'argument'
  })
  await backend.settings.update({ tools: { weather: { ipLocation: false } } })
  await expect(weather({})).rejects.toHaveProperty('problem.code', 'weather_location_required')
  const hardware = systemTool({
    cpu: async () => ({ brand: 'test', cores: 8 }),
    currentLoad: async () => ({ currentLoad: 15 }),
    mem: async () => {
      throw Error('missing')
    },
    graphics: async () => {
      throw Error('missing')
    }
  }).bind({})
  const value = (await hardware({})).value
  expect(value.cpu).toMatchObject({ model: 'test', usagePercent: 15 })
  expect(value.memory.totalBytes).toBeGreaterThan(0)
  expect(value.gpu.reason).toBe('unavailable_or_timeout')
})

test('网络返回体有界且错误不泄露远端响应或凭据', async () => {
  await expect(
    fetchJson(
      async () => new Response('private-secret', { status: 500 }),
      'https://example.com',
      {}
    )
  ).rejects.not.toThrow('private-secret')
  await expect(
    fetchJson(async () => new Response('x'.repeat(3 * 1024 * 1024)), 'https://example.com', {})
  ).rejects.toThrow()
})

test('工具选择按轮固定并自动加入依赖，停止后的抽签不可再开始', async () => {
  const { backend, chatId, agent } = await fixture(async (request, observer, signal) => {
    if (request.tools.some((tool) => tool.name === 'start_lottery'))
      await invoke(request, observer, signal, 'start_lottery', {
        title: '选择',
        options: ['甲', '乙']
      })
    return { status: 'completed', usage: null }
  })
  const definitions = await backend.tools.list()
  expect(definitions).toHaveLength(13)
  await backend.conversations.createTurn(
    chatId,
    { text: '未开启工具', attachments: [] },
    { requestId: 'disabled' }
  )
  await idle(backend, chatId)
  expect(agent.run.mock.calls[0][0].tools).toEqual([])
  await backend.conversations.setTools(chatId, {
    enabled: ['start_lottery', 'create_reminder', 'change_character_live2d']
  })
  const turnId = await backend.conversations.createTurn(
    chatId,
    { text: '开启', attachments: [] },
    { requestId: 'enabled' }
  )
  await idle(backend, chatId)
  expect(agent.run.mock.calls[1][0].tools.map((tool) => tool.name)).toEqual(
    expect.arrayContaining(['get_current_datetime', 'fetch_all_live2d_models'])
  )
  expect(agent.run.mock.calls[0][0].tools).toEqual([])
  const [lottery] = await backend.conversations.listLotteries(chatId)
  await backend.conversations.stopTurn(chatId, turnId)
  await expect(backend.conversations.drawLottery(chatId, lottery.id)).rejects.toHaveProperty(
    'problem.code',
    'lottery_expired'
  )
})

test('抽签结果保存失败不交付；重试保存后重复点击返回已确定结果', async () => {
  const { backend, chatId, agent } = await fixture(async (request, observer, signal) => {
    if (request.input.message.source.kind === 'manual')
      await invoke(request, observer, signal, 'start_lottery', {
        title: '选择',
        options: ['甲', '乙']
      })
    return { status: 'completed', usage: null }
  })
  await backend.conversations.setTools(chatId, { enabled: ['start_lottery'] })
  await backend.conversations.createTurn(
    chatId,
    { text: '抽签', attachments: [] },
    { requestId: 'first' }
  )
  await idle(backend, chatId)
  const [lottery] = await backend.conversations.listLotteries(chatId)
  const save = vi.spyOn(backend.store, 'save').mockRejectedValueOnce(Error('disk full'))
  await expect(backend.conversations.drawLottery(chatId, lottery.id)).rejects.toHaveProperty(
    'problem.code',
    'save_failed'
  )
  const [unsaved] = await backend.conversations.listLotteries(chatId)
  expect(unsaved.result).not.toBeNull()
  expect(agent.run).toHaveBeenCalledTimes(1)
  save.mockRestore()
  await backend.conversations.retrySave(chatId)
  const recovered = await backend.conversations.drawLottery(chatId, lottery.id)
  expect(recovered.result).toEqual(unsaved.result)
  await until(() => agent.run.mock.calls.length === 2)
  await idle(backend, chatId)
  expect((await backend.store.load(chatId)).chat.meta.lotteries[0].result).toEqual(unsaved.result)
})

test('服装工具只列可解析模型，保存选择并排除其他角色与无效资源', async () => {
  const outcomes = []
  const { backend, chatId } = await fixture(async (request, observer, signal) => {
    outcomes.push(await invoke(request, observer, signal, 'fetch_all_live2d_models', {}, 'list'))
    outcomes.push(
      await invoke(
        request,
        observer,
        signal,
        'change_character_live2d',
        { character: '角色', modelId: 'second' },
        'change'
      )
    )
    await expect(
      invoke(
        request,
        observer,
        signal,
        'change_character_live2d',
        { character: '角色', modelId: 'broken' },
        'invalid'
      )
    ).rejects.toHaveProperty('problem.code', 'model_unavailable')
    await expect(
      invoke(
        request,
        observer,
        signal,
        'change_character_live2d',
        { character: '其他', modelId: 'second' },
        'other'
      )
    ).rejects.toHaveProperty('problem.code', 'character_unavailable')
    return { status: 'completed', usage: null }
  })
  const valid = await backend.assets.importAsset(
    {
      kind: 'bytes',
      name: 'model.model3.json',
      mediaType: 'application/json',
      bytes: Buffer.from(JSON.stringify({ Version: 3, FileReferences: {} }))
    },
    'chat:test',
    'attachment'
  )
  const broken = await backend.assets.importAsset(
    {
      kind: 'bytes',
      name: 'broken.model3.json',
      mediaType: 'application/json',
      bytes: Buffer.from('invalid json')
    },
    'chat:test',
    'attachment'
  )
  const actor = await backend.characters.get('actor')
  actor.forms[0].models = [
    { id: 'first', name: '第一套', presentation: { model: valid.asset, mapping: null } },
    { id: 'second', name: '第二套', presentation: { model: valid.asset, mapping: null } },
    { id: 'broken', name: '损坏', presentation: { model: broken.asset, mapping: null } }
  ]
  actor.forms[0].defaultModelId = 'first'
  await backend.characters.save(actor)
  await backend.conversations.setTools(chatId, { enabled: ['change_character_live2d'] })
  await backend.conversations.createTurn(
    chatId,
    { text: '换服装', attachments: [] },
    { requestId: 'costume' }
  )
  await idle(backend, chatId)
  expect(outcomes).toHaveLength(2)
  expect(outcomes[0].value.characters[0]).toMatchObject({
    character: '角色',
    form: '角色'
  })
  expect(outcomes[0].value.characters[0]).not.toHaveProperty('characterId')
  expect(outcomes[0].value.characters[0]).not.toHaveProperty('formId')
  expect(outcomes[0].value.characters[0].models.map((model) => model.modelId)).toEqual([
    'first',
    'second'
  ])
  expect(outcomes[1].value).toEqual({ character: '角色', modelId: 'second', state: 'saved' })
  expect(
    (await backend.store.load(chatId)).chat.meta.settings.characters.actor.modelOverrides.default
  ).toBe('second')
})
