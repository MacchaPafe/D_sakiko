import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { afterEach, expect, test, vi } from 'vitest'
import { config, ConfigItem } from '../../src/backend/core/config.js'
import { writeJson } from '../../src/backend/core/files.js'
import { generationOptions, speechOptions } from '../../src/backend/core/validation.js'
import { createDefaultSettings, settingsDefinition } from '../../src/backend/settings/definition.js'
import { Settings } from '../../src/backend/settings/interface.js'
import { MediaAssets } from '../../src/backend/resources/interface.js'

const directories = []
afterEach(async () => {
  vi.restoreAllMocks()
  await Promise.all(
    directories.splice(0).map((directory) => rm(directory, { recursive: true, force: true }))
  )
})

async function fixture() {
  const directory = await mkdtemp(path.join(tmpdir(), 'dsakiko-settings-'))
  directories.push(directory)
  const roots = {
    appDataRoot: path.join(directory, 'app'),
    resourceLibraryRoot: path.join(directory, 'library'),
    cacheRoot: path.join(directory, 'cache')
  }
  const assets = new MediaAssets(roots)
  const settings = new Settings({ directory: roots.appDataRoot, assets })
  await assets.initialize()
  return { settings, assets, roots }
}

test('配置项独立提供校验和显式修复，修复结果必须再次通过校验', () => {
  const item = config.number({ default: 1, min: 0, max: 2 })
  expect(item.validate(3).valid).toBe(false)
  expect(item.validate(3).message).toBeTruthy()
  expect(item.correct(3)).toBe(2)
  expect(() => item.correct('2')).toThrow()
  expect(() => item.correct(NaN)).toThrow()
  expect(config.boolean({ default: false }).correct('false')).toBe(false)
  expect(config.enum({ default: 'system', values: ['system', 'dark'] }).correct('invalid')).toBe(
    'system'
  )
  expect(() =>
    new ConfigItem({ validate: () => false, correct: () => 1, message: '仍然无效' }).correct(0)
  ).toThrow('仍然无效')
  expect(() => config.number({ default: -1, min: 0 })).toThrow()
})

test('默认值是独立快照，不共享嵌套对象，也不猜测连接和采样参数', () => {
  const first = createDefaultSettings()
  first.generation.reasoning.enabled = false
  const second = createDefaultSettings()
  expect(second.generation.reasoning.enabled).toBe(true)
  expect(second.generation).not.toHaveProperty('connectionId')
  expect(second.speech).not.toHaveProperty('sampling')
  expect(Object.isFrozen(settingsDefinition.generation)).toBe(true)
  const item = new ConfigItem({ default: { enabled: true }, validate: () => true, message: '' })
  item.defaultValue.enabled = false
  expect(item.defaultValue.enabled).toBe(true)
})

test('set 立即保存嵌套配置，update 批量保存一次并保留未提供的字段', async () => {
  const { settings, roots, assets } = await fixture()
  const notifications = []
  await settings.observe((snapshot) => notifications.push(snapshot))
  const first = await settings.set('generation.reasoning.enabled', false, { expectedRevision: 0 })
  expect(first.revision).toBe(1)
  const commit = vi.spyOn(assets, 'commitReferences')
  const next = await settings.update(
    { display: { theme: 'dark' }, generation: { temperature: 0.7, limits: { maxSteps: 4 } } },
    { expectedRevision: first.revision }
  )
  expect(next.revision).toBe(2)
  expect(next.generation.reasoning).toEqual({ enabled: false, effort: null })
  expect(next.generation.limits).toEqual({ maxSteps: 4, maxRequestRetries: 2, maxFormatRepairs: 2 })
  expect(next.display.frameRate).toBe(60)
  expect(commit).toHaveBeenCalledTimes(1)
  const restarted = new Settings({ directory: roots.appDataRoot, assets })
  expect(await restarted.get()).toEqual(next)
  expect(notifications.map((snapshot) => snapshot.revision)).toEqual([0, 1, 2])
})

test('get 可按路径读取默认值和最新值，读取不写盘且返回独立副本', async () => {
  const { settings } = await fixture()
  expect(await settings.get('generation.temperature')).toBe(1)
  expect(await settings.get('generation.connectionId')).toBeUndefined()
  expect(await settings.get('bgm.audio')).toBeNull()
  await expect(readFile(settings.file)).rejects.toHaveProperty('code', 'ENOENT')
  const sampling = { topK: 3, topP: 0.9, temperature: 1, seed: -1, sampleSteps: 16 }
  const saved = await settings.update({ generation: { temperature: 0.4 }, speech: { sampling } })
  const before = await readFile(settings.file, 'utf8')
  expect(await settings.get('generation.temperature')).toBe(0.4)
  const result = await settings.get('speech.sampling')
  result.topK = 100
  expect(await settings.get('speech.sampling')).toEqual(sampling)
  expect(await settings.get()).toEqual(saved)
  expect(await settings.get(undefined)).toEqual(saved)
  expect(await readFile(settings.file, 'utf8')).toBe(before)
})

test('get 与 set 使用同样的路径限制，不允许访问分组、内部数据或原型', async () => {
  const { settings } = await fixture()
  for (const field of [
    '',
    null,
    1,
    'generation',
    'generation.missing',
    'generation..temperature',
    '__proto__.polluted',
    'display.constructor',
    'revision',
    'secrets',
    'bgm.audio.id'
  ]) {
    await expect(settings.get(field)).rejects.toHaveProperty('problem.code', 'invalid_settings')
    await expect(settings.set(field, 1)).rejects.toHaveProperty('problem.code', 'invalid_settings')
  }
})

test('批量任意字段非法时整批拒绝，不写盘、不递增版本、不发布事件', async () => {
  const { settings, assets } = await fixture()
  await settings.set('display.theme', 'dark')
  const before = await readFile(settings.file, 'utf8')
  const listener = vi.fn()
  await settings.observe(listener)
  listener.mockClear()
  const commit = vi.spyOn(assets, 'commitReferences')
  await expect(
    settings.update({ display: { theme: 'light' }, generation: { temperature: 3 } })
  ).rejects.toThrow('generation.temperature')
  expect(await readFile(settings.file, 'utf8')).toBe(before)
  expect(listener).not.toHaveBeenCalled()
  expect(commit).not.toHaveBeenCalled()
})

test('显式修复保存实际值，无法修复的值仍然拒绝整批更新', async () => {
  const { settings } = await fixture()
  await expect(settings.set('generation.temperature', 9)).rejects.toThrow()
  const next = await settings.update(
    { generation: { temperature: 9 }, display: { theme: 'unknown' } },
    { correct: true }
  )
  expect(next.generation.temperature).toBe(2)
  expect(next.display.theme).toBe('system')
  await expect(
    settings.update({ generation: { temperature: -1 }, speech: { speed: 0 } }, { correct: true })
  ).rejects.toThrow('speech.speed')
  expect(await settings.get()).toEqual(next)
})

test('并发增量在同一队列中应用于最新快照，不覆盖其他字段', async () => {
  const { settings } = await fixture()
  const results = await Promise.all([
    settings.set('display.theme', 'dark'),
    settings.update({ generation: { temperature: 0.5 } }),
    settings.set('speech.enabled', true)
  ])
  expect(results.map((result) => result.revision)).toEqual([1, 2, 3])
  const final = await settings.get()
  expect(final.display.theme).toBe('dark')
  expect(final.generation.temperature).toBe(0.5)
  expect(final.speech.enabled).toBe(true)
})

test('相同预期版本只能提交一次，冲突之后队列仍可继续使用', async () => {
  const { settings } = await fixture()
  const results = await Promise.allSettled([
    settings.set('display.theme', 'dark', { expectedRevision: 0 }),
    settings.set('speech.enabled', true, { expectedRevision: 0 })
  ])
  expect(results[0].status).toBe('fulfilled')
  expect(results[1].reason.problem.code).toBe('revision_conflict')
  expect((await settings.get()).speech.enabled).toBe(false)
  expect((await settings.set('speech.enabled', true, { expectedRevision: 1 })).revision).toBe(2)
})

test('设置更新与连接写入共用队列，保留连接和私有凭据', async () => {
  const { settings } = await fixture()
  await Promise.all([
    settings.saveConnection(
      {
        id: 'local',
        revision: 0,
        name: '本地连接',
        provider: 'openai-compatible',
        protocol: 'chat',
        baseUrl: 'http://127.0.0.1:9999/v1',
        attachmentTransport: 'inline',
        models: [{ id: 'test', declared: {}, overrides: {} }]
      },
      { kind: 'replace', secret: 'test-secret' }
    ),
    settings.update({ generation: { connectionId: 'local', modelId: 'test' } }),
    settings.set('display.theme', 'dark')
  ])
  const resolved = await settings.resolveConnection({ connectionId: 'local', modelId: 'test' })
  expect(settings.credential(resolved.credentialScope)).toBe('test-secret')
  expect((await settings.get()).generation.connectionId).toBe('local')
  expect((await settings.get()).display.theme).toBe('dark')
  expect(JSON.stringify(await settings.get())).not.toContain('test-secret')
})

test('等待期间修改调用参数或返回快照，不会绕过校验改变配置', async () => {
  const { settings } = await fixture()
  let release
  const gate = new Promise((resolve) => {
    release = resolve
  })
  const blocker = settings.queue.run('settings', () => gate)
  const patch = { generation: { temperature: 0.5 }, display: { theme: 'dark' } }
  const options = { expectedRevision: 0 }
  const pending = settings.update(patch, options)
  patch.generation.temperature = 100
  options.expectedRevision = 100
  release()
  await blocker
  const result = await pending
  expect(result.generation.temperature).toBe(0.5)
  result.generation.temperature = 100
  const read = await settings.get()
  read.generation.temperature = 100
  expect((await settings.get()).generation.temperature).toBe(0.5)
})

test('同协议多个连接独立保存密钥，解析后冻结设置与凭据，并公开无密钥预设', async () => {
  const { settings } = await fixture()
  for (const id of ['first', 'second']) {
    await settings.saveConnection(
      {
        id,
        revision: 0,
        name: id,
        provider: 'anthropic-compatible',
        protocol: 'anthropic-messages',
        baseUrl: `https://${id}.invalid/v1`,
        behavior: 'generic',
        attachmentTransport: 'inline',
        models: [{ id: 'model', declared: { reasoning: { status: 'supported' } }, overrides: {} }]
      },
      { kind: 'replace', secret: `secret-${id}` }
    )
  }
  const snapshot = await settings.resolveConnection({ connectionId: 'first', modelId: 'model' })
  const first = (await settings.listConnections()).find((item) => item.id === 'first')
  await settings.saveConnection(
    { ...first, behavior: 'anthropic' },
    { kind: 'replace', secret: 'new-secret' }
  )
  expect(settings.credential(snapshot.credentialScope)).toBe('secret-first')
  expect(snapshot.behavior).toBe('generic')
  expect(
    settings.credential(
      (await settings.resolveConnection({ connectionId: 'second', modelId: 'model' }))
        .credentialScope
    )
  ).toBe('secret-second')
  expect(JSON.stringify(await settings.listConnections())).not.toContain('secret-')
  expect(settings.listProviderPresets().map((item) => item.id)).toEqual(
    expect.arrayContaining(['gemini', 'responses-compatible', 'anthropic-compatible', 'volcengine'])
  )
})

test('资源提交失败时不保存、不通知，之后仍能更新', async () => {
  const { settings, assets } = await fixture()
  const before = await settings.set('display.theme', 'dark')
  const listener = vi.fn()
  await settings.observe(listener)
  listener.mockClear()
  await expect(settings.set('bgm.audio', { id: 'missing-resource' })).rejects.toThrow()
  const commit = vi.spyOn(assets, 'commitReferences').mockRejectedValueOnce(new Error('提交失败'))
  await expect(settings.set('display.theme', 'light')).rejects.toThrow('提交失败')
  expect(await settings.get()).toEqual(before)
  expect(listener).not.toHaveBeenCalled()
  commit.mockRestore()
  expect((await settings.set('display.theme', 'light')).revision).toBe(2)
})

test('拒绝未知字段、原型路径、revision 注入和不合法的选项', async () => {
  const { settings } = await fixture()
  for (const field of [
    '__proto__.polluted',
    'display.constructor',
    'display.missing',
    'revision',
    'display'
  ])
    await expect(settings.set(field, 1)).rejects.toHaveProperty('problem.code', 'invalid_settings')
  for (const patch of [
    { revision: 0 },
    { display: { missing: true } },
    { display: null },
    { speech: { enabled: 1 } },
    JSON.parse('{"__proto__":{"polluted":true}}')
  ])
    await expect(settings.update(patch)).rejects.toHaveProperty('problem.code', 'invalid_settings')
  await expect(settings.update({}, { expectedRevision: '0' })).rejects.toThrow()
  await expect(settings.update({}, { correct: 'true' })).rejects.toThrow()
  expect((await settings.get()).revision).toBe(0)
})

test('读取只补缺失默认值，显式非法值与损坏文件不得静默覆盖', async () => {
  const { settings } = await fixture()
  await writeJson(settings.file, {
    settings: { revision: 7, generation: { temperature: 0.3 } },
    connections: {},
    secrets: {}
  })
  const before = await readFile(settings.file, 'utf8')
  const loaded = await settings.get()
  expect(loaded.revision).toBe(7)
  expect(loaded.generation.temperature).toBe(0.3)
  expect(loaded.display.theme).toBe('system')
  expect(await readFile(settings.file, 'utf8')).toBe(before)
  await writeJson(settings.file, {
    settings: { ...loaded, display: { ...loaded.display, theme: 'bad' } },
    connections: {},
    secrets: {}
  })
  const invalid = await readFile(settings.file, 'utf8')
  await expect(settings.set('speech.enabled', true)).rejects.toThrow('display.theme')
  expect(await readFile(settings.file, 'utf8')).toBe(invalid)
  await writeFile(settings.file, '{broken')
  await expect(settings.update({ display: { theme: 'dark' } })).rejects.toThrow(SyntaxError)
  expect(await readFile(settings.file, 'utf8')).toBe('{broken')
})

test('全局批量更新复用规则，对话覆盖保持稀疏且采样组必须完整', async () => {
  const override = { temperature: 0.4 }
  generationOptions(override)
  expect(override).toEqual({ temperature: 0.4 })
  generationOptions({})
  speechOptions({})
  expect(() => generationOptions({ reasoning: { enabled: true } })).toThrow()
  expect(() => speechOptions({ sampling: { topK: 3 } })).toThrow()
  expect(() => speechOptions({ sampling: null })).toThrow()
  const { settings } = await fixture()
  const sampling = { topK: 3, topP: 0.9, temperature: 1, seed: -1, sampleSteps: 16 }
  await settings.set('speech.sampling', sampling)
  await expect(settings.update({ speech: { sampling: { topK: 5 } } })).rejects.toThrow()
  expect((await settings.get()).speech.sampling).toEqual(sampling)
  await settings.set('generation.translationLanguage', null)
  expect((await settings.get()).generation.translationLanguage).toBeNull()
})
