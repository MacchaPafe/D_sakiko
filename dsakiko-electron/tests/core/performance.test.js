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

function timedFixture({ assets = null, repeatLongDialogueMotion } = {}) {
  const solo = structuredClone(scene)
  solo.slots[0].presentation = { model: { id: 'original' }, mapping: null }
  const planner = new Performance({
    assets,
    now: () => Date.now(),
    repeatLongDialogueMotion,
    resolveModel: async (presentation) => ({
      version: 'v2',
      groups: { happiness: ['primary', 'state', 'follow-up'] },
      states: { mask: { on: { motion: 'state' } } },
      motions: {
        primary: {
          variants: {
            C: {
              member: 'primary.mtn',
              durationMs: presentation.model.id === 'original' ? 6000 : 1200
            }
          }
        },
        state: { variants: { C: { member: 'state.mtn', durationMs: 4000 } } },
        'follow-up': { variants: { C: { member: 'second.mtn', durationMs: 4000 } } }
      }
    })
  })
  return { planner, solo }
}

test('抽签首句从揭晓时刻才开始，减少动态效果时立即开始', async () => {
  vi.useFakeTimers()
  const planner = new Performance({ now: () => Date.now() - 1700000000000 })
  try {
    for (const delay of [2000, 0]) {
      const id = await planner.createSequence(scene)
      const [line] = await planner.appendGuides(id, [{ ...guide, revealAt: Date.now() + delay }])
      if (delay) {
        await vi.advanceTimersByTimeAsync(delay - 1)
        expect((await planner.getProgress(id)).current).toBeNull()
        await vi.advanceTimersByTimeAsync(1)
      }
      expect((await planner.getProgress(id)).current.guideId).toBe(line)
    }
  } finally {
    await planner.close()
  }
})

test('单人台词按后端动作时间表结算，换装只影响后续台词', async () => {
  vi.useFakeTimers()
  let ownerCount = 0
  const releaseWhenPossible = vi.fn(async () => {})
  const assets = {
    createTemporaryOwner: async (kind) => kind + ++ownerCount,
    addReferences: async () => {},
    releaseWhenPossible
  }
  const { planner, solo } = timedFixture({ assets })
  const sequenceId = await planner.createSequence(solo, { timedDialogue: true })
  try {
    const [first, second] = await planner.appendGuides(sequenceId, [
      { ...guide, ready: false },
      { ...guide, pauseBeforeMs: 300 }
    ])
    const audio = { asset: { id: 'voice' }, durationMs: 10000 }
    await planner.resolveGuide(first, { audio })
    const started = (await planner.getProgress(sequenceId)).current
    expect(started).toMatchObject({
      durationMs: 13000,
      guide: {
        subtitleDurationMs: 10000,
        motionDurationMs: 13000,
        resolvedPerformance: {
          motionCues: [
            { atMs: 0, durationMs: 6000, motion: { id: 'primary' } },
            { atMs: 9000, durationMs: 4000, motion: { id: 'follow-up' } }
          ]
        }
      }
    })
    const [replay] = await planner.resolveGuides(solo, [{ ...guide, audio }], {
      timedDialogue: true
    })
    expect(replay).toEqual(started.guide)
    await vi.advanceTimersByTimeAsync(10000)
    const replacement = structuredClone(solo)
    replacement.slots[0].presentation.model.id = 'replacement'
    await planner.setScene(sequenceId, replacement)
    const tail = await planner.getProgress(sequenceId)
    expect(tail.scene.slots[0].presentation.model.id).toBe('original')
    expect(tail.current).toMatchObject({ guideId: first, elapsedMs: 10000, durationMs: 13000 })
    expect(tail.current.guide).toEqual(started.guide)
    expect(releaseWhenPossible).not.toHaveBeenCalledWith('scene1')
    await vi.advanceTimersByTimeAsync(3000)
    expect(releaseWhenPossible).toHaveBeenCalledWith('scene1')
    expect((await planner.getProgress(sequenceId)).waitingFor).toBe(second)
    await vi.advanceTimersByTimeAsync(300)
    const next = await planner.getProgress(sequenceId)
    expect(next.scene.slots[0].presentation.model.id).toBe('replacement')
    expect(next.current).toMatchObject({ guideId: second, durationMs: 1200 })
  } finally {
    await planner.close()
  }
})

test('长句按语音或阅读时长补播，关闭补播仍等单个长动作', async () => {
  const { planner, solo } = timedFixture({ repeatLongDialogueMotion: false })
  const audio = (durationMs) => ({ durationMs, asset: { id: 'voice' } })
  const textOnly = { ...guide, text: '字'.repeat(113) }
  const timed = { timedDialogue: true }
  try {
    const [one] = await planner.resolveGuides(solo, [{ ...textOnly, audio: audio(1000) }], timed)
    expect(one).toMatchObject({ subtitleDurationMs: 1000, motionDurationMs: 6000 })
    const off = await planner.resolveGuides(
      solo,
      [{ ...guide, audio: audio(10000) }, textOnly],
      timed
    )
    expect(off.map((item) => item.resolvedPerformance.motionCues.length)).toEqual([1, 1])
    const enabled = timedFixture({ repeatLongDialogueMotion: true }).planner
    try {
      const eligible = [7000, 9000, 9001].map((durationMs) => ({
        ...guide,
        audio: audio(durationMs)
      }))
      const plans = await enabled.resolveGuides(solo, eligible, timed)
      expect(plans.map((plan) => plan.resolvedPerformance.motionCues.length)).toEqual([1, 1, 2])
      const [short, long, voiced] = await enabled.resolveGuides(
        solo,
        [{ ...guide, text: '字'.repeat(112) }, textOnly, { ...textOnly, audio: audio(1000) }],
        timed
      )
      expect(short).toMatchObject({ subtitleDurationMs: 8960, motionDurationMs: 6000 })
      expect(long).toMatchObject({ subtitleDurationMs: 9040, motionDurationMs: 13000 })
      expect(long.resolvedPerformance.motionCues[1].atMs).toBe(9000)
      expect(voiced.resolvedPerformance.motionCues).toHaveLength(1)
      const theatre = await enabled.resolveGuides(solo, eligible)
      expect(theatre.every((item) => item.resolvedPerformance.motionCues === undefined)).toBe(true)
    } finally {
      await enabled.close()
    }
  } finally {
    await planner.close()
  }
})

test('字幕在语音结束时全部点亮，恢复到动作收尾时不重播音频', async () => {
  vi.useFakeTimers()
  let report
  const output = { play: vi.fn(), stop: vi.fn() }
  const playback = new Playback({
    client: {
      performance: {
        observe(_id, listener) {
          report = listener
          return () => {}
        }
      }
    },
    now: () => Date.now(),
    output
  })
  const line = {
    ...guide,
    audio: { asset: { id: 'voice' }, durationMs: 1000 },
    subtitleDurationMs: 1000,
    motionDurationMs: 2500
  }
  try {
    playback.follow('sequence')
    report({ scene, current: { guideId: 'line', durationMs: 2500, elapsedMs: 500, guide: line } })
    expect(playback.getSnapshot().revealedCharacters).toBe(1)
    expect(output.play).toHaveBeenCalledOnce()
    playback.setVisible(false)
    await vi.advanceTimersByTimeAsync(500)
    playback.setVisible(true)
    expect(playback.getSnapshot()).toMatchObject({ elapsedMs: 1000, revealedCharacters: 2 })
    expect(output.play).toHaveBeenCalledOnce()
    playback.replay({ scene, guides: [line] })
    await vi.advanceTimersByTimeAsync(1000)
    expect(playback.getSnapshot()).toMatchObject({ mode: 'replay', revealedCharacters: 2 })
    await vi.advanceTimersByTimeAsync(1500)
    expect(playback.getSnapshot().mode).toBe('live')
  } finally {
    playback.close()
  }
})

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
test('相邻台词按下限停顿；合成等待已经超过停顿时间时立即播放', async () => {
  vi.useFakeTimers()
  const planner = new Performance({
    now: () => Date.now(),
    minimumReadingMs: 100,
    readingMsPerCharacter: 10
  })
  const sequenceId = await planner.createSequence(scene)
  const [first, second, third] = await planner.appendGuides(sequenceId, [
    { ...guide, pauseBeforeMs: 300 },
    { ...guide, pauseBeforeMs: 300 },
    { ...guide, pauseBeforeMs: 300, ready: false }
  ])
  let observed
  const stop = planner.observe(sequenceId, (update) => {
    observed = update.snapshot
  })
  expect(observed.current.guideId).toBe(first)
  await vi.advanceTimersByTimeAsync(399)
  expect(observed.current).toBeNull()
  expect(observed.waitingFor).toBe(second)
  await vi.advanceTimersByTimeAsync(1)
  expect(observed.current.guideId).toBe(second)
  await vi.advanceTimersByTimeAsync(500)
  expect(observed.waitingFor).toBe(third)
  await planner.resolveGuide(third, { audio: null })
  expect(observed.current.guideId).toBe(third)
  stop()
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
test('整轮回放等待声音启动，保留换句停顿并在播完后恢复实时快照', async () => {
  vi.useFakeTimers()
  const started = Promise.withResolvers()
  const output = { play: vi.fn(() => started.promise), stop: vi.fn() }
  const playback = new Playback({
    client: {
      performance: {
        observe: (_id, listener) => {
          listener({ snapshot: { scene, current: null, transition: null } })
          return () => {}
        }
      }
    },
    events: null,
    now: () => Date.now(),
    output
  })
  const onEnd = vi.fn()
  const audio = { asset: { id: 'saved-audio' }, durationMs: 100 }
  playback.follow('live')
  playback.replay(
    {
      scene,
      guides: [
        { ...guide, audio },
        { ...guide, pauseBeforeMs: 300 }
      ]
    },
    { onEnd }
  )
  await vi.advanceTimersByTimeAsync(1000)
  expect(playback.getSnapshot().progress.current).toBeNull()
  started.resolve()
  await vi.advanceTimersByTimeAsync(399)
  expect(playback.getSnapshot().progress.current.guideId).toBe('replay:0')
  await vi.advanceTimersByTimeAsync(1)
  expect(playback.getSnapshot().progress.current.guideId).toBe('replay:1')
  expect(output.play).toHaveBeenCalledTimes(1)
  await vi.advanceTimersByTimeAsync(800)
  expect(playback.getSnapshot()).toMatchObject({ mode: 'live', progress: { current: null } })
  expect(onEnd).toHaveBeenCalledTimes(1)
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

test('隐藏时保持最新演出锚点但不播放，恢复跳到当前时间，切换后拒绝旧回调', () => {
  const listeners = new Map()
  let now = 0
  const output = { play: vi.fn(), stop: vi.fn(), setVisible: vi.fn() }
  const playback = new Playback({
    client: {
      performance: {
        observe: (id, listener) => {
          listeners.set(id, listener)
          return () => {}
        }
      }
    },
    storage: null,
    events: null,
    now: () => now,
    output
  })
  const snapshot = (id, elapsedMs) => ({
    scene,
    current: { guideId: id, guide, durationMs: 1000, elapsedMs, recurring: false },
    transition: null
  })
  playback.follow('a')
  listeners.get('a')(snapshot('one', 100))
  expect(output.play).toHaveBeenCalledTimes(1)
  playback.setVisible(false)
  listeners.get('a')(snapshot('two', 400))
  expect(output.play).toHaveBeenCalledTimes(1)
  now = 200
  playback.setVisible(true)
  expect(output.play).toHaveBeenLastCalledWith(guide, expect.objectContaining({ offsetMs: 600 }))
  playback.follow('b')
  listeners.get('a')(snapshot('late', 0))
  expect(playback.getSnapshot().progress).toBeNull()
  playback.close()
})

test('V3 等待首句就绪时维持思考，首句开演后即使后续仍在生成也自然待机', async () => {
  vi.useFakeTimers()
  const solo = structuredClone(scene)
  solo.slots[0].presentation = { model: { id: 'solo-v3' } }
  const planner = new Performance({
    now: () => Date.now(),
    resolveModel: async () => ({
      version: 'v3',
      motions: { thinking: { variants: { C: { member: 'thinking.motion3.json' } } } },
      groups: { text_generating: ['thinking'] }
    })
  })
  try {
    const id = await planner.createSequence(solo, { timedDialogue: true })
    await planner.setActivity(id, 'thinking')
    const [first] = await planner.appendGuides(id, [{ ...guide, ready: false }])
    expect((await planner.getProgress(id)).activity).toBe('thinking')
    await planner.resolveGuide(first, { audio: null })
    const started = await planner.getProgress(id)
    expect(started.current.guideId).toBe(first)
    expect(started.activity).toBe('idle')
    await vi.advanceTimersByTimeAsync(started.current.durationMs + 16000)
    const gap = await planner.getProgress(id)
    expect(gap.current).toBeNull()
    expect(gap.defaultPerformances[0].resolvedPerformance.thinkingMotions).toBeUndefined()
  } finally {
    await planner.close()
  }
})

test('剧情短句立即出字，慢语音保留到句尾，手动翻句使用可见字数', async () => {
  vi.useFakeTimers()
  const playback = new Playback({ events: null, now: () => Date.now() })
  const line = { ...guide, text: '今天辛苦了。', subtitleDurationMs: 4500 }
  try {
    playback.setStoryCaptions(true)
    playback.setReplayAuto(false)
    playback.replay({ scene, guides: [line, line] })
    expect(playback.getSnapshot().revealedCharacters).toBe(1)
    await vi.advanceTimersByTimeAsync(60)
    expect(playback.getSnapshot().revealedCharacters).toBe(3)
    playback.setReadingLog(true)
    await vi.advanceTimersByTimeAsync(900)
    expect(playback.getSnapshot().revealedCharacters).toBe(3)
    playback.setReadingLog(false)
    await vi.advanceTimersByTimeAsync(90)
    expect(playback.getSnapshot()).toMatchObject({
      revealedCharacters: 6,
      waiting: false,
      progress: { current: { guideId: 'replay:0' } }
    })
    playback.advanceReplay()
    expect(playback.getSnapshot().progress.current.guideId).toBe('replay:1')
    await vi.advanceTimersByTimeAsync(4499)
    expect(playback.getSnapshot().mode).toBe('replay')
    await vi.advanceTimersByTimeAsync(1)
    expect(playback.getSnapshot().mode).toBe('live')
  } finally {
    playback.close()
  }
})

test('剧情舞台手动翻句、记录暂停和普通舞台接续共用同一回放', async () => {
  vi.useFakeTimers()
  const output = { play: vi.fn(), stop: vi.fn(), setBgmVolume: vi.fn() }
  const playback = new Playback({ events: null, now: () => Date.now(), output })
  const audio = { asset: { id: 'voice' }, durationMs: 1000 }
  const line = { ...guide, audio, subtitleDurationMs: 1000 }
  playback.setReplayAuto(false)
  playback.replay({ scene, guides: [line, line, line] })
  await vi.advanceTimersByTimeAsync(350)
  playback.advanceReplay()
  expect(playback.getSnapshot().revealedCharacters).toBe(2)
  playback.setReadingLog(true)
  await vi.advanceTimersByTimeAsync(4500)
  expect(playback.getSnapshot().elapsedMs).toBe(350)
  playback.setReadingLog(false)
  expect(output.play).toHaveBeenLastCalledWith(line, expect.objectContaining({ offsetMs: 350 }))
  await vi.advanceTimersByTimeAsync(650)
  expect(playback.getSnapshot().waiting).toBe(true)
  playback.advanceReplay()
  await vi.advanceTimersByTimeAsync(1000)
  expect(playback.getSnapshot()).toMatchObject({
    waiting: true,
    progress: { current: { guideId: 'replay:1' } }
  })
  playback.setReplayAuto(true)
  await vi.advanceTimersByTimeAsync(0)
  expect(playback.getSnapshot().progress.current.guideId).toBe('replay:2')
  await vi.advanceTimersByTimeAsync(1000)
  expect(playback.getSnapshot().mode).toBe('live')
  playback.close()
})
