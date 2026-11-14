import { expect, test, vi } from 'vitest'
import { SetupFlow } from '../../src/renderer/src/features/setup/setup-flow.js'
import { taskRows, formatBytes } from '../../src/renderer/src/features/setup/presentation.js'

function deferred() {
  let resolve
  let reject
  const promise = new Promise((yes, no) => {
    resolve = yes
    reject = no
  })
  return { promise, resolve, reject }
}
function fixture({ onboarding = { stage: 'welcome' }, jobs = [] } = {}) {
  const listeners = new Map()
  const catalog = {
    groups: [
      {
        options: [
          { id: 'asr', available: true, ready: false },
          { id: 'gsv', available: true, ready: false },
          { id: 'voice.sakiko', available: true, ready: false, requiresSelection: 'gsv' }
        ]
      }
    ],
    selectionDownloadBytes: 100
  }
  const client = {
    onboarding: {
      getState: vi.fn(async () => onboarding),
      begin: vi.fn(async () => ({ stage: 'optional-resources' }))
    },
    setup: {
      listJobs: vi.fn(async () => jobs),
      getCatalog: vi.fn(async () => catalog),
      getFeatureState: vi.fn(async () => ({ characters: [] }))
    },
    observe: vi.fn((topic, key, listener) => {
      listeners.set(topic + ':' + key, listener)
      return vi.fn(() => listeners.delete(topic + ':' + key))
    }),
    observeConnection: vi.fn(() => vi.fn()),
    close: vi.fn(),
    call: vi.fn()
  }
  const flow = new SetupFlow({ connect: async () => client, randomId: () => 'stable-plan-request' })
  return { flow, client, listeners, catalog }
}
function job(status = 'planned') {
  return {
    id: 'job',
    kind: 'download',
    status,
    phase: 'planned',
    expiresAt: Date.now() + 60000,
    download: { totalBytes: 100, bytes: 0, components: [{ id: 'gsv.models', status: 'waiting' }] }
  }
}
async function selection(flow) {
  await flow.mount()
  await flow.quickStart()
  await vi.waitFor(() => expect(flow.getSnapshot().catalogLoading).toBe(false))
  flow.toggle('gsv')
  await vi.waitFor(() => expect(flow.getSnapshot().catalogLoading).toBe(false))
}

test('快速开始不依赖舞台，重复点击只受理一次，返回不回滚后端状态', async () => {
  const { flow, client } = fixture()
  const begin = deferred()
  client.onboarding.begin.mockReturnValue(begin.promise)
  await flow.mount()
  const first = flow.quickStart()
  await flow.quickStart()
  expect(client.onboarding.begin).toHaveBeenCalledTimes(1)
  begin.resolve({ stage: 'optional-resources', initialized: false })
  await first
  expect(flow.getSnapshot()).toMatchObject({
    view: 'resources',
    onboarding: { initialized: false }
  })
  flow.back()
  expect(flow.getSnapshot().view).toBe('welcome')
  expect(client.call).not.toHaveBeenCalled()
  flow.dispose()
})

test('资源依赖与迟到容量估计隔离；ASR 不接受选择', async () => {
  const { flow, client } = fixture()
  await selection(flow)
  flow.toggle('asr')
  expect(flow.getSnapshot().selection).toEqual(['gsv'])
  const old = deferred()
  client.setup.getCatalog.mockReturnValueOnce(old.promise)
  flow.toggle('voice.sakiko')
  flow.toggle('gsv')
  await vi.waitFor(() => expect(flow.getSnapshot().catalogLoading).toBe(false))
  expect(flow.getSnapshot().selection).toEqual([])
  old.resolve({ groups: [], selectionDownloadBytes: 999 })
  await Promise.resolve()
  expect(flow.getSnapshot().catalog.selectionDownloadBytes).toBe(100)
  flow.toggle('voice.sakiko')
  expect(flow.getSnapshot().selection).toEqual(['gsv', 'voice.sakiko'])
  expect(client.setup.getCatalog).toHaveBeenLastCalledWith({
    selection: ['gsv', 'voice.sakiko'],
    refresh: false
  })
  flow.toggle('voice.sakiko')
  expect(flow.getSnapshot().selection).toEqual(['gsv'])
  flow.dispose()
})

test('丢失创建计划响应重试同一命令身份，较晚 running 响应不能覆盖完成快照', async () => {
  const { flow, client, listeners } = fixture()
  await selection(flow)
  client.call.mockRejectedValueOnce(new Error('连接中断'))
  await flow.download()
  expect(flow.getSnapshot().view).toBe('resources')
  const starting = deferred()
  client.call.mockResolvedValueOnce(job()).mockReturnValueOnce(starting.promise)
  const retry = flow.download()
  await vi.waitFor(() => expect(client.call).toHaveBeenCalledTimes(3))
  expect(client.call.mock.calls[0][3].requestId).toBe(client.call.mock.calls[1][3].requestId)
  const ready = {
    ...job('succeeded'),
    phase: 'complete',
    download: { totalBytes: 100, bytes: 100, components: [{ id: 'gsv.models', status: 'ready' }] }
  }
  listeners.get('setup:job')(ready)
  starting.resolve(job('running'))
  await retry
  expect(flow.getSnapshot().job.status).toBe('succeeded')
  flow.dispose()
})

test('重启恢复暂停任务且不自动下载；卸载释放观察，不取消任务', async () => {
  const { flow, client } = fixture({
    onboarding: { stage: 'optional-resources' },
    jobs: [job('paused')]
  })
  await flow.mount()
  expect(flow.getSnapshot()).toMatchObject({ view: 'download', job: { status: 'paused' } })
  expect(client.call).not.toHaveBeenCalled()
  client.call.mockResolvedValue(job('running'))
  await flow.control('resume')
  expect(client.call).toHaveBeenCalledWith('setup', 'controlJob', ['job', 'resume'])
  flow.dispose()
  expect(client.close).toHaveBeenCalledTimes(1)
  expect(client.call).toHaveBeenCalledTimes(1)
})

test('迟到连接在卸载后关闭，不发布新界面；中断内置准备恢复为资源选择', async () => {
  const { flow, client } = fixture({ onboarding: { stage: 'preparing' } })
  const connecting = deferred()
  flow.connect = () => connecting.promise
  const mounting = flow.mount()
  flow.dispose()
  connecting.resolve(client)
  await mounting
  expect(client.onboarding.getState).not.toHaveBeenCalled()
  expect(client.close).toHaveBeenCalledTimes(1)
  flow.connect = async () => client
  await flow.mount()
  expect(client.onboarding.begin).toHaveBeenCalledWith({ path: 'quick' })
  expect(flow.getSnapshot().view).toBe('resources')
  flow.dispose()
})

test('下载 100% 仍安装时不能显示已就绪，合并引擎必须等待全部组件', () => {
  expect(
    taskRows({
      download: {
        bytes: 100,
        totalBytes: 100,
        components: [
          { id: 'gsv.runtime', status: 'ready' },
          { id: 'gsv.models', status: 'installing' },
          { id: 'voice.sakiko', status: 'waiting' }
        ]
      }
    })
  ).toEqual([
    { id: 'gsv', name: 'GPT-SoVITS', status: 'installing' },
    { id: 'voice.sakiko', name: '祥子', status: 'waiting' }
  ])
  expect(formatBytes(null)).toBe('大小待确认')
  expect(formatBytes(0, 2)).toBe('0.00 MB')
  expect(formatBytes(3e9, 2)).toBe('3.00 GB')
})

test('容量核对失败保留旧目录但不能按旧估计开始下载，刷新成功后恢复', async () => {
  const { flow, client } = fixture()
  await selection(flow)
  client.setup.getCatalog.mockRejectedValueOnce(new Error('核对失败'))
  await flow.refreshCatalog()
  expect(flow.getSnapshot().catalog.selectionDownloadBytes).toBe(100)
  expect(flow.getSnapshot().catalogProblem).toBe('核对失败')
  await flow.download()
  expect(client.call).not.toHaveBeenCalled()
  await flow.refreshCatalog()
  expect(flow.getSnapshot().catalogProblem).toBe(null)
  flow.dispose()
})

test('重新选择先确认取消旧任务；取消失败不丢失原任务或暂停状态', async () => {
  const { flow, client } = fixture({
    onboarding: { stage: 'optional-resources' },
    jobs: [job('paused')]
  })
  client.setup.controlJob = vi
    .fn()
    .mockRejectedValueOnce(new Error('连接中断'))
    .mockResolvedValueOnce(job('cancelled'))
  await flow.mount()
  await flow.reselect()
  expect(flow.getSnapshot()).toMatchObject({
    view: 'download',
    job: { id: 'job', status: 'paused' }
  })
  await flow.reselect()
  expect(client.setup.controlJob).toHaveBeenLastCalledWith('job', 'cancel')
  expect(flow.getSnapshot()).toMatchObject({ view: 'resources', job: null, selection: [] })
  flow.dispose()
})
