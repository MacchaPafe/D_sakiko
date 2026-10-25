import { afterEach, expect, test, vi } from 'vitest'
import { createAudioOutput } from '../../src/renderer/src/performance/audio-output.js'
import { createBackendClient } from '../../src/renderer/src/backend/client.js'

const outputs = []
afterEach(() => {
  for (const output of outputs.splice(0)) output.close()
  vi.useRealTimers()
})

class FakeAudio extends EventTarget {
  static instances = []
  constructor(url) {
    super()
    this.src = url
    FakeAudio.instances.push(this)
  }
  async play() {}
  pause() {}
}

function fixture(access) {
  FakeAudio.instances = []
  const client = {
    assets: {
      access: vi.fn(access ?? (async () => ({ url: 'audio', leaseId: 'lease' }))),
      releaseAccess: vi.fn(async () => {})
    }
  }
  const output = createAudioOutput(client, { Audio: FakeAudio })
  outputs.push(output)
  return { client, output }
}

const guide = { audio: { asset: { id: 'audio' }, durationMs: 100 } }

test('音频真正结束或停止时释放访问租约，循环 BGM 保留到停止', async () => {
  const { output, client } = fixture()
  await output.play(guide, {})
  expect(client.assets.releaseAccess).not.toHaveBeenCalled()
  FakeAudio.instances[0].dispatchEvent(new Event('ended'))
  expect(client.assets.releaseAccess).toHaveBeenCalledExactlyOnceWith('lease')
  await output.setBgm({ enabled: true, loop: true, audio: { id: 'bgm' } }, {})
  expect(client.assets.releaseAccess).toHaveBeenCalledTimes(1)
  output.close()
  expect(client.assets.releaseAccess).toHaveBeenCalledTimes(2)
})

test('停止发生在访问请求返回之前，迟到租约立即释放且不播放', async () => {
  let resolveAccess
  const { output, client } = fixture(
    () =>
      new Promise((resolve) => {
        resolveAccess = resolve
      })
  )
  const pending = output.play(guide, {})
  output.stop()
  resolveAccess({ url: 'late', leaseId: 'late-lease' })
  await pending
  expect(FakeAudio.instances).toHaveLength(0)
  expect(client.assets.releaseAccess).toHaveBeenCalledWith('late-lease')
})

test('正式客户端续期活动访问与草稿，关闭后释放且停止续期', async () => {
  vi.useFakeTimers()
  const methods = []
  const client = createBackendClient({
    baseUrl: 'http://localhost',
    token: 'test',
    fetch: async (url) => {
      const method = url.split('/').at(-1)
      methods.push(method)
      const value = method === 'access' ? { url: 'audio', leaseId: 'lease' } : { draftId: 'draft' }
      return new Response(JSON.stringify({ value }))
    }
  })
  await client.assets.access({ id: 'audio' })
  await client.assets.createDraft()
  await vi.advanceTimersByTimeAsync(60000)
  expect(methods).toContain('renewAccess')
  expect(methods).toContain('renewDraft')
  client.close()
  expect(methods).toContain('releaseAccess')
  expect(methods).toContain('discardDraft')
  const count = methods.length
  await vi.advanceTimersByTimeAsync(120000)
  expect(methods).toHaveLength(count)
})

test('客户端关闭后才返回的访问凭证也会释放', async () => {
  let resolveAccess
  const methods = []
  const client = createBackendClient({
    baseUrl: 'http://localhost',
    token: 'test',
    fetch: async (url) => {
      const method = url.split('/').at(-1)
      methods.push(method)
      if (method === 'access')
        return new Promise((resolve) => {
          resolveAccess = resolve
        })
      return new Response(JSON.stringify({ value: null }))
    }
  })
  const pending = client.assets.access({ id: 'audio' })
  client.close()
  resolveAccess(new Response(JSON.stringify({ value: { url: 'late', leaseId: 'late-lease' } })))
  await expect(pending).rejects.toHaveProperty('code', 'client_closed')
  expect(methods).toEqual(['access', 'releaseAccess'])
})

test('租约失效向调用方报告，停止无效续期，并允许显式申请新草稿保护原资源', async () => {
  vi.useFakeTimers()
  const calls = []
  const client = createBackendClient({
    baseUrl: 'http://localhost',
    token: 'test',
    fetch: async (url, options) => {
      const method = url.split('/').at(-1)
      calls.push({ method, args: JSON.parse(options.body).args })
      if (method === 'renewDraft')
        return new Response(
          JSON.stringify({
            problem: { code: 'lease_expired', message: '草稿过期', retryable: false }
          })
        )
      return new Response(JSON.stringify({ value: { draftId: 'draft' } }))
    }
  })
  outputs.push(client)
  const observer = vi.fn()
  const dispose = client.assets.observeLeaseProblems(observer)
  await client.assets.createDraft([{ id: 'existing' }])
  await client.assets.renewResources()
  expect(observer.mock.lastCall[0][0]).toMatchObject({
    kind: 'draft',
    id: 'draft',
    problem: { code: 'lease_expired' }
  })
  await vi.advanceTimersByTimeAsync(60000)
  expect(calls.filter((call) => call.method === 'renewDraft')).toHaveLength(1)
  await client.assets.createDraft([{ id: 'existing' }])
  expect(calls.at(-1)).toEqual({ method: 'createDraft', args: [[{ id: 'existing' }]] })
  dispose()
})
