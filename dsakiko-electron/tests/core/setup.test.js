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
import { chatSettings } from '../../src/backend/core/defaults.js'
import { startHttpServer } from '../../src/server/http-server.js'
import { createBackendClient } from '../../src/renderer/src/backend/client.js'
import { matchBangDreamCharacters } from '../../src/backend/characters/bang-dream.js'
import { OutfitSources } from '../../src/backend/live2d-import/sources.js'

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

test('快速开始延迟到最终提交才幂等释放三个角色，语音保持可选，UP 凭据不进入公开状态', async () => {
  const { backend } = await fixture({ builtinConnection: { secret: 'builtin-test-secret' } })
  await backend.onboarding.getState()
  expect(await backend.characters.list()).toEqual([])
  expect(Object.keys((await backend.assets.state()).assets)).toHaveLength(0)
  const initial = await backend.onboarding.begin({ path: 'quick' })
  expect(initial.stage).toBe('optional-resources')
  expect(await backend.characters.list()).toEqual([])
  expect(await backend.settings.listConnections()).toEqual([])
  await backend.onboarding.finish()
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
  await backend.onboarding.finish()
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

test('按旧版来源固定导入对话的 LLM，补填仅接受有任务记录且未选择的聊天', async () => {
  const { root, roots, backend } = await fixture()
  const source = await legacy(root)
  const file = 'reference_audio/all_conversation.json'
  const {
    chat_list: [original]
  } = JSON.parse(await readFile(path.join(source, file), 'utf8'))
  const ids = ['legacy-unset', 'explicit', 'reset', 'retargeted']
  await put(
    source,
    file,
    JSON.stringify({
      chat_list: [
        ...ids.map((id) => ({ ...original, chat_id: id, name: id })),
        {
          id: 'timeline',
          title: 'timeline',
          mode: 'single-character',
          characterIds: ['anon'],
          userPersona: null,
          meta: { settings: chatSettings(), extensions: {} },
          turns: []
        }
      ]
    })
  )
  const manual = await backend.settings.saveConnection(
    {
      id: 'manual',
      revision: 0,
      name: '同名模型的另一连接',
      provider: 'deepseek',
      protocol: 'chat-completions',
      baseUrl: 'http://localhost:9999/v1',
      models: [{ id: 'deepseek-chat', declared: {}, overrides: {} }]
    },
    { kind: 'replace', secret: 'fixture' }
  )
  const scan = await backend.setup.scanLegacy(
    (await backend.setup.grantLegacySource(source)).sourceId
  )
  const plan = await backend.setup.createPlan({ kind: 'legacy', scanId: scan.scanId })
  await backend.setup.start(plan.id)
  expect(await done(backend.setup, plan.id)).toMatchObject({ status: 'succeeded' })
  const saved = await backend.settings.listConnections()
  const migrated = saved.find((item) => item.id.startsWith('legacy-'))
  const selection = { connectionId: migrated.id, modelId: 'deepseek-chat' }
  const other = { connectionId: manual.id, modelId: selection.modelId }
  const chats = Object.fromEntries(
    (await backend.store.list()).map((item) => [item.title, item.id])
  )
  for (const id of Object.values(chats)) {
    const { chat } = await backend.store.load(id)
    expect(chat.meta.settings.generation).toEqual(selection)
    expect(chat.meta.extensions.modelSelectionVersion).toBe(1)
  }

  // 模拟旧版 Electron 已导入、只继承全局设置的存档。
  for (const title of ids) {
    const { chat } = await backend.store.load(chats[title])
    chat.meta.settings.generation = title === 'explicit' ? other : {}
    if (title !== 'reset') delete chat.meta.extensions.modelSelectionVersion
    await backend.store.save(chat)
    if (title === 'legacy-unset')
      await backend.store.save({ ...chat, id: 'without-import-receipt', revision: 0 })
  }
  await backend.settings.update({ generation: other })
  await backend.close()
  const resumed = await createCoreBackend({ ...roots, scanReminders: false })
  cleanups.push(() => resumed.close())
  const readSelection = async (id) =>
    (await resumed.conversations.getSnapshot(id)).chat.meta.settings.generation
  expect(await readSelection(chats['legacy-unset'])).toEqual(selection)
  expect((await resumed.store.load(chats['legacy-unset'])).chat.meta.settings.generation).toEqual(
    selection
  )
  expect(await readSelection(chats.explicit)).toEqual(other)
  expect(await readSelection(chats.reset)).toEqual({})
  expect(await readSelection('without-import-receipt')).toEqual({})
  await expect(
    resumed.conversations.createTurn(
      'without-import-receipt',
      { text: '继续', attachments: [] },
      { requestId: 'no-import-model' }
    )
  ).rejects.toHaveProperty('problem.code', 'model_unavailable')
  await resumed.settings.saveConnection(
    { ...migrated, baseUrl: 'http://localhost:8888/v1' },
    { kind: 'remove' }
  )
  expect(await readSelection(chats.retargeted)).toEqual({})
})

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
  const file = await put(source, 'GPT_SoVITS/pretrained_models/gsv-v4-pretrained/s2Gv4.pth', '')
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
  expect(commands[1].options.env.UV_CACHE_DIR).toBe(path.join(directory, '.uv-cache'))
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
  const cached = path.join(roots.cacheRoot, 'setup', sha256)
  expect(await readFile(cached)).toEqual(payload)
  const prepared = await next.setup.readJob(plan.id)
  const modelRef = prepared.prepared['asr.models'].refs['config.json']
  const modelPath = await next.assets.localPath(modelRef)
  next.setup.activate = activate
  await next.setup.controlJob(plan.id, 'resume')
  expect((await done(next.setup, plan.id)).status).toBe('succeeded')
  expect(await next.assets.localPath(modelRef)).toBe(modelPath)
  expect(await readFile(modelPath)).toEqual(payload)
  expect(downloads).toBe(1)
  await vi.waitFor(async () => {
    await expect(stat(cached)).rejects.toMatchObject({ code: 'ENOENT' })
  })
  const job = await next.setup.readJob(plan.id)
  job.status = 'running'
  await writeJson(next.setup.jobFile(job.id), job)
  await next.close()
  await writeFile(cached, payload)
  const restored = await createCoreBackend({
    ...roots,
    scanReminders: false,
    setupOptions: { catalog }
  })
  cleanups.push(() => restored.close())
  // 中断任务仍可恢复，启动回收不能误删它的下载材料。
  expect(await readFile(cached)).toEqual(payload)
  expect((await restored.setup.getJob(job.id)).status).toBe('paused')
  await restored.setup.start(job.id)
  expect((await done(restored.setup, job.id)).status).toBe('succeeded')
  await vi.waitFor(async () => {
    await expect(stat(cached)).rejects.toMatchObject({ code: 'ENOENT' })
  })
})

test('跨卷硬链接失败在计划阶段降级为复制，执行后保持源文件权限', async () => {
  const { root, backend } = await fixture()
  const source = await legacy(root)
  const file = await put(source, 'GPT_SoVITS/pretrained_models/gsv-v4-pretrained/s2Gv4.pth', '')
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

test('邦角色匹配优先精确 ID，别名只接受唯一候选并保留手动归属', () => {
  const roles = [
    { id: 'sakiko', name: '改过名字' },
    { id: 'extra-sakiko', name: '豊川祥子' },
    { id: 'custom-soyo', name: '長崎そよ' },
    { id: 'first-anon', name: '千早爱音' },
    { id: 'second-anon', name: '愛音' },
    { id: 'uika', name: '初华', bangDreamId: null }
  ]
  expect([...matchBangDreamCharacters(roles)]).toEqual([
    ['sakiko', 'sakiko'],
    ['custom-soyo', 'soyo']
  ])
})

test('同 ID 的旧版内置角色追加服装并保留 Electron 默认与已有配置', async () => {
  const { root, backend } = await fixture()
  await backend.onboarding.begin({ path: 'quick' })
  await backend.onboarding.finish()
  const before = await backend.characters.get('anon')
  const source = await legacy(root)
  const scan = await backend.setup.scanLegacy(
    (await backend.setup.grantLegacySource(source)).sourceId
  )
  const plan = await backend.setup.createPlan({ kind: 'legacy', scanId: scan.scanId })
  await backend.setup.start(plan.id)
  expect(await done(backend.setup, plan.id)).toMatchObject({ status: 'succeeded' })
  const roles = await backend.characters.list()
  expect(roles.filter((role) => role.bangDreamId === 'anon')).toHaveLength(1)
  const after = await backend.characters.get('anon')
  expect(after.forms[0].defaultModelId).toBe(before.forms[0].defaultModelId)
  expect(after.forms[0].description).toBe(before.forms[0].description)
  expect(after.forms[0].models).toHaveLength(before.forms[0].models.length + 1)
  expect((await backend.conversations.listChats())[0].characterIds).toEqual(['anon'])
})

async function outfitRole(backend) {
  await backend.characters.save({
    id: 'local-sakiko',
    name: '测试祥子',
    revision: 0,
    defaultFormId: 'stage',
    forms: [
      {
        id: 'stage',
        displayName: '舞台',
        description: '测试人设',
        avatar: null,
        voice: null,
        models: [],
        defaultModelId: null
      }
    ],
    knowledgeMappings: [],
    extensions: {}
  })
  await backend.outfits.setCharacter('local-sakiko', 'sakiko')
  return { characterId: 'local-sakiko', formId: 'stage' }
}

test('文件夹安装、默认固定和删除交接在正式对话存档中生效', async () => {
  const { root, backend } = await fixture()
  const target = await outfitRole(backend)
  const options = { title: '对话', characterIds: [target.characterId], mode: 'single-character' }
  const first = await backend.conversations.createChat(options)
  const inactive = await backend.conversations.createChat(options)
  const firstFolder = path.join(root, 'first-model')
  await put(
    firstFolder,
    'model.json',
    JSON.stringify({ model: 'body.moc', textures: ['body.png'] })
  )
  await put(firstFolder, 'body.moc', 'first model')
  await put(firstFolder, 'body.png', 'test texture')
  const { modelId: firstId } = await backend.outfits.importDirectory(target, firstFolder)
  const read = async (id) => {
    const { chat } = await backend.store.load(id)
    return chat.meta.settings.characters[target.characterId].modelOverrides.stage
  }
  expect(await read(first)).toBe(firstId)
  expect(await read(inactive)).toBe(firstId)
  expect(await backend.outfits.importDirectory(target, firstFolder)).toEqual({
    modelId: firstId,
    alreadyInstalled: true
  })
  await expect(backend.outfits.previewRemoval(target, firstId)).rejects.toMatchObject({
    problem: { code: 'default_outfit_in_use' }
  })

  const secondFolder = path.join(root, 'second-model')
  await put(
    secondFolder,
    'nested/test.model3.json',
    '\uFEFF' +
      JSON.stringify({
        FileReferences: {
          Moc: 'body.moc3',
          Textures: ['../body.png'],
          Motions: { idle: [{ File: '../idle.motion3.json' }] }
        }
      })
  )
  await put(secondFolder, 'nested/body.moc3', 'second model')
  await put(secondFolder, 'body.png', 'test texture')
  await put(secondFolder, 'idle.motion3.json', '{"Meta":{"Duration":1},"Curves":[]}')
  const { modelId: secondId } = await backend.outfits.importDirectory(target, secondFolder)
  expect(
    (
      await backend.characters.resolveCapabilities(target.characterId, {
        formId: target.formId,
        modelId: secondId
      })
    ).modelVersion
  ).toBe('v3')
  await put(secondFolder, 'another.model.json', '{}')
  await expect(backend.outfits.importDirectory(target, secondFolder)).rejects.toMatchObject({
    problem: {
      code: 'live2d_multiple_entries',
      message: expect.stringContaining('another.model.json')
    }
  })
  await backend.outfits.rename(target, secondId, '晚礼服')
  await backend.outfits.setDefault(target, secondId)
  await backend.conversations.getSnapshot(first)
  expect(await read(first)).toBe(firstId)
  expect(await read(await backend.conversations.createChat(options))).toBe(secondId)
  expect(await backend.outfits.previewRemoval(target, firstId)).toMatchObject({
    count: 2,
    busy: false,
    defaultName: '晚礼服'
  })
  await backend.outfits.remove(target, firstId, secondId)
  const live = await backend.conversations.getSnapshot(first)
  expect(live.chat.meta.settings.characters[target.characterId].modelOverrides.stage).toBe(secondId)
  expect(await read(inactive)).toBe(secondId)
  expect((await backend.characters.get(target.characterId)).forms[0].models).toHaveLength(1)
})

test('Bestdori 服装优先展示目录名称，并按已知资源编号补足缺失的显示名', async () => {
  const sources = new OutfitSources(async (url) => {
    if (url.endsWith('/_info.json'))
      return Response.json({
        live2d: {
          chara: {
            '036_casual-2023': 1,
            '036_school_summer-2023': 1,
            '036_collabo_d_3_ur': 1,
            '036_live_default': 1,
            '036_unknown': 1
          }
        }
      })
    return Response.json({
      1786: {
        assetBundleName: '036_live_default',
        description: ['一生を、はじめよう', null, null, '开始吧，一辈子的约定']
      },
      1871: {
        assetBundleName: '036_collabo_d_3_ur',
        description: ['のんびりダイアログ', null, null, null]
      }
    })
  })
  const items = await sources.v2({ id: 'tomori', bestdoriId: 36 })
  expect(Object.fromEntries(items.map(({ id, name }) => [id, name]))).toEqual({
    '036_casual-2023': '常服（2023）',
    '036_school_summer-2023': '夏季校服（2023）',
    '036_collabo_d_3_ur': 'のんびりダイアログ',
    '036_live_default': '开始吧，一辈子的约定',
    '036_unknown': '036_unknown'
  })
})

test('Bestdori 与 ModelScope 的小样本经真实任务、校验和入库流程安装', async () => {
  const archive = zipFixture([
    {
      name: 'outfit.model3.json',
      text: JSON.stringify({ FileReferences: { Moc: 'body.moc3', Textures: ['body.png'] } })
    },
    { name: 'body.moc3' },
    { name: 'body.png' }
  ])
  const row = {
    id: 'stage-test',
    characterId: 'sakiko',
    name: '现代舞台服',
    path: 'models/sakiko/stage-test.zip',
    sizeBytes: archive.length,
    sha256: createHash('sha256').update(archive).digest('hex')
  }
  const { backend } = await fixture({
    fetch: async (url) => {
      const pathname = new URL(url).pathname
      if (pathname.endsWith('/catalog.json'))
        return Response.json({ schemaVersion: 1, items: [row] })
      if (pathname.endsWith('/' + row.path)) return new Response(archive)
      if (pathname.endsWith('/_info.json'))
        return Response.json({
          live2d: { chara: { '341_test': 1, '341_general': 1, '037_test': 1 } }
        })
      if (pathname.endsWith('/all.5.json'))
        return Response.json({
          1: { assetBundleName: '341_test', description: ['日本語', null, null, '旧版舞台服'] }
        })
      if (pathname.endsWith('/buildData.asset')) {
        const bundleName = 'live2d/chara/341_test'
        return Response.json({
          Base: {
            model: { fileName: 'body.moc.bytes', bundleName },
            physics: { fileName: 'missing.physics.json', bundleName },
            textures: [{ fileName: 'body', bundleName }],
            motions: [],
            expressions: []
          }
        })
      }
      if (/\/(body\.moc|body\.png)$/u.test(pathname)) return new Response('fixture')
      return new Response(null, { status: 404 })
    }
  })
  const target = await outfitRole(backend)
  const catalog = await backend.outfits.catalog(target)
  expect(catalog.problems).toEqual([])
  expect(catalog.items.map(({ id, name }) => [id, name])).toEqual([
    ['341_test', '旧版舞台服'],
    ['stage-test', '现代舞台服']
  ])
  for (const item of catalog.items) {
    const job = await backend.outfits.start(target, item.source, item.id)
    await vi.waitFor(() =>
      expect(backend.outfits.listJobs(target).find(({ id }) => id === job.id)?.status).toBe(
        'succeeded'
      )
    )
  }
  expect(backend.outfits.listJobs()).toEqual(backend.outfits.listJobs(target))
  expect(backend.outfits.listJobs({ ...target, formId: 'another-form' })).toEqual([])
  expect(backend.outfits.listJobs().every((job) => job.completedAt > 0)).toBe(true)
  const { models } = (await backend.characters.get(target.characterId)).forms[0]
  expect(models.map(({ source }) => source.kind)).toEqual(['bestdori', 'modelscope'])
  const old = JSON.parse(
    Buffer.from(await backend.assets.read(models[0].presentation.model)).toString('utf8')
  )
  expect(old).toMatchObject({ model: 'body.moc', textures: ['textures/body.png'] })
  expect(old).not.toHaveProperty('physics')
  expect((await backend.outfits.catalog(target)).items.every(({ installed }) => installed)).toBe(
    true
  )
})

test('ModelScope 分片在进程重启后保留，手动继续使用 Range', async () => {
  const archive = zipFixture([
    {
      name: 'model3.json',
      text: JSON.stringify({ FileReferences: { Moc: 'test.moc3', Textures: ['test.png'] } })
    },
    { name: 'test.moc3' },
    { name: 'test.png' }
  ])
  const item = {
    id: 'continuity',
    characterId: 'sakiko',
    name: '续传服装',
    path: 'sakiko/continuity.zip',
    sizeBytes: archive.length,
    sha256: createHash('sha256').update(archive).digest('hex')
  }
  const offset = 64
  const transport = vi.fn(async (url, options) => {
    if (url.endsWith('/catalog.json')) return Response.json({ schemaVersion: 1, items: [item] })
    if (options.headers.Range) {
      expect(options.headers.Range).toBe('bytes=64-')
      return new Response(archive.subarray(offset), {
        status: 206,
        headers: {
          'Content-Range': `bytes ${offset}-${archive.length - 1}/${archive.length}`
        }
      })
    }
    return new Response(
      new ReadableStream({
        start(controller) {
          controller.enqueue(archive.subarray(0, offset))
        }
      })
    )
  })
  const { backend, roots } = await fixture({ fetch: transport })
  const target = await outfitRole(backend)
  const job = await backend.outfits.start(target, 'modelscope', item.id)
  const partial = path.join(roots.cacheRoot, 'outfit-downloads', job.id, item.sha256 + '.part')
  await vi.waitFor(async () => expect((await stat(partial)).size).toBe(offset))
  await backend.outfits.close()
  expect(backend.outfits.listJobs(target)[0].status).toBe('interrupted')
  const restarted = await createCoreBackend({ ...roots, scanReminders: false, fetch: transport })
  cleanups.push(() => restarted.close())
  expect(restarted.outfits.listJobs(target)[0].status).toBe('interrupted')
  await restarted.outfits.control(job.id, 'resume')
  await vi.waitFor(() => expect(restarted.outfits.listJobs(target)[0].status).toBe('succeeded'))
  expect(transport.mock.calls.some(([, options]) => options.headers.Range === 'bytes=64-')).toBe(
    true
  )
})

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

test('只有待配置连接的旧目录仍可选择导入并保留告警', async () => {
  const { root, backend } = await fixture()
  const source = path.join(root, 'pending-connections')
  await put(
    source,
    'd_sakiko_config.json',
    JSON.stringify({
      llm_setting: {
        llm_api_provider: 'unknown-provider',
        llm_api_model: 'unknown-model',
        llm_api_key: 'private-pending-secret'
      }
    })
  )
  const scan = await backend.setup.scanLegacy(
    (await backend.setup.grantLegacySource(source)).sourceId
  )
  expect(scan).toMatchObject({
    canImport: true,
    connectionCount: 0,
    pendingConnectionCount: 1,
    availableCategories: { connections: true }
  })
  expect(JSON.stringify(scan)).not.toContain('private-pending-secret')
  const plan = await backend.setup.createPlan({
    kind: 'legacy',
    scanId: scan.scanId,
    include: { connections: true }
  })
  await backend.setup.start(plan.id)
  const result = await done(backend.setup, plan.id)
  expect(result).toMatchObject({
    status: 'succeeded',
    items: { connections: { status: 'partial' } },
    warnings: expect.arrayContaining([{ code: 'legacy_connections_pending', count: 1 }])
  })
  expect(JSON.stringify(result)).not.toContain('private-pending-secret')
})

test('整套语音一致时复用导入资源；只匹配权重时计划跳过相同权重', async () => {
  const voice = (id) => {
    const definition = {
      schemaVersion: 1,
      characterId: id,
      architecture: 'v2ProPlus',
      weights: { gpt: 'a.ckpt', sovits: 'b.pth' },
      forms: { default: { defaultReference: { audio: 'c.wav', text: '参考台词', language: 'ja' } } }
    }
    const contents = {
      'a.ckpt': Buffer.from('gpt'),
      'b.pth': Buffer.from('sovits'),
      'c.wav': Buffer.from('published voice'),
      'voice.json': Buffer.from(JSON.stringify(definition))
    }
    return {
      id: 'voice.' + id,
      characterId: id,
      kind: 'voice',
      version: '1',
      available: true,
      contents,
      files: Object.entries(contents).map(([name, bytes]) => ({
        path: id + '/' + name,
        source: 'voices',
        sizeBytes: bytes.length,
        sha256: createHash('sha256').update(bytes).digest('hex')
      }))
    }
  }
  const components = [voice('anon'), voice('soyo')]
  const catalog = {
    platform: 'darwin',
    arch: 'arm64',
    components: async () => structuredClone(components),
    select: async (choices) =>
      structuredClone(components.filter((item) => choices.includes(item.id)))
  }
  const transport = async (url) => {
    const item = components.find((component) => url.endsWith(component.characterId + '/voice.json'))
    return item ? new Response(item.contents['voice.json']) : new Response(null, { status: 404 })
  }
  const { root, backend } = await fixture({ setupOptions: { catalog }, fetch: transport })
  for (const item of components) {
    const refs = {}
    const id = item.characterId
    const owner = await backend.assets.createTemporaryOwner('draft')
    for (const name of ['a.ckpt', 'b.pth', 'c.wav']) {
      const bytes = id === 'soyo' && name === 'c.wav' ? 'custom sound' : item.contents[name]
      const file = await put(root, id + '/' + name, bytes)
      refs[name] = (
        await backend.assets.importAsset(
          { kind: 'local-file', path: file },
          owner,
          name.endsWith('.wav') ? 'audio' : 'model'
        )
      ).asset
    }
    await backend.characters.save({
      id,
      name: id,
      revision: 0,
      defaultFormId: 'default',
      knowledgeMappings: [],
      extensions: {},
      forms: [
        {
          id: 'default',
          displayName: id,
          description: '',
          avatar: null,
          themeColor: null,
          models: [],
          defaultModelId: null,
          voice: {
            gptWeights: refs['a.ckpt'],
            sovitsWeights: refs['b.pth'],
            defaultReference: { audio: refs['c.wav'], text: '参考台词', language: 'ja' },
            emotions: {}
          }
        }
      ]
    })
    await backend.assets.releaseOwner(owner)
  }
  const catalogResult = await backend.setup.getCatalog({ managed: true })
  const options = catalogResult.groups.flatMap((group) => group.options)
  expect(options.find((item) => item.id === 'voice.anon')).toMatchObject({
    installed: true,
    ready: true,
    downloadBytes: 0,
    updateAvailable: false,
    forms: [{ active: 'package', customAvailable: false }]
  })
  expect(options.find((item) => item.id === 'voice.soyo')).toMatchObject({
    installed: false,
    ready: false,
    forms: [{ active: 'custom', customAvailable: true }]
  })
  const plan = await backend.setup.createPlan({
    kind: 'download',
    managed: true,
    selection: ['voice.soyo']
  })
  const job = await backend.setup.readJob(plan.id)
  expect(job.selected[0].files.map((file) => file.path)).toEqual(['soyo/c.wav', 'soyo/voice.json'])
  expect(Object.keys(job.selected[0].reused)).toEqual(['soyo/a.ckpt', 'soyo/b.pth'])
  await backend.setup.selectVoice('voice.anon', 'default', 'none')
  await backend.setup.removeVoice('voice.anon')
  expect((await backend.setup.installations())['voice.anon']).toBeUndefined()
})

test('已安装的识别资源收到新版报价，校验失败时保留安装版本', async () => {
  const names = [
    'config.json',
    'tokenizer.json',
    'tokenizer_config.json',
    'preprocessor_config.json',
    'generation_config.json',
    'onnx/encoder_model_quantized.onnx',
    'onnx/decoder_model_merged_quantized.onnx'
  ]
  let version = 'first'
  let corrupt = false
  function asr() {
    const material = Buffer.from(version)
    return {
      id: 'asr.models',
      kind: 'asr',
      available: true,
      version,
      files: names.map((name) => ({
        path: name,
        source: 'asr',
        sizeBytes: material.length,
        sha256: createHash('sha256').update(material).digest('hex')
      }))
    }
  }
  const catalog = {
    platform: 'darwin',
    arch: 'arm64',
    components: async () => [asr()],
    select: async (choices) => (choices.includes('asr') ? [asr()] : [])
  }
  const { backend } = await fixture({
    setupOptions: { catalog },
    fetch: async () => new Response(corrupt ? 'broken' : version)
  })
  const plan = await backend.setup.createPlan({
    kind: 'download',
    selection: ['asr'],
    managed: true
  })
  await backend.setup.start(plan.id)
  expect((await done(backend.setup, plan.id)).status).toBe('succeeded')
  version = 'second'
  const latest = await backend.setup.getCatalog({ managed: true })
  expect(latest.groups[0].options[0]).toMatchObject({ installed: true, updateAvailable: true })
  const update = await backend.setup.createPlan({
    kind: 'download',
    selection: ['asr'],
    managed: true
  })
  corrupt = true
  await backend.setup.start(update.id)
  expect((await done(backend.setup, update.id)).status).toBe('failed')
  expect((await backend.setup.installations())['asr.models'].version).toBe('first')
  corrupt = false
  await backend.setup.controlJob(update.id, 'resume')
  expect((await done(backend.setup, update.id)).status).toBe('succeeded')
  expect((await backend.setup.installations())['asr.models'].version).toBe('second')
})

test('正常初始化导入 Python 厂商字典中的连接和所选模型', async () => {
  const { root, backend } = await fixture()
  const config = {
    llm_setting: {
      llm_api_provider: 'deepseek',
      llm_api_model: {
        deepseek: 'deepseek/existing',
        modelscope: 'deepseek-ai/fixture',
        gemini: 'gemini/fixture',
        dashscope: 'dashscope/qwen-fixture',
        openai: 'example'
      },
      llm_api_key: {
        deepseek: 'fixture-deepseek',
        modelscope: 'fixture-modelscope',
        gemini: 'fixture-gemini',
        dashscope: 'fixture-dashscope'
      },
      llm_api_base_url: { modelscope: 'https://api-inference.modelscope.cn/v1' }
    }
  }
  const directory = await legacy(root)
  await put(directory, 'd_sakiko_config.json', JSON.stringify(config))
  const grant = await backend.setup.grantLegacySource(directory)
  const scan = await backend.setup.scanLegacy(grant.sourceId)
  expect(scan).toMatchObject({ connectionCount: 4, pendingConnectionCount: 0 })
  expect(JSON.stringify(scan)).not.toContain('fixture-deepseek')
  const plan = await backend.setup.createPlan({
    kind: 'legacy',
    scanId: scan.scanId,
    include: { connections: true }
  })
  await backend.onboarding.begin({ path: 'legacy', planId: plan.id })
  expect(await done(backend.setup, plan.id)).toMatchObject({
    status: 'succeeded',
    items: { connections: { status: 'ready', completed: 4 } }
  })

  const connections = await backend.settings.listConnections()
  expect(connections).toMatchObject([
    { provider: 'deepseek', models: [{ id: 'existing' }], credentialConfigured: true },
    { provider: 'modelscope', models: [{ id: 'deepseek-ai/fixture' }], credentialConfigured: true },
    { provider: 'gemini', models: [{ id: 'fixture' }], credentialConfigured: true },
    { provider: 'dashscope', models: [{ id: 'qwen-fixture' }], credentialConfigured: true }
  ])
  expect((await backend.settings.get()).generation).toMatchObject({
    connectionId: connections[0].id,
    modelId: 'existing'
  })
})
