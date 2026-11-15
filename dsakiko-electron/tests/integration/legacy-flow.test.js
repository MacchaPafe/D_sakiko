import { expect, test, vi } from 'vitest'
import { SetupFlow } from '../../src/renderer/src/features/setup/setup-flow.js'

function deferred() {
  let resolve
  const promise = new Promise((done) => {
    resolve = done
  })
  return { promise, resolve }
}
function job(status = 'paused', warnings = []) {
  return {
    id: 'legacy-job',
    kind: 'legacy',
    status,
    warnings,
    items: { chats: { status: 'ready', completed: 1, total: 1 } }
  }
}
function fixture({ restored, warnings = [] } = {}) {
  let onboarding = restored
    ? { stage: 'importing', path: 'legacy', jobId: 'legacy-job' }
    : { stage: 'welcome', path: null }
  const listeners = new Map()
  const scan = {
    scanId: 'scan',
    canImport: true,
    availableCategories: { characters: true, chats: true, connections: false, gsv: false }
  }
  const client = {
    setup: {
      listJobs: vi.fn(async () => (restored ? [job(restored, warnings)] : [])),
      scanLegacy: vi.fn(async () => scan),
      getJob: vi.fn(async () => job('running')),
      getCatalog: vi.fn(async () => ({ groups: [] })),
      getFeatureState: vi.fn(async () => ({}))
    },
    onboarding: {
      getState: vi.fn(async () => onboarding),
      begin: vi.fn(async () => {
        onboarding = { stage: 'importing', path: 'legacy', jobId: 'legacy-job' }
        return onboarding
      }),
      prepareOptionalResources: vi.fn(async () => ({
        stage: 'optional-resources',
        path: 'legacy',
        initialized: false
      })),
      skipLegacyImport: vi.fn(async () => ({
        stage: 'optional-resources',
        path: 'legacy',
        initialized: false
      }))
    },
    call: vi.fn(async () => job('planned')),
    observe: vi.fn((topic, key, callback) => {
      listeners.set(topic + ':' + key, callback)
      return () => listeners.delete(topic + ':' + key)
    }),
    observeConnection: vi.fn(() => vi.fn()),
    close: vi.fn()
  }
  const picker = vi.fn(async () => ({ sourceId: 'source', name: '旧版程序' }))
  const flow = new SetupFlow({
    connect: async () => client,
    selectLegacyDirectory: picker,
    randomId: () => 'same-command'
  })
  return { flow, client, picker, scan, listeners }
}
async function select(flow) {
  await flow.mount()
  flow.openLegacy()
  await flow.chooseLegacy()
}

test('默认选择可用整类；取消选择器保持原结果；空选择不能开始', async () => {
  const { flow, client, picker, scan } = fixture()
  await select(flow)
  expect(flow.getSnapshot().legacyInclude).toEqual(scan.availableCategories)
  picker.mockResolvedValueOnce(null)
  await flow.chooseLegacy()
  expect(flow.getSnapshot().legacyScan).toBe(scan)
  flow.toggleLegacy('connections')
  flow.toggleLegacy('characters')
  flow.toggleLegacy('chats')
  await flow.importLegacy()
  expect(client.call).not.toHaveBeenCalled()
  expect(client.onboarding.begin).not.toHaveBeenCalled()
  flow.dispose()
})

test('更换目录使迟到扫描失效；返回欢迎也不接收旧扫描', async () => {
  const { flow, client, scan } = fixture()
  await select(flow)
  const first = deferred()
  client.setup.scanLegacy.mockReturnValueOnce(first.promise)
  const pending = flow.scanLegacy({ sourceId: 'old', name: '旧目录' })
  await flow.scanLegacy({ sourceId: 'new', name: '新目录' })
  first.resolve({ ...scan, scanId: 'stale' })
  await pending
  expect(flow.getSnapshot()).toMatchObject({
    legacySource: { sourceId: 'new' },
    legacyScan: { scanId: 'scan' },
    scanning: false
  })
  const second = deferred()
  client.setup.scanLegacy.mockReturnValueOnce(second.promise)
  const leaving = flow.scanLegacy()
  flow.back()
  second.resolve(scan)
  await leaving
  expect(flow.getSnapshot()).toMatchObject({ view: 'welcome', scanning: false, legacyScan: null })
  flow.dispose()
})

test('计划响应丢失重用命令；begin 响应丢失恢复后端已受理任务', async () => {
  const { flow, client } = fixture()
  await select(flow)
  client.call.mockRejectedValueOnce(new Error('连接断开'))
  await flow.importLegacy()
  expect(flow.getSnapshot().view).toBe('legacy')
  client.onboarding.begin.mockImplementationOnce(async () => {
    client.onboarding.getState.mockResolvedValue({
      stage: 'importing',
      path: 'legacy',
      jobId: 'legacy-job'
    })
    throw new Error('响应丢失')
  })
  await flow.importLegacy()
  expect(client.call.mock.calls[0][3]).toEqual(client.call.mock.calls[1][3])
  expect(flow.getSnapshot()).toMatchObject({ view: 'import', job: { status: 'running' } })
  flow.dispose()
})

test('重启显示暂停导入，不自动运行；较晚控制响应不覆盖完成快照', async () => {
  const { flow, client, listeners } = fixture({ restored: 'paused' })
  await flow.mount()
  expect(flow.getSnapshot()).toMatchObject({ view: 'import', job: { status: 'paused' } })
  expect(client.call).not.toHaveBeenCalled()
  const pending = deferred()
  client.call.mockReturnValueOnce(pending.promise)
  const resuming = flow.control('resume')
  listeners.get('setup:legacy-job')(job('succeeded'))
  pending.resolve(job('running'))
  await resuming
  expect(client.onboarding.prepareOptionalResources).toHaveBeenCalledTimes(1)
  expect(flow.getSnapshot()).toMatchObject({
    view: 'resources',
    onboarding: { initialized: false }
  })
  flow.dispose()
})

test('带告警成功等待确认；跳过失败任务保留结果并进入附加资源', async () => {
  const successful = fixture({ restored: 'succeeded', warnings: [{ code: 'legacy_file_missing' }] })
  await successful.flow.mount()
  expect(successful.flow.getSnapshot().view).toBe('import')
  await successful.flow.continueLegacy()
  expect(successful.client.onboarding.prepareOptionalResources).toHaveBeenCalledTimes(1)
  successful.flow.dispose()
  const failed = fixture({ restored: 'failed' })
  await failed.flow.mount()
  await failed.flow.continueLegacy()
  expect(failed.client.onboarding.skipLegacyImport).toHaveBeenCalledTimes(1)
  expect(failed.flow.getSnapshot().view).toBe('resources')
  failed.flow.dispose()
})

test('重新选择必须先取消旧任务，取消失败保留原任务；能返回取消结果', async () => {
  const { flow, client } = fixture({ restored: 'paused' })
  await flow.mount()
  client.call.mockRejectedValueOnce(new Error('取消失败'))
  await flow.reselectLegacy()
  expect(flow.getSnapshot()).toMatchObject({ view: 'import', job: { status: 'paused' } })
  client.call.mockResolvedValueOnce(job('cancelled'))
  await flow.reselectLegacy()
  expect(flow.getSnapshot()).toMatchObject({ view: 'legacy', job: null })
  client.setup.getJob.mockResolvedValueOnce(job('cancelled'))
  await flow.returnToLegacyResult()
  expect(flow.getSnapshot()).toMatchObject({ view: 'import', job: { status: 'cancelled' } })
  flow.dispose()
})
