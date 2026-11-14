import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises'
import path from 'node:path'
import os from 'node:os'
import { afterEach, expect, test, vi } from 'vitest'
import { WebSocket } from 'ws'
import { Notices } from '../../src/backend/notices/interface.js'
import { readNoticeSources } from '../../src/backend/notices/sources.js'
import { createCoreBackend } from '../../src/backend/create-core-backend.js'
import { writeJson } from '../../src/backend/core/support.js'
import { startHttpServer } from '../../src/server/http-server.js'
import { createBackendClient } from '../../src/renderer/src/backend/client.js'

const cleanups = []
afterEach(async () => {
  for (const cleanup of cleanups.splice(0).reverse()) await cleanup()
  vi.restoreAllMocks()
})

/** @returns {Promise<string>} 每项验证独享数据目录，结束后清理。 */
async function directory() {
  const root = await mkdtemp(path.join(os.tmpdir(), 'sakiko-notices-'))
  cleanups.push(() => rm(root, { recursive: true, force: true }))
  return root
}

/** @param {string} key 来源身份。 @param {number} createdAt 时间。 @returns {import('../../src/backend/notices/interface.js').NoticeSource} 可公开的测试来源。 */
function source(key, createdAt = 1) {
  return { key, createdAt, kind: 'connection', data: { connectionState: 'missing' } }
}

test('通知顺序、关闭证据和身份跨重启保留，迟到来源追加而不抢占', async () => {
  const root = await directory()
  let sources = [source('first', 20)]
  let notices = new Notices({ appDataRoot: root, readSources: async () => sources })
  const first = (await notices.list()).notices[0]
  sources.push(source('late', 10))
  expect((await notices.list()).notices.map((item) => item.id)).toEqual([
    first.id,
    (await notices.list()).notices[1].id
  ])
  const dismissed = await notices.dismiss(first.id)
  expect(dismissed.notices).toHaveLength(1)
  expect((await notices.dismiss(first.id)).revision).toBe(dismissed.revision)
  await notices.close()
  notices = new Notices({ appDataRoot: root, readSources: async () => sources })
  cleanups.push(() => notices.close())
  const restored = await notices.list()
  expect(restored.runtimeId).not.toBe(dismissed.runtimeId)
  expect(restored.notices).toEqual(dismissed.notices)
  sources = [source('first', 20), source('late', 10), source('new', 5)]
  const next = await notices.list()
  expect(next.notices.map((item) => item.order)).toEqual([1, 2])
  expect(next.notices.some((item) => item.id === first.id)).toBe(false)
})

test('来源失效后隐藏，恢复不复活已关闭记录，数据刷新不改变身份', async () => {
  const root = await directory()
  let sources = [source('connection')]
  const notices = new Notices({ appDataRoot: root, readSources: async () => sources })
  cleanups.push(() => notices.close())
  const original = (await notices.list()).notices[0]
  sources = []
  expect((await notices.list()).notices).toEqual([])
  sources = [{ ...source('connection'), data: { connectionState: 'builtin' } }]
  expect((await notices.list()).notices[0]).toEqual({
    ...original,
    data: { connectionState: 'builtin' }
  })
  await notices.dismiss(original.id)
  sources = []
  await notices.list()
  sources = [source('connection')]
  expect((await notices.list()).notices).toEqual([])
})

test('通知写入失败可从业务来源补齐，关闭失败不发布虚假成功', async () => {
  const root = await directory()
  let failing = true
  const notices = new Notices({
    appDataRoot: root,
    readSources: async () => [source('ready')],
    write: async (...args) => {
      if (failing) throw new Error('disk unavailable')
      await writeJson(...args)
    }
  })
  cleanups.push(() => notices.close())
  await expect(notices.list()).rejects.toThrow('disk unavailable')
  failing = false
  const seen = []
  const stop = await notices.observe((snapshot) => seen.push(snapshot))
  const original = seen[0].notices[0]
  failing = true
  await expect(notices.dismiss(original.id)).rejects.toThrow('disk unavailable')
  expect(seen).toHaveLength(1)
  failing = false
  expect((await notices.list()).notices[0]).toEqual(original)
  await notices.dismiss(original.id)
  expect(seen.at(-1).notices).toEqual([])
  stop()
})

test('来源读取失败不清空通知，仍可关闭；观察与并发核对不会丢失确认', async () => {
  const root = await directory()
  let failing = false
  const notices = new Notices({
    appDataRoot: root,
    readSources: async () => {
      if (failing) throw new Error('source unavailable')
      return [source('one')]
    }
  })
  cleanups.push(() => notices.close())
  const snapshots = []
  const stop = await notices.observe((snapshot) => snapshots.push(snapshot))
  const id = snapshots[0].notices[0].id
  failing = true
  await expect(notices.list()).rejects.toThrow('source unavailable')
  expect(snapshots).toHaveLength(1)
  await notices.dismiss(id)
  failing = false
  await Promise.all([notices.list(), notices.dismiss(id), notices.list()])
  expect((await notices.list()).notices).toEqual([])
  expect(snapshots.map((snapshot) => snapshot.revision)).toEqual([1, 2])
  stop()
})

test('损坏的通知文件不会作为空历史覆盖', async () => {
  const root = await directory()
  const file = path.join(root, 'notices.json')
  await writeFile(file, '{broken')
  const notices = new Notices({ appDataRoot: root, readSources: async () => [source('one')] })
  cleanups.push(() => notices.close())
  await expect(notices.list()).rejects.toThrow()
  expect(await readFile(file, 'utf8')).toBe('{broken')
})

/** @param {string} root 数据根目录。 @returns {object} 后端独立数据位置。 */
function roots(root) {
  return {
    appDataRoot: path.join(root, 'app'),
    resourceLibraryRoot: path.join(root, 'library'),
    cacheRoot: path.join(root, 'cache'),
    scanReminders: false
  }
}

test('初始化完成后生成连接通知，写入失败不回滚完成状态，重启补齐并统一确认', async () => {
  const config = roots(await directory())
  let backend = await createCoreBackend(config)
  const failure = vi
    .spyOn(backend.notices, 'write')
    .mockRejectedValue(new Error('disk unavailable'))
  // 此处从持久可选资源阶段完成，避免通知测试重复解包所有内置模型。
  await backend.onboarding.save({
    version: 1,
    path: 'quick',
    stage: 'optional-resources',
    initialized: false
  })
  expect(await backend.onboarding.finish()).toMatchObject({
    initialized: true,
    connectionState: 'missing'
  })
  await backend.close()
  failure.mockRestore()
  backend = await createCoreBackend(config)
  cleanups.push(() => backend.close())
  const notice = (await backend.notices.list()).notices[0]
  expect(notice).toMatchObject({ kind: 'connection', data: { connectionState: 'missing' } })
  await backend.notices.dismiss(notice.id)
  expect((await backend.onboarding.getState()).connectionState).toBe('missing')
  expect(await backend.onboarding.getState()).not.toHaveProperty('noticeAcknowledged')
  expect(backend.onboarding.acknowledgeNotice).toBeUndefined()
  await backend.close()
  backend = await createCoreBackend(config)
  expect((await backend.notices.list()).notices).toEqual([])
})

test('语音来源按实际形态和当前偏好展示，角色缺失不产生错误就绪承诺', async () => {
  let available = true
  let enabled = true
  let deleted = false
  const modules = {
    onboarding: { getState: async () => ({ initialized: false }) },
    setup: {
      listVoiceNoticeMilestones: async () => [
        { jobId: 'job', characterId: 'sakiko', formIds: ['black'], createdAt: 4 }
      ],
      getFeatureState: async () => ({
        characters: [
          { characterId: 'sakiko', formId: 'black', ready: available, autoEnabled: enabled },
          { characterId: 'sakiko', formId: 'white', ready: true, autoEnabled: enabled }
        ]
      })
    },
    settings: { get: async () => false },
    characters: { get: async () => (deleted ? null : { id: 'sakiko', name: '祥子' }) }
  }
  expect((await readNoticeSources(modules))[0].data).toMatchObject({
    readyFormIds: ['black'],
    enabled: true
  })
  enabled = false
  expect((await readNoticeSources(modules))[0].data.enabled).toBe(false)
  available = false
  expect(await readNoticeSources(modules)).toEqual([])
  available = true
  deleted = true
  expect(await readNoticeSources(modules)).toEqual([])
})

/**
 * 只替换真实模型安装与推理可用性，保留任务受理、持久化、通知和偏好流程。
 * @returns {Promise<{backend: import('../../src/backend/create-core-backend.js').CoreBackend, setReady: (value: boolean) => void}>} 可控的语音安装夹具。
 */
async function voiceBackend() {
  const component = {
    id: 'voice.sakiko',
    kind: 'voice',
    characterId: 'sakiko',
    version: 'test',
    available: true,
    files: [],
    requires: []
  }
  const backend = await createCoreBackend({
    ...roots(await directory()),
    setupOptions: { catalog: { select: async () => [structuredClone(component)] } }
  })
  cleanups.push(() => backend.close())
  const role = { id: 'sakiko', name: '祥子', forms: [{ id: 'black', voice: {} }] }
  vi.spyOn(backend.characters, 'get').mockImplementation(async (id) =>
    id === role.id ? role : null
  )
  vi.spyOn(backend.characters, 'list').mockResolvedValue([{ id: role.id }])
  vi.spyOn(backend.setup, 'componentReady').mockResolvedValue(false)
  let ready = false
  vi.spyOn(backend.setup, 'speechReady').mockImplementation(async () => ready)
  vi.spyOn(backend.setup, 'installComponent').mockImplementation(async (job, item) => {
    ready = true
    await writeJson(path.join(backend.setup.directory, 'installations.json'), {
      [item.id]: { id: item.id, status: 'ready', jobId: job.id }
    })
  })
  return {
    backend,
    setReady: (value) => {
      ready = value
    }
  }
}

test('真实任务流程先持久就绪来源，通知失败不改变任务成功，偏好关闭后文案材料更新', async () => {
  const { backend } = await voiceBackend()
  const plan = await backend.setup.createPlan({ kind: 'download', selection: ['voice.sakiko'] })
  const writer = vi
    .spyOn(backend.notices, 'write')
    .mockRejectedValue(new Error('notice disk unavailable'))
  await backend.setup.start(plan.id)
  await vi.waitFor(async () =>
    expect((await backend.setup.getJob(plan.id)).status).toBe('succeeded')
  )
  expect(await backend.setup.listVoiceNoticeMilestones()).toEqual([
    expect.objectContaining({ jobId: plan.id, characterId: 'sakiko', formIds: ['black'] })
  ])
  writer.mockRestore()
  const notice = (await backend.notices.list()).notices[0]
  expect(notice).toMatchObject({
    kind: 'voice-ready',
    data: { enabled: true, characterId: 'sakiko' }
  })
  await backend.settings.update({ speech: { enabled: false } })
  expect((await backend.notices.list()).notices[0]).toMatchObject({
    id: notice.id,
    data: { enabled: false }
  })
  await backend.notices.dismiss(notice.id)
  await backend.setup.start(plan.id)
  expect((await backend.notices.list()).notices).toEqual([])
})

test('安装登记后任务检查点前中断，初始化按安装来源补齐里程碑且尊重后来关闭语音', async () => {
  const { backend, setReady } = await voiceBackend()
  const plan = await backend.setup.createPlan({ kind: 'download', selection: ['voice.sakiko'] })
  // 模拟进程停在安装记录提交与 completed 写入之间。
  const job = await backend.setup.readJob(plan.id)
  job.status = 'running'
  job.startedAt = Date.now()
  await backend.setup.save(job)
  await writeJson(path.join(backend.setup.directory, 'installations.json'), {
    'voice.sakiko': { id: 'voice.sakiko', status: 'ready', jobId: job.id }
  })
  setReady(true)
  await backend.settings.update({ speech: { enabled: false } })
  expect((await backend.notices.list()).notices).toEqual([])
  await backend.setup.initialize()
  expect((await backend.setup.getJob(job.id)).status).toBe('paused')
  const notices = (await backend.notices.list()).notices
  expect(notices).toHaveLength(1)
  expect(notices[0]).toMatchObject({ kind: 'voice-ready', data: { enabled: false } })
  await backend.setup.initialize()
  expect((await backend.notices.list()).notices).toEqual(notices)
})

test('未执行的计划和其他任务的安装记录不产生就绪通知，取消任务不自动启用', async () => {
  const { backend, setReady } = await voiceBackend()
  const plan = await backend.setup.createPlan({ kind: 'download', selection: ['voice.sakiko'] })
  setReady(true)
  await backend.setup.initialize()
  expect(await backend.setup.listVoiceNoticeMilestones()).toEqual([])
  const job = await backend.setup.readJob(plan.id)
  job.status = 'paused'
  job.startedAt = Date.now()
  await backend.setup.save(job)
  await writeJson(path.join(backend.setup.directory, 'installations.json'), {
    'voice.sakiko': { status: 'ready', jobId: 'different-job' }
  })
  await backend.setup.initialize()
  expect(await backend.setup.listVoiceNoticeMilestones()).toEqual([])
  job.status = 'cancelled'
  await backend.setup.save(job)
  await writeJson(path.join(backend.setup.directory, 'installations.json'), {
    'voice.sakiko': { status: 'ready', jobId: job.id }
  })
  await backend.setup.initialize()
  expect(await backend.setup.listVoiceNoticeMilestones()).toEqual([])
  expect((await backend.settings.voicePreference()).enabled).toEqual({})
})

test('通知经正式 HTTP/WS 交付、关闭和重新订阅，内部来源入口不公开', async () => {
  const backend = await createCoreBackend(roots(await directory()))
  cleanups.push(() => backend.close())
  await backend.onboarding.save({
    version: 1,
    path: 'quick',
    stage: 'complete',
    initialized: true,
    completedAt: 1
  })
  const server = await startHttpServer(backend)
  cleanups.push(() => server.close())
  const client = createBackendClient({ ...server, WebSocket })
  cleanups.push(() => client.close())
  const snapshots = []
  const stop = client.notices.observe((snapshot) => snapshots.push(snapshot))
  await vi.waitFor(() => expect(snapshots[0]?.notices).toHaveLength(1))
  const notice = (await client.notices.list()).notices[0]
  expect(notice).toEqual(snapshots[0].notices[0])
  await client.notices.dismiss(notice.id)
  await vi.waitFor(() => expect(snapshots.at(-1).notices).toEqual([]))
  stop()
  const restored = []
  const unsubscribe = client.notices.observe((snapshot) => restored.push(snapshot))
  await vi.waitFor(() => expect(restored[0]?.notices).toEqual([]))
  await expect(client.call('notices', 'reconcile')).rejects.toMatchObject({
    code: 'method_not_found'
  })
  await expect(client.call('onboarding', 'acknowledgeNotice')).rejects.toMatchObject({
    code: 'method_not_found'
  })
  await expect(client.call('notices', 'list', ['extra'])).rejects.toMatchObject({
    code: 'invalid_input'
  })
  await expect(client.notices.dismiss('../private')).rejects.toMatchObject({
    code: 'invalid_notice_id'
  })
  expect(JSON.stringify(restored)).not.toContain(backend.notices.file)
  unsubscribe()
})
