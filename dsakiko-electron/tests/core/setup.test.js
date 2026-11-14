import {
  mkdtemp,
  mkdir,
  writeFile,
  readFile,
  rm,
  stat,
  open,
  chmod,
  symlink
} from 'node:fs/promises'
import path from 'node:path'
import os from 'node:os'
import { createHash } from 'node:crypto'
import { afterEach, expect, test, vi } from 'vitest'
import { link } from 'node:fs/promises'
import { randomUUID } from 'node:crypto'
import * as tar from 'tar'
import { createCoreBackend } from '../../src/backend/create-core-backend.js'
import { downloadFile } from '../../src/backend/setup/download.js'
import { validateRelease } from '../../src/backend/setup/catalog.js'
import { scanLegacyDirectory } from '../../src/backend/setup/legacy.js'
import { prepareRuntime, extractArchive } from '../../src/backend/setup/installers.js'
import { writeJson } from '../../src/backend/core/support.js'
import { startHttpServer } from '../../src/server/http-server.js'
import { createBackendClient } from '../../src/renderer/src/backend/client.js'

const cleanups = []
vi.mock('node:fs/promises', async (importOriginal) => {
  const original = await importOriginal()
  return { ...original, link: vi.fn(original.link) }
})
afterEach(async () => {
  for (const cleanup of cleanups.splice(0).reverse()) await cleanup()
})
async function fixture(options = {}) {
  const root = await mkdtemp(path.join(os.tmpdir(), 'sakiko-setup-'))
  cleanups.push(() => rm(root, { recursive: true, force: true }))
  const roots = {
    appDataRoot: path.join(root, 'app'),
    resourceLibraryRoot: path.join(root, 'library'),
    cacheRoot: path.join(root, 'cache')
  }
  const backend = await createCoreBackend({ ...roots, scanReminders: false, ...options })
  cleanups.push(() => backend.close())
  return { root, roots, backend }
}
async function put(root, name, data) {
  const file = path.join(root, name)
  await mkdir(path.dirname(file), { recursive: true })
  await writeFile(file, data)
  return file
}
async function legacy(root) {
  const source = path.join(root, 'python')
  await put(source, 'live2d_related/anon/name.txt', '爱音')
  await put(source, 'live2d_related/anon/character_description.txt', '旧版自定义人设')
  await put(source, 'live2d_related/anon/live2D_model/3.model.json', '{}')
  await put(source, 'live2d_related/anon/live2D_model/model.moc', 'fixture')
  await put(
    source,
    'd_sakiko_config.json',
    JSON.stringify({
      llm_setting: {
        llm_api_provider: 'deepseek',
        llm_api_model: 'deepseek/deepseek-chat',
        llm_api_key: 'private-test-secret'
      }
    })
  )
  await put(
    source,
    'reference_audio/all_conversation.json',
    JSON.stringify({
      chat_list: [
        {
          chat_id: 'chat-1',
          name: '旧聊天',
          type: 1,
          prompt_config: { character_name: '爱音' },
          message_list: [
            { character_name: 'User', text: '你好' },
            {
              character_name: '爱音',
              text: 'こんにちは',
              translation: '你好',
              audio_path: '../reference_audio/missing.wav'
            }
          ]
        }
      ]
    })
  )
  return source
}
async function done(setup, id) {
  for (let attempt = 0; attempt < 500; attempt += 1) {
    const job = await setup.getJob(id)
    if (job.status !== 'running') return job
    await new Promise((resolve) => setTimeout(resolve, 10))
  }
  throw Error('资源任务未结束')
}

test('快速初始化幂等释放三个角色，语音保持可选，UP 凭据不进入公开状态', async () => {
  const { backend } = await fixture({ builtinConnection: { secret: 'builtin-test-secret' } })
  await backend.onboarding.getState()
  expect(await backend.characters.list()).toEqual([])
  expect(Object.keys((await backend.assets.state()).assets)).toHaveLength(0)
  const initial = await backend.onboarding.begin({ path: 'quick' })
  expect(initial.stage).toBe('optional-resources')
  expect(await backend.characters.list()).toHaveLength(3)
  expect((await backend.characters.get('sakiko')).forms.map((form) => form.id)).toEqual([
    'black',
    'white'
  ])
  expect((await backend.characters.get('anon')).forms[0].voice).toBeNull()
  const sakiko = await backend.characters.get('sakiko')
  expect(sakiko.defaultFormId).toBe('black')
  expect(sakiko.forms.find((form) => form.id === 'white').description).toContain('当前形态：白祥')
  expect(sakiko.forms.find((form) => form.id === 'black').description).toContain('当前形态：黑祥')
  for (const id of ['sakiko', 'anon', 'soyo']) {
    const character = await backend.characters.get(id)
    for (const form of character.forms) {
      const asset = form.models[0].presentation.model
      const filename = await backend.assets.localPath(asset)
      expect(filename).toMatch(/\.model3\.json$/)
      expect(filename).not.toContain('_mask')
      const capabilities = await backend.characters.resolveCapabilities(id, { formId: form.id })
      expect(capabilities.modelVersion).toBe('v3')
      expect(capabilities.problems).toEqual([])
    }
  }
  await backend.onboarding.finish()
  await backend.onboarding.begin({ path: 'quick' })
  expect(await backend.characters.list()).toHaveLength(3)
  expect(Object.keys((await backend.assets.state()).assets)).toHaveLength(4)
  expect(JSON.stringify(await backend.onboarding.getState())).not.toContain('builtin-test-secret')
  expect((await backend.settings.get()).generation.connectionId).toBe('builtin-up')
  const notice = (await backend.notices.list()).notices[0]
  expect(notice).toMatchObject({ kind: 'connection', data: { connectionState: 'builtin' } })
  expect((await backend.notices.dismiss(notice.id)).notices).toEqual([])
}, 15000)

test('内置形态元数据不依赖排序，旧清单仍沿用首形态和共用人设', async () => {
  const { root, backend } = await fixture()
  const directory = path.join(root, 'builtin')
  await put(directory, 'model/model.model3.json', JSON.stringify({ Version: 3 }))
  const model = { directory: 'model', entry: 'model.model3.json' }
  await put(
    directory,
    'manifest.json',
    JSON.stringify({
      version: 1,
      characters: [
        {
          id: 'forms',
          name: '双形态',
          description: '共用描述',
          themeColor: null,
          defaultFormId: 'black',
          previewFormId: 'white',
          forms: [
            {
              id: 'white',
              ...model,
              displayName: '白祥',
              description: '白祥语气',
              modelName: '常服'
            },
            { id: 'black', ...model, description: '黑祥语气' }
          ]
        },
        {
          id: 'old',
          name: '旧格式',
          description: '旧人设',
          themeColor: null,
          forms: [{ id: 'first', ...model }]
        }
      ]
    })
  )
  backend.onboarding.builtinDirectory = directory
  await backend.onboarding.begin({ path: 'quick' })
  const character = await backend.characters.get('forms')
  expect(character.defaultFormId).toBe('black')
  expect(character.forms[0]).toMatchObject({ displayName: '白祥', description: '白祥语气' })
  expect(character.forms[0].models[0].name).toBe('常服')
  expect(character.forms[1]).toMatchObject({ displayName: '双形态', description: '黑祥语气' })
  const old = await backend.characters.get('old')
  expect(old.defaultFormId).toBe('first')
  expect(old.forms[0]).toMatchObject({ displayName: '旧格式', description: '旧人设' })
  expect(old.forms[0].models[0].name).toBe('默认')
})

test('欢迎页预览允许读取同一模型的相对资源且不写入角色目录', async () => {
  const { backend } = await fixture()
  const server = await startHttpServer(backend)
  cleanups.push(() => server.close())
  const client = createBackendClient(server)
  cleanups.push(() => client.close())
  const preview = await client.onboarding.getBuiltinPreview('sakiko')
  expect(preview).toMatchObject({ characterId: 'sakiko', formId: 'white' })
  const model = await fetch(preview.url)
  expect(model.headers.get('content-type')).toBe('application/json')
  const definition = await model.json()
  expect((await fetch(new URL(definition.FileReferences.Moc, preview.url))).ok).toBe(true)
  expect((await fetch(new URL('../unknown', preview.url))).ok).toBe(false)
  expect(await backend.characters.list()).toEqual([])
})

test('旧版扫描、导入、重启重试不重复角色和聊天，密钥只留私有配置', async () => {
  const { root, roots, backend } = await fixture()
  const source = await legacy(root)
  const grant = await backend.setup.grantLegacySource(source)
  const scan = await backend.setup.scanLegacy(grant.sourceId)
  expect(scan.chatCount).toBe(1)
  expect(JSON.stringify(scan)).not.toContain('private-test-secret')
  const plan = await backend.setup.createPlan({ kind: 'legacy', scanId: scan.scanId })
  await backend.onboarding.begin({ path: 'legacy', planId: plan.id })
  expect(await done(backend.setup, plan.id)).toMatchObject({ status: 'succeeded' })
  await backend.onboarding.finish()
  expect((await backend.characters.get('anon')).forms[0].description).toBe('旧版自定义人设')
  expect(await backend.characters.list()).toHaveLength(3)
  expect(await backend.store.list()).toHaveLength(1)
  const chat = await backend.store.load((await backend.store.list())[0].id)
  expect(chat.chat.turns[0].messages[1].parts[0].lines[0].text).toBe('こんにちは')
  expect((await backend.settings.get()).generation.connectionId).toMatch(/^legacy-/)
  expect((await backend.notices.list()).notices).toEqual([])
  await backend.close()
  const resumed = await createCoreBackend({ ...roots, scanReminders: false })
  cleanups.push(() => resumed.close())
  const again = await resumed.setup.scanLegacy(
    (await resumed.setup.grantLegacySource(source)).sourceId
  )
  const next = await resumed.setup.createPlan({ kind: 'legacy', scanId: again.scanId })
  await resumed.setup.start(next.id)
  expect(await done(resumed.setup, next.id)).toMatchObject({ status: 'succeeded' })
  expect(await resumed.store.list()).toHaveLength(1)
  expect((await resumed.characters.get('anon')).revision).toBe(1)
}, 15000)

test('同名角色分别保留身份，旧聊天优先使用存档中的目录身份', async () => {
  const { root, backend } = await fixture()
  const source = await legacy(root)
  await put(source, 'live2d_related/anon-copy/name.txt', '爱音')
  await put(source, 'live2d_related/anon-copy/character_description.txt', '另一份人设')
  const chat = (id, names, meta = {}) => ({
    chat_id: id,
    name: id,
    type: names.length === 2 ? 2 : 1,
    prompt_config: { character_names: names },
    meta,
    message_list: [{ character_name: '爱音', text: '原台词' }]
  })
  await put(
    source,
    'reference_audio/all_conversation.json',
    JSON.stringify({
      chat_list: [
        chat('explicit', ['爱音'], { character_forms: { anon: 'default' } }),
        chat('name-only', ['爱音']),
        chat('theater', ['爱音', '爱音'])
      ]
    })
  )
  const scan = await backend.setup.scanLegacy(
    (await backend.setup.grantLegacySource(source)).sourceId
  )
  expect(scan).toMatchObject({ canImport: true, connectionCount: 1, pendingConnectionCount: 0 })
  const importOnce = async () => {
    const plan = await backend.setup.createPlan({ kind: 'legacy', scanId: scan.scanId })
    await backend.setup.start(plan.id)
    expect(await done(backend.setup, plan.id)).toMatchObject({ status: 'succeeded' })
  }
  await importOnce()
  const renamed = await backend.characters.get('anon-copy')
  expect(renamed).toMatchObject({ name: '爱音_1', forms: [{ description: '另一份人设' }] })
  const summaries = await backend.conversations.listChats()
  expect(summaries.find((item) => item.title === 'explicit').characterIds).toEqual(['anon'])
  expect(summaries.find((item) => item.title === 'name-only').characterIds).toEqual(['anon-copy'])
  const theater = summaries.find((item) => item.title === 'theater')
  const history = await backend.conversations.getSnapshot(theater.id)
  expect(history.participantIssue.code).toBe('ambiguous-theater-characters')
  await expect(
    backend.conversations.createTurn(
      theater.id,
      { text: '继续', attachments: [] },
      { requestId: 'ambiguous' }
    )
  ).rejects.toHaveProperty('problem.code', 'ambiguous_theater_characters')
  const replay = await backend.conversations.getReplayMaterials(theater.id, {
    kind: 'turn',
    turnId: history.chat.turns[0].id
  })
  expect(replay.guides[0].text).toBe('原台词')
  await backend.characters.save({ ...renamed, name: '我的爱音' })
  await importOnce()
  expect(await backend.characters.get('anon-copy')).toMatchObject({ name: '我的爱音', revision: 2 })
  expect(await backend.store.list()).toHaveLength(3)
})

test.each([false, true])('跳过失败导入按落盘数据继续，已保存聊天=%s', async (saved) => {
  const { root, backend } = await fixture({ builtinConnection: { secret: 'builtin-test-secret' } })
  const source = await legacy(root)
  const scan = await backend.setup.scanLegacy(
    (await backend.setup.grantLegacySource(source)).sourceId
  )
  const plan = await backend.setup.createPlan({
    kind: 'legacy',
    scanId: scan.scanId,
    include: { chats: true }
  })
  const save = backend.store.save.bind(backend.store)
  const intercept = vi.spyOn(backend.store, 'save').mockImplementationOnce(async (chat) => {
    if (saved) await save(chat)
    throw new Error('模拟写入结果前中断')
  })
  await backend.onboarding.begin({ path: 'legacy', planId: plan.id })
  expect(await done(backend.setup, plan.id)).toMatchObject({ status: 'failed', results: [] })
  intercept.mockRestore()
  const next = await backend.onboarding.skipLegacyImport()
  expect(next).toMatchObject({ path: saved ? 'legacy' : 'quick', stage: 'optional-resources' })
  expect(await backend.onboarding.skipLegacyImport()).toEqual(next)
  expect(await backend.store.list()).toHaveLength(Number(saved))
  expect((await backend.onboarding.finish()).initialized).toBe(true)
  expect((await backend.settings.get()).generation.connectionId).toBe(
    saved ? undefined : 'builtin-up'
  )
})

test('计划实际保留硬链接、空间扣除大权重，取消不更改源权限或内容', async () => {
  const { root, backend } = await fixture()
  const source = await legacy(root)
  const file = await put(source, 'GPT_SoVITS/pretrained_models/large.pth', '')
  const handle = await open(file, 'r+')
  await handle.truncate(32 * 1024 * 1024)
  await handle.close()
  await chmod(file, 0o640)
  const before = await stat(file)
  const scan = await backend.setup.scanLegacy(
    (await backend.setup.grantLegacySource(source)).sourceId
  )
  const plan = await backend.setup.createPlan({ kind: 'legacy', scanId: scan.scanId })
  expect(plan.transfer.hardlinkFiles).toBe(1)
  expect(plan.space.logical - plan.space.added).toBe(32 * 1024 * 1024)
  expect((await stat(file)).nlink).toBe(before.nlink + 1)
  await backend.setup.controlJob(plan.id, 'cancel')
  const after = await stat(file)
  expect(after.mode).toBe(before.mode)
  expect(after.nlink).toBe(before.nlink)
  expect(after.size).toBe(before.size)
})

test('确认后源文件改变使计划失败，损坏与更早格式都给出具体诊断', async () => {
  const { root, backend } = await fixture()
  const source = await legacy(root)
  const scan = await backend.setup.scanLegacy(
    (await backend.setup.grantLegacySource(source)).sourceId
  )
  const plan = await backend.setup.createPlan({ kind: 'legacy', scanId: scan.scanId })
  await put(source, 'live2d_related/anon/character_description.txt', '修改了')
  await backend.setup.start(plan.id)
  expect(await done(backend.setup, plan.id)).toMatchObject({
    status: 'failed',
    problem: { code: 'legacy_source_changed' }
  })
  await put(source, 'd_sakiko_config.json', '{invalid')
  expect(
    (await scanLegacyDirectory(source)).warnings.some(
      (item) => item.code === 'legacy_document_invalid'
    )
  ).toBe(true)
  const old = path.join(root, 'older')
  await put(old, 'dsakiko_config.json', '{}')
  expect(
    (await scanLegacyDirectory(old)).warnings.some(
      (item) => item.code === 'legacy_upgrade_required'
    )
  ).toBe(true)
})

test('分组只展示五个选择，运行包未发布时不能伪造整组安装成功', async () => {
  const { backend } = await fixture({ setupOptions: { platform: 'darwin', arch: 'arm64' } })
  const catalog = await backend.setup.getCatalog()
  expect(catalog.groups).toHaveLength(3)
  expect(catalog.groups.flatMap((group) => group.options).map((item) => item.id)).toEqual([
    'asr',
    'gsv',
    'voice.anon',
    'voice.soyo',
    'voice.sakiko'
  ])
  expect(catalog.groups[0].options[0].available).toBe(false)
  await expect(
    backend.setup.createPlan({ kind: 'download', selection: ['asr'] })
  ).rejects.toMatchObject({ problem: { code: 'runtime_not_published' } })
  await expect(
    backend.setup.createPlan({ kind: 'download', selection: ['asr.models'] })
  ).rejects.toMatchObject({ problem: { code: 'invalid_resource_selection' } })
})

test('角色语音容量排除公共引擎，多选总量仍包含依赖且只计一次', async () => {
  const { backend } = await fixture({
    setupOptions: {
      platform: 'darwin',
      arch: 'arm64',
      runtimes: {
        'gsv.darwin-arm64-cpu': {
          available: true,
          version: 'test',
          files: [{ source: 'runtimes', path: 'gsv.zip', sizeBytes: 123, sha256: 'a'.repeat(64) }]
        }
      }
    }
  })
  await backend.onboarding.begin({ path: 'quick' })
  const components = await backend.setup.catalog.components()
  const voiceBytes = (id) =>
    components.find((item) => item.id === id).files.reduce((sum, file) => sum + file.sizeBytes, 0)
  const anonBytes = voiceBytes('voice.anon')
  const soyoBytes = voiceBytes('voice.soyo')
  const catalog = await backend.setup.getCatalog({ selection: ['gsv', 'voice.anon', 'voice.soyo'] })
  const options = catalog.groups.flatMap((group) => group.options)
  const gsvBytes = options.find((item) => item.id === 'gsv').downloadBytes
  expect(anonBytes).toBeGreaterThan(0)
  expect(soyoBytes).toBeGreaterThan(0)
  expect(gsvBytes).toBeGreaterThan(123)
  expect(options.find((item) => item.id === 'voice.anon')).toMatchObject({
    downloadBytes: anonBytes,
    sizeKnown: true,
    requiresSelection: 'gsv'
  })
  expect(options.find((item) => item.id === 'voice.soyo').downloadBytes).toBe(soyoBytes)
  expect(catalog.selectionDownloadBytes).toBe(gsvBytes + anonBytes + soyoBytes)
  const single = await backend.setup.getCatalog({ selection: ['voice.anon'] })
  expect(single.selectionDownloadBytes).toBe(gsvBytes + anonBytes)
})

test('固定来源下载断点续传，哈希不符删除分片，拒绝任意重定向', async () => {
  const { root } = await fixture()
  const bytes = Buffer.from('verified payload')
  const sha256 = createHash('sha256').update(bytes).digest('hex')
  const file = { source: 'asr', path: 'config.json', sizeBytes: bytes.length, sha256 }
  const directory = path.join(root, 'download')
  await put(directory, sha256 + '.part', bytes.subarray(0, 4))
  const output = await downloadFile(file, directory, {
    fetch: async (url, options) => {
      expect(url).toBe(
        'https://www.modelscope.cn/datasets/Jerry4787/custom-gsv-model/resolve/master/asr-whisper-small-q8/config.json'
      )
      expect(options.headers.Range).toBe('bytes=4-')
      return new Response(bytes.subarray(4), {
        status: 206,
        headers: { 'content-range': `bytes 4-${bytes.length - 1}/${bytes.length}` }
      })
    }
  })
  expect(await readFile(output)).toEqual(bytes)
  await rm(output)
  await expect(
    downloadFile(file, directory, { fetch: async () => new Response(Buffer.alloc(bytes.length)) })
  ).rejects.toMatchObject({ problem: { code: 'resource_hash_mismatch' } })
  await expect(stat(path.join(directory, sha256 + '.part'))).rejects.toMatchObject({
    code: 'ENOENT'
  })
  await expect(
    downloadFile(file, directory, {
      fetch: async () =>
        new Response(null, { status: 302, headers: { location: 'https://untrusted.example/file' } })
    })
  ).rejects.toMatchObject({ problem: { code: 'resource_redirect_denied' } })
})

test('清单拒绝跨平台路径穿越和大小写重名，来源扫描不跟随符号链接', async () => {
  for (const name of ['../secret', 'C:/data', 'name:stream', 'NUL', 'dir/file.'])
    expect(() =>
      validateRelease({
        schemaVersion: 1,
        version: '1',
        files: [{ path: name, sizeBytes: 1, sha256: '0'.repeat(64) }]
      })
    ).toThrow()
  const { root } = await fixture()
  const source = await legacy(root)
  await symlink(
    path.join(root, 'app'),
    path.join(source, 'live2d_related/anon/live2D_model/escape')
  )
  await expect(scanLegacyDirectory(source)).rejects.toMatchObject({
    problem: { code: 'source_symlink' }
  })
})

test('GSV 环境只使用固定离线命令和最终路径，不使用系统 Python', async () => {
  const { root } = await fixture()
  const directory = path.join(root, '环境 中文')
  for (const name of [
    'python/bin/python3',
    'uv/uv',
    'bin/ffmpeg',
    'requirements.lock',
    'wheelhouse/model.whl'
  ])
    await put(directory, name, 'fixture')
  const commands = []
  const result = await prepareRuntime(
    { id: 'gsv.runtime', target: 'darwin-arm64-cpu' },
    directory,
    undefined,
    async (executable, args, options) => {
      commands.push({ executable, args, options })
      if (args[0] === 'venv') await put(directory, 'env/bin/python', 'fixture')
    }
  )
  expect(result.pythonExecutable).toBe(path.join(directory, 'env/bin/python'))
  expect(commands[1].args).toContain('--require-hashes')
  expect(commands[1].args).toContain('--no-index')
  expect(commands[1].options.env.UV_OFFLINE).toBe('1')
})

test('下载暂停跨重启继续，提交中断再次执行不删除已登记文件', async () => {
  const payload = Buffer.from('{}')
  const sha256 = createHash('sha256').update(payload).digest('hex')
  const component = {
    id: 'asr.models',
    kind: 'asr',
    version: 'test',
    available: true,
    requires: [],
    files: [
      'config.json',
      'tokenizer.json',
      'tokenizer_config.json',
      'preprocessor_config.json',
      'generation_config.json',
      'onnx/encoder_model_quantized.onnx',
      'onnx/decoder_model_merged_quantized.onnx'
    ].map((name) => ({ source: 'asr', path: name, sizeBytes: payload.length, sha256 }))
  }
  const catalog = { select: async () => [component] }
  let began
  const started = new Promise((resolve) => {
    began = resolve
  })
  const fetch = async (url, options) => {
    began()
    return new Promise((resolve, reject) =>
      options.signal.addEventListener('abort', () => reject(options.signal.reason), { once: true })
    )
  }
  const { roots, backend } = await fixture({ setupOptions: { catalog }, fetch })
  const plan = await backend.setup.createPlan({ kind: 'download', selection: ['asr'] })
  await backend.setup.start(plan.id)
  await started
  expect((await backend.setup.controlJob(plan.id, 'pause')).status).toBe('paused')
  await backend.close()
  let downloads = 0
  const next = await createCoreBackend({
    ...roots,
    scanReminders: false,
    setupOptions: { catalog },
    fetch: async () => {
      downloads += 1
      return new Response(payload)
    }
  })
  cleanups.push(() => next.close())
  const activate = next.setup.activate.bind(next.setup)
  next.setup.activate = async () => {
    throw Error('模拟启用记录写入失败')
  }
  await next.setup.controlJob(plan.id, 'resume')
  expect((await done(next.setup, plan.id)).status).toBe('failed')
  const prepared = await next.setup.readJob(plan.id)
  const modelRef = prepared.prepared['asr.models'].refs['config.json']
  const modelPath = await next.assets.localPath(modelRef)
  next.setup.activate = activate
  await next.setup.controlJob(plan.id, 'resume')
  expect((await done(next.setup, plan.id)).status).toBe('succeeded')
  expect(await next.assets.localPath(modelRef)).toBe(modelPath)
  expect(await readFile(modelPath)).toEqual(payload)
  expect(downloads).toBe(1)
  const job = await next.setup.readJob(plan.id)
  job.status = 'running'
  await writeJson(next.setup.jobFile(job.id), job)
  await next.close()
  const restored = await createCoreBackend({
    ...roots,
    scanReminders: false,
    setupOptions: { catalog }
  })
  cleanups.push(() => restored.close())
  expect((await restored.setup.getJob(job.id)).status).toBe('paused')
  await restored.setup.start(job.id)
  expect((await done(restored.setup, job.id)).status).toBe('succeeded')
})

test('跨卷硬链接失败在计划阶段降级为复制，执行后保持源文件权限', async () => {
  const { root, backend } = await fixture()
  const source = await legacy(root)
  const file = await put(source, 'GPT_SoVITS/pretrained_models/large.pth', '')
  const handle = await open(file, 'r+')
  await handle.truncate(32 * 1024 * 1024)
  await handle.close()
  await chmod(file, 0o640)
  link.mockRejectedValueOnce(Object.assign(new Error('cross device'), { code: 'EXDEV' }))
  const before = await stat(file)
  const scan = await backend.setup.scanLegacy(
    (await backend.setup.grantLegacySource(source)).sourceId
  )
  const plan = await backend.setup.createPlan({
    kind: 'legacy',
    scanId: scan.scanId,
    include: { gsv: true }
  })
  expect(plan.transfer.hardlinkFiles).toBe(0)
  expect(plan.space.added).toBe(32 * 1024 * 1024)
  await backend.setup.start(plan.id)
  expect((await done(backend.setup, plan.id)).status).toBe('succeeded')
  expect((await stat(file)).mode).toBe(before.mode)
  const next = await backend.setup.createPlan({
    kind: 'legacy',
    scanId: scan.scanId,
    include: { gsv: true }
  })
  expect(next.space.added).toBe(0)
  expect(next.transfer.reusedFiles).toBe(1)
})

test('运行环境切换后进程租约保护旧目录，只有确认退出才回收', async () => {
  const { backend, roots } = await fixture()
  const oldDirectory = path.join(roots.resourceLibraryRoot, 'runtimes', randomUUID(), 'asr.runtime')
  const newDirectory = path.join(roots.resourceLibraryRoot, 'runtimes', randomUUID(), 'asr.runtime')
  await put(oldDirectory, 'entry.mjs', 'export {}')
  await put(newDirectory, 'entry.mjs', 'export {}')
  await backend.setup.activate({
    id: 'asr.models',
    status: 'ready',
    directory: roots.resourceLibraryRoot,
    refs: {}
  })
  await backend.setup.activate({
    id: 'asr.runtime',
    status: 'ready',
    directory: oldDirectory,
    runtime: { entry: path.join(oldDirectory, 'entry.mjs') },
    refs: {}
  })
  const runtime = await backend.setup.acquireRuntime('asr')
  await runtime.bindResources(process.pid)
  await backend.setup.activate({
    id: 'asr.runtime',
    status: 'ready',
    directory: newDirectory,
    runtime: { entry: path.join(newDirectory, 'entry.mjs') },
    refs: {}
  })
  await backend.setup.maintainRuntimes()
  expect((await stat(oldDirectory)).isDirectory()).toBe(true)
  await runtime.releaseResources()
  expect((await stat(oldDirectory)).isDirectory()).toBe(true)
  await runtime.releaseResources(process.pid)
  await expect(stat(oldDirectory)).rejects.toMatchObject({ code: 'ENOENT' })
  expect((await stat(newDirectory)).isDirectory()).toBe(true)
})

/** 构造无压缩 ZIP 夹具，直接覆盖跨平台路径、链接和解包上限行为。 */
function zipFixture(entries) {
  let offset = 0
  const bodies = []
  const directories = []
  for (const item of entries) {
    const name = Buffer.from(item.name)
    const data = Buffer.from(item.text ?? 'fixture')
    let crc = 0xffffffff
    for (const byte of data) {
      crc ^= byte
      for (let bit = 0; bit < 8; bit += 1) crc = (crc >>> 1) ^ (crc & 1 ? 0xedb88320 : 0)
    }
    crc = (crc ^ 0xffffffff) >>> 0
    const header = Buffer.alloc(30)
    header.writeUInt32LE(0x04034b50)
    header.writeUInt16LE(20, 4)
    header.writeUInt32LE(crc, 14)
    header.writeUInt32LE(data.length, 18)
    header.writeUInt32LE(data.length, 22)
    header.writeUInt16LE(name.length, 26)
    const central = Buffer.alloc(46)
    central.writeUInt32LE(0x02014b50)
    central.writeUInt16LE(0x0314, 4)
    central.writeUInt16LE(20, 6)
    central.writeUInt32LE(crc, 16)
    central.writeUInt32LE(data.length, 20)
    central.writeUInt32LE(data.length, 24)
    central.writeUInt16LE(name.length, 28)
    central.writeUInt32LE(((item.mode ?? 0o100600) << 16) >>> 0, 38)
    central.writeUInt32LE(offset, 42)
    bodies.push(header, name, data)
    directories.push(central, name)
    offset += header.length + name.length + data.length
  }
  const directory = Buffer.concat(directories)
  const end = Buffer.alloc(22)
  end.writeUInt32LE(0x06054b50)
  end.writeUInt16LE(entries.length, 8)
  end.writeUInt16LE(entries.length, 10)
  end.writeUInt32LE(directory.length, 12)
  end.writeUInt32LE(offset, 16)
  return Buffer.concat([...bodies, directory, end])
}

test('ZIP 解包保留目录结构并拒绝越界、符号链接和超额内容', async () => {
  const { root } = await fixture()
  const archive = await put(
    root,
    'good.zip',
    zipFixture([{ name: 'data/file.txt', text: 'known' }])
  )
  await extractArchive(archive, path.join(root, 'good'), 'zip', 5)
  expect(await readFile(path.join(root, 'good/data/file.txt'), 'utf8')).toBe('known')
  for (const [index, entries] of [
    [{ name: '../escape' }],
    [{ name: 'link', mode: 0o120777 }],
    [{ name: 'Case' }, { name: 'case' }]
  ].entries()) {
    const invalid = await put(root, `bad-${index}.zip`, zipFixture(entries))
    await expect(
      extractArchive(invalid, path.join(root, 'bad-' + index), 'zip', 100)
    ).rejects.toThrow()
  }
  await expect(extractArchive(archive, path.join(root, 'limited'), 'zip', 2)).rejects.toMatchObject(
    { problem: { code: 'archive_too_large' } }
  )
})

test('tar 字典包支持普通目录，拒绝链接时返回任务错误而不产生未处理异常', async () => {
  const { root } = await fixture()
  const source = path.join(root, 'tar-source')
  await put(source, 'dictionary/sys.dic', 'dictionary')
  const good = path.join(root, 'good.tar.gz')
  await tar.c({ file: good, gzip: true, cwd: source }, ['dictionary'])
  await extractArchive(good, path.join(root, 'unpacked'), 'tar.gz', 100)
  expect(await readFile(path.join(root, 'unpacked/dictionary/sys.dic'), 'utf8')).toBe('dictionary')
  await symlink('dictionary/sys.dic', path.join(source, 'link'))
  const bad = path.join(root, 'bad.tar.gz')
  await tar.c({ file: bad, gzip: true, cwd: source }, ['link'])
  await expect(
    extractArchive(bad, path.join(root, 'rejected'), 'tar.gz', 100)
  ).rejects.toMatchObject({ problem: { code: 'archive_link_denied' } })
})
