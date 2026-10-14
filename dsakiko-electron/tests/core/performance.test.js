import { afterEach, expect, test, vi } from 'vitest'
import { Performance } from '../../src/backend/performance/interface.js'
import { Performance as Playback } from '../../src/renderer/src/performance/interface.js'
afterEach(() => vi.useRealTimers())
const scene = {
  background: null,
  slots: [
    {
      id: 'actor',
      displayName: '演员',
      presentation: null,
      layout: { x: 0.5, y: 0.5, scale: 1, facing: 'right' }
    }
  ]
}
const guide = {
  slotId: 'actor',
  text: '文字',
  translation: null,
  emotion: null,
  performance: { motion: 'auto', expression: 'auto' },
  audio: null,
  ready: true
}
test('两个 sequence 独立推进；未 ready 不跳过；停止只保留当前句', async () => {
  vi.useFakeTimers()
  const planner = new Performance({
    now: () => Date.now(),
    minimumReadingMs: 100,
    readingMsPerCharacter: 10
  })
  const a = await planner.createSequence(scene)
  const b = await planner.createSequence(scene)
  const [first, second] = await planner.appendGuides(a, [guide, guide])
  const [waiting, next] = await planner.appendGuides(b, [{ ...guide, ready: false }, guide])
  await vi.advanceTimersByTimeAsync(40)
  expect((await planner.getProgress(a)).current.guideId).toBe(first)
  expect((await planner.getProgress(b)).waitingFor).toBe(waiting)
  expect(await planner.discardPendingGuides([first, second])).toEqual([second])
  await vi.advanceTimersByTimeAsync(60)
  expect((await planner.waitForGuideFinish(first)).outcome).toBe('completed')
  expect((await planner.waitForGuideFinish(second)).outcome).toBe('removed')
  await planner.resolveGuide(waiting, { audio: null })
  await vi.advanceTimersByTimeAsync(100)
  expect((await planner.getProgress(b)).current.guideId).toBe(next)
  await planner.close()
})
test('取消一个等待不取消演出或其他等待者', async () => {
  vi.useFakeTimers()
  const planner = new Performance({ now: () => Date.now(), minimumReadingMs: 100 })
  const sequenceId = await planner.createSequence(scene)
  const [id] = await planner.appendGuides(sequenceId, [guide])
  const controller = new AbortController()
  const cancelled = planner.waitForGuideFinish(id, controller.signal)
  const rejection = expect(cancelled).rejects.toMatchObject({ name: 'AbortError' })
  controller.abort()
  await rejection
  const completed = planner.waitForGuideFinish(id)
  await vi.advanceTimersByTimeAsync(200)
  expect((await completed).outcome).toBe('completed')
  await planner.close()
})
test('本地音量、回放和逐字计算不向后端上报；其他端新内容不打断回放', async () => {
  vi.useFakeTimers()
  let listener
  let now = 0
  const client = {
    performance: {
      observe: vi.fn((_id, callback) => {
        listener = callback
        return () => {}
      })
    },
    conversations: { createTurn: vi.fn() }
  }
  const entries = new Map()
  const storage = {
    getItem: (key) => entries.get(key) ?? null,
    setItem: (key, value) => entries.set(key, value)
  }
  const output = { play: vi.fn(), stop: vi.fn(), setVolume: vi.fn() }
  const playback = new Playback({ client, storage, events: null, now: () => now, output })
  expect(playback.getPlaybackOptions()).toEqual({ volume: 1, bgmVolume: 1 })
  playback.setPlaybackOptions({ volume: 0.2, bgmVolume: 0.5 })
  playback.follow('live')
  listener({
    snapshot: {
      sequenceId: 'live',
      revision: 1,
      current: { guideId: 'live-one', guide, durationMs: 1000, elapsedMs: 100 }
    }
  })
  now = 500
  expect(playback.getSnapshot().elapsedMs).toBe(600)
  playback.replay({ scene, guides: [guide] })
  listener({
    snapshot: {
      sequenceId: 'live',
      revision: 2,
      current: { guideId: 'live-two', guide, durationMs: 1000, elapsedMs: 0 }
    }
  })
  expect(playback.getSnapshot().mode).toBe('replay')
  expect(playback.getSnapshot().progress.current.guideId).toBe('replay:0')
  client.conversations.createTurn.mockRejectedValueOnce(new Error('busy'))
  await expect(playback.createTurn('chat', {}, {})).rejects.toThrow('busy')
  expect(playback.getSnapshot().mode).toBe('replay')
  client.conversations.createTurn.mockResolvedValueOnce('new-turn')
  await playback.createTurn('chat', {}, {})
  expect(playback.getSnapshot().mode).toBe('live')
  expect(playback.getSnapshot().progress.current.guideId).toBe('live-two')
  expect(new Playback({ client, storage, events: null }).getPlaybackOptions()).toEqual({
    volume: 0.2,
    bgmVolume: 0.5
  })
  playback.close()
})
test('模型目标状态重复请求幂等；相反目标顺序执行，抢占后不伪称已达到目标', async () => {
  vi.useFakeTimers()
  const planner = new Performance({
    now: () => Date.now(),
    resolveModel: async () => ({
      motions: { on: {}, off: {} },
      states: {
        masked: {
          initial: false,
          on: { motion: 'on', durationMs: 100 },
          off: { motion: 'off', durationMs: 100 }
        }
      }
    })
  })
  const stateScene = structuredClone(scene)
  stateScene.slots[0].presentation = { model: { id: 'model' }, mapping: null }
  const id = await planner.createSequence(stateScene)
  const on = await planner.setModelState(id, { slotId: 'actor', state: 'masked', value: true })
  expect(await planner.setModelState(id, { slotId: 'actor', state: 'masked', value: true })).toBe(
    on
  )
  const off = await planner.setModelState(id, { slotId: 'actor', state: 'masked', value: false })
  expect(off).not.toBeNull()
  await vi.advanceTimersByTimeAsync(200)
  expect((await planner.getProgress(id)).modelStates[0].value).toBe(false)
  const interrupted = await planner.setModelState(id, {
    slotId: 'actor',
    state: 'masked',
    value: true
  })
  await planner.appendGuides(id, [guide])
  expect((await planner.waitForGuideFinish(interrupted)).outcome).toBe('removed')
  expect((await planner.getProgress(id)).modelStates[0].value).toBeNull()
  await planner.close()
})
test('清除本地偏好后恢复 100%，BGM 使用总音量乘以相对音量', () => {
  let onStorage
  const entries = new Map()
  const storage = {
    getItem: (key) => entries.get(key) ?? null,
    setItem: (key, value) => entries.set(key, value)
  }
  const output = { setVolume: vi.fn(), setBgmVolume: vi.fn() }
  const playback = new Playback({
    client: {},
    storage,
    events: {
      addEventListener: (_type, listener) => {
        onStorage = listener
      },
      removeEventListener: () => {}
    },
    output
  })
  playback.setPlaybackOptions({ volume: 0.4, bgmVolume: 0.5 })
  expect(output.setBgmVolume).toHaveBeenLastCalledWith(0.2)
  entries.clear()
  onStorage({ key: null })
  expect(playback.getPlaybackOptions()).toEqual({ volume: 1, bgmVolume: 1 })
  playback.close()
})
