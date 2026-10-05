import { afterEach, expect, test, vi } from 'vitest'
import { displayMessage } from '../../src/renderer/src/features/chat/message-display.js'

vi.mock('pixi.js', () => ({ Application: class {}, Ticker: {} }))
vi.mock('pixi-live2d-display', () => ({
  Live2DModel: { registerTicker() {} },
  MotionPriority: { FORCE: 3 }
}))
const { StageSurface } = await import('../../src/renderer/src/performance/surface.js')

const surfaces = []
afterEach(() => {
  for (const surface of surfaces.splice(0)) surface.stop()
  vi.useRealTimers()
})

function fixture(startMotion) {
  const surface = new StageSurface({})
  surfaces.push(surface)
  let onFinish
  const unsubscribe = vi.fn()
  const adapter = {
    startMotion: vi.fn(() => startMotion(() => onFinish?.())),
    onceMotionFinish(callback) {
      onFinish = callback
      return unsubscribe
    },
    setSpeechActive() {},
    resetExpression() {},
    setMouthOpen() {},
    stopMotions() {}
  }
  surface.models.set('a', {
    adapter,
    catalog: { motions: [{ id: 'IDLE:0', group: 'IDLE', index: 0 }] }
  })
  const guide = {
    slotId: 'a',
    text: '你好',
    audio: null,
    performance: { motion: 'auto', expression: 'auto' }
  }
  return { surface, adapter, guide, unsubscribe }
}

test('等待状态和零进度没有消息占位，台词与翻译逐字显示，历史完整保留', () => {
  const entry = { kind: 'character', text: '你好😀呀', translation: '欢迎见面' }
  expect(displayMessage(entry, { mode: 'pending', revealedCharacters: 0 }).visible).toBe(false)
  expect(displayMessage(entry, { mode: 'progress', revealedCharacters: 0 }).visible).toBe(false)
  expect(displayMessage(entry, { mode: 'progress', revealedCharacters: 3 })).toEqual({
    visible: true,
    text: '你好😀',
    translation: '欢迎见'
  })
  expect(displayMessage(entry, { mode: 'full', revealedCharacters: 0 }).text).toBe(entry.text)
  expect(displayMessage(entry).translation).toBe(entry.translation)
})

test.each(['declined', 'rejected'])(
  'SDK 动作 %s 时不立即重试，文字计时仍能完成',
  async (outcome) => {
    vi.useFakeTimers()
    let attempts = 0
    const { surface, adapter, guide, unsubscribe } = fixture(() => {
      // 旧实现连续重试时有界挂起，避免回归测试本身也饿死事件循环。
      if (++attempts > 3) return new Promise(() => {})
      if (outcome === 'rejected') return Promise.reject(new Error('动作缺失'))
      return Promise.resolve(false)
    })
    const playing = surface.play(guide, null, 100)
    await vi.advanceTimersByTimeAsync(10)
    expect(adapter.startMotion).toHaveBeenCalledTimes(1)
    expect(surface.snapshot().current).toBe(guide)
    await vi.advanceTimersByTimeAsync(100)
    await playing
    expect(surface.snapshot().current).toBeNull()
    expect(unsubscribe).toHaveBeenCalledTimes(1)
  }
)

test('SDK 同步完成也让出事件循环，停止立即清理动作回调与计时器', async () => {
  vi.useFakeTimers()
  let attempts = 0
  const { surface, adapter, guide } = fixture((finish) => {
    if (++attempts > 5) return new Promise(() => {})
    finish()
    return Promise.resolve(true)
  })
  const playing = surface.play(guide, null, 10000)
  const inputEvent = vi.fn()
  setTimeout(inputEvent, 5)
  await vi.advanceTimersByTimeAsync(30)
  expect(inputEvent).toHaveBeenCalledOnce()
  expect(adapter.startMotion.mock.calls.length).toBeLessThanOrEqual(3)
  surface.stop()
  await playing
  expect(vi.getTimerCount()).toBe(0)
})

test('停止旧播放后可立刻回放，新播放不受旧动作的迟到结果影响', async () => {
  vi.useFakeTimers()
  const resolvers = []
  const { surface, guide, unsubscribe } = fixture(
    () => new Promise((resolve) => resolvers.push(resolve))
  )
  const first = surface.play(guide, null, 10000)
  await vi.advanceTimersByTimeAsync(1)
  surface.stop()
  await first
  const secondGuide = { ...guide, text: '下一句' }
  const second = surface.play(secondGuide, null, 10000)
  await vi.advanceTimersByTimeAsync(1)
  resolvers[0](false)
  await vi.advanceTimersByTimeAsync(1)
  expect(surface.snapshot().current).toBe(secondGuide)
  surface.stop()
  await second
  expect(unsubscribe).toHaveBeenCalledTimes(2)
  expect(vi.getTimerCount()).toBe(0)
})
