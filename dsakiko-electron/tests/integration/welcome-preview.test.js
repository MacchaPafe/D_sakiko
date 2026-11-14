import { expect, test, vi } from 'vitest'
import { WelcomePreview } from '../../src/renderer/src/features/onboarding/welcome-preview.js'

/** @returns {{promise: Promise<object>, resolve: (value: object) => void, reject: (error: Error) => void}} 可手动安排先后顺序的请求。 */
function deferred() {
  let resolve
  let reject
  const promise = new Promise((yes, no) => {
    resolve = yes
    reject = no
  })
  return { promise, resolve, reject }
}

/** @returns {object} 不依赖 WebGL 的界面生命周期夹具。 */
function fixture() {
  const requests = []
  const client = {
    close: vi.fn(),
    call: vi.fn((_namespace, _method, args, options) => {
      const request = { ...deferred(), args, options }
      requests.push(request)
      return request.promise
    })
  }
  const surface = {
    clear: vi.fn(),
    show: vi.fn(async () => {}),
    destroy: vi.fn(),
    setVisible: vi.fn()
  }
  const stop = vi.fn()
  const initialVisibility = deferred()
  let visibility
  const preview = new WelcomePreview({
    connect: async () => client,
    createSurface: () => surface,
    host: () => ({
      onWindowVisibility: (listener) => {
        visibility = listener
        return stop
      },
      getWindowVisibility: () => initialVisibility.promise
    })
  })
  preview.mount({})
  return {
    preview,
    requests,
    client,
    surface,
    stop,
    initialVisibility,
    visibility: (value) => visibility(value)
  }
}

test('快速切换时忽略迟到预览，取消等待不会调用初始化写接口', async () => {
  const { preview, requests, client, surface } = fixture()
  await vi.waitFor(() => expect(requests).toHaveLength(1))
  const selected = preview.select('anon')
  await vi.waitFor(() => expect(requests).toHaveLength(2))
  expect(requests[0].options.signal.aborted).toBe(true)
  requests[1].resolve({ url: 'anon' })
  await selected
  requests[0].resolve({ url: 'sakiko' })
  await Promise.resolve()
  expect(surface.show).toHaveBeenCalledTimes(1)
  expect(surface.show.mock.calls[0][0]).toBe('anon')
  expect(preview.getSnapshot()).toMatchObject({ characterId: 'anon', phase: 'ready' })
  expect(
    client.call.mock.calls.every(
      ([namespace, method]) => namespace === 'onboarding' && method === 'getBuiltinPreview'
    )
  ).toBe(true)
  preview.dispose()
})

test('失败可获取新地址重试；卸载后旧结果不能恢复画布', async () => {
  const { preview, requests, surface, client, stop } = fixture()
  await vi.waitFor(() => expect(requests).toHaveLength(1))
  requests[0].reject(new Error('资源不可读'))
  await vi.waitFor(() => expect(preview.getSnapshot().phase).toBe('error'))
  preview.retry()
  await vi.waitFor(() => expect(requests).toHaveLength(2))
  preview.dispose()
  requests[1].resolve({ url: 'late' })
  await Promise.resolve()
  expect(surface.show).not.toHaveBeenCalled()
  expect(surface.destroy).toHaveBeenCalledOnce()
  expect(client.close).toHaveBeenCalledOnce()
  expect(stop).toHaveBeenCalledOnce()
})

test('迟到的可见性查询不能覆盖更新的隐藏事件', async () => {
  const { preview, surface, initialVisibility, visibility, requests } = fixture()
  visibility(false)
  initialVisibility.resolve(true)
  await Promise.resolve()
  expect(surface.setVisible.mock.calls).toEqual([[false]])
  preview.dispose()
  if (requests[0]) requests[0].resolve({ url: 'unused' })
})
