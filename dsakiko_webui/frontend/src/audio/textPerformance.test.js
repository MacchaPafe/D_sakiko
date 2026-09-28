import { act, renderHook } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { useAudioController } from './useAudioController'

class FakeAudio extends EventTarget {
  static latest
  constructor() { super(); FakeAudio.latest = this }
  paused = true
  ended = false
  volume = 1
  pause() { this.paused = true; this.dispatchEvent(new Event('pause')) }
  play() { this.paused = false; this.dispatchEvent(new Event('play')); return Promise.resolve() }
  removeAttribute() {}
  load() {}
}

afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals() })

it('presents text-only segments in sequence and cancels their timers', async () => {
  vi.useFakeTimers()
  vi.stubGlobal('Audio', FakeAudio)
  const { result, unmount } = renderHook(() => useAudioController())
  act(() => {
    result.current.enqueue({ id: 'one', text: '你好', performance: { motion: 'nod', expression: 'smile' } })
    result.current.enqueue({ id: 'two', text: '再见', performance: { motion: 'nod', expression: 'sad' } })
  })
  expect(result.current.playback).toMatchObject({ messageId: 'one', status: 'presenting' })
  // 浏览器可能在下一任务才派发上一段音频的 pause/error/timeupdate。
  act(() => {
    for (const event of ['pause', 'error', 'timeupdate', 'ended']) FakeAudio.latest.dispatchEvent(new Event(event))
  })
  expect(result.current.playback).toMatchObject({ messageId: 'one', status: 'presenting' })
  await act(() => vi.advanceTimersByTimeAsync(6000))
  expect(result.current.playback).toMatchObject({ messageId: 'two', status: 'presenting' })
  act(() => result.current.stop())
  await act(() => vi.advanceTimersByTimeAsync(30000))
  expect(result.current.playback.status).toBe('idle')
  unmount()
})
