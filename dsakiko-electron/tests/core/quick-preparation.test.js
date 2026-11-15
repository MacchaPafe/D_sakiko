import { createHash } from 'node:crypto'
import { mkdtemp, mkdir, writeFile, readFile, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { afterEach, expect, test, vi } from 'vitest'
import { createCoreBackend } from '../../src/backend/create-core-backend.js'

const cleanups = []
afterEach(async () => {
  vi.restoreAllMocks()
  for (const cleanup of cleanups.splice(0).reverse()) await cleanup()
})
async function put(root, name, content) {
  const file = path.join(root, name)
  await mkdir(path.dirname(file), { recursive: true })
  await writeFile(file, content)
}
async function fixture() {
  const root = await mkdtemp(path.join(tmpdir(), 'quick-preparation-'))
  cleanups.push(() => rm(root, { recursive: true, force: true }))
  const payloads = {
    'anon/voice.json': JSON.stringify({
      schemaVersion: 1,
      characterId: 'anon',
      architecture: 'v4',
      weights: { gpt: 'gpt.ckpt', sovits: 'sovits.pth' },
      forms: {
        default: {
          defaultReference: { audio: 'ref.wav', text: '你好', language: 'zh' },
          emotions: {}
        }
      }
    }),
    'anon/gpt.ckpt': 'test-gpt',
    'anon/sovits.pth': 'test-sovits',
    'anon/ref.wav': 'test-reference'
  }
  const component = {
    id: 'voice.anon',
    kind: 'voice',
    characterId: 'anon',
    version: 'test',
    available: true,
    requires: [],
    files: Object.entries(payloads).map(([name, content]) => ({
      source: 'voices',
      path: name,
      sizeBytes: Buffer.byteLength(content),
      sha256: createHash('sha256').update(content).digest('hex')
    }))
  }
  const unavailable = { id: 'unavailable', kind: 'runtime', available: false, files: [] }
  const catalog = {
    platform: 'darwin',
    arch: 'arm64',
    components: async () => [component],
    select: async (choices) => (choices.includes('voice.anon') ? [component] : [unavailable])
  }
  const control = { block: false, entered: false }
  const fetchResource = vi.fn(async (url, { signal }) => {
    control.entered = true
    if (control.block)
      await new Promise((resolve, reject) => {
        if (signal.aborted) reject(signal.reason)
        else signal.addEventListener('abort', () => reject(signal.reason), { once: true })
      })
    const name = decodeURIComponent(new URL(url).pathname.split('/character-voices/')[1])
    return new Response(payloads[name])
  })
  const options = {
    appDataRoot: path.join(root, 'app'),
    resourceLibraryRoot: path.join(root, 'library'),
    cacheRoot: path.join(root, 'cache'),
    builtinConnection: { secret: 'test-secret' },
    scanReminders: false,
    setupOptions: { catalog },
    fetch: fetchResource
  }
  async function open() {
    const backend = await createCoreBackend(options)
    cleanups.push(() => backend.close())
    return backend
  }
  return { root, open, control, fetchResource, backend: await open() }
}
async function download(backend) {
  const state = await backend.onboarding.getState()
  const plan = await backend.setup.createPlan({
    kind: 'download',
    selection: ['voice.anon'],
    preparationId: state.preparationId
  })
  await backend.setup.start(plan.id)
  await vi.waitFor(async () =>
    expect((await backend.setup.getJob(plan.id)).status).toBe('succeeded')
  )
  return plan
}

test('未创建角色也能准备语音；重启、撤回和重新开始复用完整包，最终提交才绑定', async () => {
  const { backend, open, fetchResource } = await fixture()
  const initial = await backend.onboarding.begin({ path: 'quick' })
  expect(
    (await backend.setup.getCatalog()).groups
      .flatMap((group) => group.options)
      .find((item) => item.id === 'voice.anon')
  ).toMatchObject({ available: true, ready: false })
  const plan = await download(backend)
  expect(await backend.characters.list()).toEqual([])
  expect(await backend.settings.listConnections()).toEqual([])
  expect((await backend.settings.voicePreference()).enabled).toEqual({})
  expect((await backend.notices.list()).notices).toEqual([])
  const refs = (await backend.setup.installations())['voice.anon'].refs
  await backend.close()
  const resumed = await open()
  expect((await resumed.onboarding.getState()).preparationId).toBe(initial.preparationId)
  await resumed.onboarding.returnToWelcome()
  expect((await resumed.onboarding.getState()).path).toBeNull()
  await expect(resumed.setup.start(plan.id)).rejects.toHaveProperty(
    'problem.code',
    'setup_preparation_changed'
  )
  for (const ref of Object.values(refs)) expect(await resumed.assets.localPath(ref)).toBeTruthy()
  const next = await resumed.onboarding.begin({ path: 'quick' })
  expect(next.preparationId).not.toBe(initial.preparationId)
  await expect(
    resumed.setup.createPlan({
      kind: 'download',
      selection: ['voice.anon'],
      preparationId: initial.preparationId
    })
  ).rejects.toHaveProperty('problem.code', 'setup_preparation_changed')
  const count = fetchResource.mock.calls.length
  const reused = await download(resumed)
  expect(reused.space.download).toBe(0)
  expect(fetchResource.mock.calls.length).toBe(count)
  expect(await resumed.characters.list()).toEqual([])
  // 本例只验证准备与绑定；运行环境不齐时不得声称可推理或自动启用。
  expect((await resumed.onboarding.finish()).initialized).toBe(true)
  expect(await resumed.characters.list()).toHaveLength(3)
  const voice = (await resumed.characters.get('anon')).forms[0].voice
  expect(voice.gptWeights).toEqual(refs['anon/gpt.ckpt'])
  expect(await resumed.setup.voiceReady(voice)).toBe(true)
  expect((await resumed.settings.voicePreference()).enabled).toEqual({})
  expect((await resumed.settings.get()).generation.connectionId).toBe('builtin-up')
  await resumed.onboarding.finish()
  expect(await resumed.characters.list()).toHaveLength(3)
})

test('运行中撤回在安全节点取消，旧任务不能恢复或绑定，随后可以选择旧版导入', async () => {
  const { backend, control, root } = await fixture()
  await backend.onboarding.begin({ path: 'quick' })
  control.block = true
  const job = await backend.setup.createPlan({ kind: 'download', selection: ['voice.anon'] })
  await backend.setup.start(job.id)
  await vi.waitFor(() => expect(control.entered).toBe(true))
  expect((await backend.onboarding.returnToWelcome()).stage).toBe('welcome')
  expect((await backend.setup.getJob(job.id)).status).toBe('cancelled')
  expect(await backend.characters.list()).toEqual([])
  expect((await backend.notices.list()).notices).toEqual([])
  await expect(backend.setup.controlJob(job.id, 'resume')).rejects.toHaveProperty(
    'problem.code',
    'setup_preparation_changed'
  )
  const source = path.join(root, 'python')
  await put(source, 'live2d_related/anon/name.txt', '爱音')
  await put(source, 'live2d_related/anon/character_description.txt', '应保留的旧版人设')
  const scan = await backend.setup.scanLegacy(
    (await backend.setup.grantLegacySource(source)).sourceId
  )
  const imported = await backend.setup.createPlan({ kind: 'legacy', scanId: scan.scanId })
  await backend.onboarding.begin({ path: 'legacy', planId: imported.id })
  await vi.waitFor(async () =>
    expect((await backend.setup.getJob(imported.id)).status).toBe('succeeded')
  )
  expect((await backend.characters.get('anon')).forms[0].description).toBe('应保留的旧版人设')
})

test('撤回中退出后自动完成清理；提交中断只能重试，既有正式角色不被重写', async () => {
  const { backend, open } = await fixture()
  const state = await backend.onboarding.begin({ path: 'quick' })
  const planned = await backend.setup.createPlan({ kind: 'download', selection: ['voice.anon'] })
  await backend.onboarding.save({ ...state, stage: 'returning' })
  await backend.close()
  const resumed = await open()
  expect((await resumed.onboarding.getState()).stage).toBe('welcome')
  expect((await resumed.setup.getJob(planned.id)).status).toBe('cancelled')
  await resumed.onboarding.begin({ path: 'quick' })
  const save = resumed.characters.save.bind(resumed.characters)
  let saved = 0
  const interrupt = vi.spyOn(resumed.characters, 'save').mockImplementation(async (role) => {
    if (++saved === 2) throw new Error('模拟提交中断')
    return save(role)
  })
  await expect(resumed.onboarding.finish()).rejects.toThrow('模拟提交中断')
  interrupt.mockRestore()
  expect(await resumed.characters.list()).toHaveLength(1)
  const first = await resumed.characters.get((await resumed.characters.list())[0].id)
  expect(await resumed.onboarding.getState()).toMatchObject({
    stage: 'committing',
    initialized: false
  })
  await expect(resumed.onboarding.returnToWelcome()).rejects.toHaveProperty(
    'problem.code',
    'onboarding_not_reversible'
  )
  await expect(
    resumed.setup.createPlan({ kind: 'download', selection: ['voice.anon'] })
  ).rejects.toHaveProperty('problem.code', 'onboarding_busy')
  await resumed.close()
  const retry = await open()
  expect((await retry.onboarding.getState()).stage).toBe('committing')
  await retry.onboarding.finish()
  expect(await retry.characters.list()).toHaveLength(3)
  expect(await retry.characters.get(first.id)).toEqual(first)
})

test('改选旧版导入后保留已有语音，缓存的另一套语音不会覆盖它', async () => {
  const { backend, root } = await fixture()
  await backend.onboarding.begin({ path: 'quick' })
  await download(backend)
  await backend.onboarding.returnToWelcome()
  const source = path.join(root, 'python')
  for (const [name, content] of Object.entries({
    'live2d_related/anon/name.txt': '爱音',
    'live2d_related/anon/character_description.txt': '旧版人设',
    'reference_audio/anon/GPT-SoVITS_models/old.ckpt': 'old-gpt',
    'reference_audio/anon/GPT-SoVITS_models/old.pth': 'old-sovits',
    'reference_audio/anon/reference_text.txt': '旧参考音频',
    'reference_audio/anon/old.wav': 'old-reference'
  }))
    await put(source, name, content)
  const scan = await backend.setup.scanLegacy(
    (await backend.setup.grantLegacySource(source)).sourceId
  )
  const plan = await backend.setup.createPlan({ kind: 'legacy', scanId: scan.scanId })
  await backend.onboarding.begin({ path: 'legacy', planId: plan.id })
  await vi.waitFor(async () =>
    expect((await backend.setup.getJob(plan.id)).status).toBe('succeeded')
  )
  const before = (await backend.characters.get('anon')).forms[0].voice
  expect(before).not.toBeNull()
  await backend.onboarding.finish()
  expect((await backend.characters.get('anon')).forms[0].voice).toEqual(before)
  expect(await readFile(await backend.assets.localPath(before.gptWeights), 'utf8')).toBe('old-gpt')
  expect((await backend.settings.voicePreference()).enabled).toEqual({})
})

test('只有最后提交才执行语音自动启用；完整运行环境由独立就绪检查负责', async () => {
  const { backend } = await fixture()
  const enable = vi.spyOn(backend.settings, 'enablePreparedVoice')
  await backend.onboarding.begin({ path: 'quick' })
  await download(backend)
  expect(enable).not.toHaveBeenCalled()
  expect(await backend.setup.listVoiceNoticeMilestones()).toEqual([])
  // 本用例把外部推理环境判定为可用，验证启用意图的提交时机，不执行模型推理。
  vi.spyOn(backend.setup, 'speechReady').mockImplementation(async (_id, voice) => !!voice)
  await backend.onboarding.finish()
  expect(enable).toHaveBeenCalledWith('anon', 0)
  expect((await backend.settings.voicePreference()).enabled.anon).toBe(true)
  expect(await backend.setup.listVoiceNoticeMilestones()).toHaveLength(1)
})
