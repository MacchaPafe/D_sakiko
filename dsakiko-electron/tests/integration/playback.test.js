import { afterEach, expect, test, vi } from 'vitest'
import { displayMessage } from '../../src/renderer/src/features/chat/message-display.js'
import { stageModelKey } from '../../src/renderer/src/performance/stage-layout.js'

vi.mock('pixi.js', () => ({ Application: class {}, RenderTexture: {}, Ticker: {} }))
vi.mock('pixi-live2d-display', () => ({
  Live2DModel: { registerTicker() {} },
  MotionPriority: { FORCE: 3 }
}))
const { StageSurface } = await import('../../src/renderer/src/performance/surface.js')
const { StageController } = await import('../../src/renderer/src/features/home/stage-controller.js')
const { createRuntimeAdapter } =
  await import('../../src/renderer/src/performance/live2d-adapter.js')

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

function dialogueStage() {
  let listener
  const getReplayMaterials = vi.fn()
  const stage = new StageController(
    {
      performance: {
        observe(_id, callback) {
          listener = callback
          return () => {}
        }
      },
      conversations: { getReplayMaterials }
    },
    () => {}
  )
  const modelPresentation = { model: { id: 'model' } }
  const adapter = {
    setMouthOpen() {},
    setSpeechActive() {},
    startMotion: vi.fn().mockResolvedValue(true),
    isPlayingMotion: () => false,
    stopMotions: vi.fn(),
    setExpression: vi.fn().mockResolvedValue(true)
  }
  stage.actors[0].surface = {
    model: {},
    motionGroups: { dialogue: [{ File: 'talk.motion3.json' }, { File: 'next.motion3.json' }] },
    adapter,
    destroy() {}
  }
  stage.actors[0].state.shownOutfitKey = stageModelKey(modelPresentation)
  const scene = { slots: [{ id: 'role', displayName: '祥子', presentation: modelPresentation }] }
  stage.player.follow('sequence')
  return {
    stage,
    report: (progress) => listener({ scene, ...progress }),
    adapter,
    getReplayMaterials
  }
}

test('回放正在展示的台词时，从头播放同一动作和表情', async () => {
  vi.useFakeTimers()
  const { stage, report, adapter, getReplayMaterials } = dialogueStage()
  const guide = {
    slotId: 'role',
    text: 'またね。',
    audio: null,
    resolvedPerformance: {
      motion: { member: 'talk.motion3.json' },
      expression: { id: 'smile' }
    }
  }
  try {
    report({ current: { guideId: 'live', guide, elapsedMs: 500, durationMs: 800 } })
    await vi.advanceTimersByTimeAsync(0)
    expect(adapter.startMotion).toHaveBeenCalledOnce()
    await stage.replayLine('chat')
    await vi.advanceTimersByTimeAsync(0)
    expect(stage.state).toMatchObject({ text: guide.text, replayingLine: true })
    expect(stage.subtitles.getSnapshot()).toBe(0)
    expect(getReplayMaterials).not.toHaveBeenCalled()
    expect(adapter.stopMotions).toHaveBeenCalledOnce()
    expect(adapter.startMotion).toHaveBeenCalledTimes(2)
    expect(adapter.startMotion).toHaveBeenLastCalledWith(
      'dialogue',
      0,
      3,
      150,
      true,
      expect.any(Function)
    )
    expect(adapter.setExpression).toHaveBeenCalledTimes(2)
    expect(adapter.setExpression).toHaveBeenLastCalledWith('smile', expect.any(Function))
  } finally {
    stage.dispose()
  }
})

test('离开后回到小剧场保持静音，主动回放时新朝向从下一句生效', async () => {
  vi.useFakeTimers()
  const { stage, report } = dialogueStage()
  const slots = [
    {
      id: 'role',
      presentation: { model: { id: 'model' } },
      supportsInward: true,
      layout: { direction: 'C' }
    }
  ]
  const guides = ['第一句', '第二句'].map((text) => ({
    slotId: 'role',
    text,
    audio: null,
    subtitleDurationMs: 100
  }))
  try {
    stage.player.suppressScript = true
    report({
      scene: { slots },
      current: { guideId: 'background', guide: guides[0], durationMs: 100 }
    })
    expect(stage.player.getSnapshot().progress.current).toBeNull()
    stage.player.replay({ scene: { slots }, guides })
    const before = () => stage.player.getSnapshot().progress
    expect(before().current.guide.text).toBe('第一句')
    const turned = [{ ...slots[0], layout: { direction: 'L' } }]
    stage.player.refreshReplay(async () => ({ scene: { slots: turned }, guides }))
    expect(before().scene.slots[0].layout.direction).toBe('C')
    await vi.advanceTimersByTimeAsync(101)
    expect(before().current.guide.text).toBe('第二句')
    expect(before().scene.slots[0].layout.direction).toBe('L')
  } finally {
    stage.dispose()
  }
})

test('舞台按 Guide 相对时标启动补播，跳过过期动作且换句立即清理', async () => {
  vi.useFakeTimers()
  const { stage, report, adapter } = dialogueStage()
  let now = 0
  stage.player.now = () => now
  const first = { member: 'nested/talk.motion3.json', entry: { File: 'talk.motion3.json' } }
  const second = { member: 'nested/next.motion3.json', entry: { File: 'next.motion3.json' } }
  const guide = {
    slotId: 'role',
    text: '本句较长',
    audio: null,
    subtitleDurationMs: 10000,
    resolvedPerformance: {
      motion: first,
      motionCues: [
        { atMs: 0, durationMs: 6000, motion: first },
        { atMs: 9000, durationMs: 4000, motion: second }
      ],
      expression: null
    }
  }
  const motionIndices = () => adapter.startMotion.mock.calls.map(([, index]) => index)
  try {
    report({ current: { guideId: 'reconnected', guide, elapsedMs: 7000, durationMs: 13000 } })
    await vi.advanceTimersByTimeAsync(0)
    expect(motionIndices()).toEqual([])
    now += 1999
    stage.tick()
    expect(motionIndices()).toEqual([])
    now += 1
    stage.tick()
    stage.tick()
    expect(motionIndices()).toEqual([1])
    const [, , , , , current] = adapter.startMotion.mock.lastCall
    expect(current()).toBe(true)
    report({ current: null })
    expect(current()).toBe(false)
    expect(adapter.stopMotions).toHaveBeenCalledTimes(2)
    report({ current: { guideId: 'ended', guide, elapsedMs: 13000, durationMs: 13000 } })
    await vi.advanceTimersByTimeAsync(0)
    expect(motionIndices()).toEqual([1])
  } finally {
    stage.dispose()
  }
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

test('V3 适配器切换不同动作时保留旧条目交叉淡出并对齐时长，同动作实例重放先去重', () => {
  const queue = {
    _motions: [],
    stopAllMotions: vi.fn(),
    startMotion: vi.fn((motion) => {
      for (const existing of queue._motions) {
        existing._fadeOutSeconds = existing._motion.getFadeOutTime()
        existing._triggered = true
      }
      const entry = {
        _motion: motion,
        _fadeOutSeconds: 0,
        _triggered: false,
        release: vi.fn(),
        isTriggeredFadeOut: () => entry._triggered,
        setFadeOut: vi.fn((seconds) => {
          entry._fadeOutSeconds = seconds
          entry._triggered = true
        })
      }
      queue._motions.push(entry)
      return entry
    })
  }
  const motionManager = { queueManager: queue, _startMotion: vi.fn() }
  const model = {
    internalModel: { on: vi.fn(), off: vi.fn(), motionManager },
    destroy: vi.fn()
  }
  createRuntimeAdapter(model, 'v3')

  const makeMotion = (fadeIn, fadeOut) => ({
    _fadeIn: fadeIn,
    _fadeOut: fadeOut,
    setFinishedMotionHandler: vi.fn(),
    getFadeInTime: () => fadeIn,
    getFadeOutTime: () => fadeOut,
    setFadeOutTime: vi.fn((seconds) => {
      fadeOut = seconds
    })
  })

  const idleMotion = makeMotion(2.0, 2.0)
  const turnMotion = makeMotion(0.8, 0.8)

  const idleEntry = motionManager._startMotion(idleMotion)
  motionManager._startMotion(turnMotion)

  expect(queue.stopAllMotions).not.toHaveBeenCalled()
  expect(queue._motions).toHaveLength(2)
  expect(idleEntry.setFadeOut).toHaveBeenCalledWith(0.8)
  expect(idleMotion.setFadeOutTime).toHaveBeenCalledWith(0.8)

  const firstTurnEntry = queue._motions[1]
  motionManager._startMotion(turnMotion)
  expect(firstTurnEntry.release).toHaveBeenCalledOnce()
  expect(queue._motions.filter((entry) => entry._motion === turnMotion)).toHaveLength(1)
})

test('小剧场切换朝向时使用 800ms 过渡，同向台词保持 150ms', async () => {
  const { stage, adapter } = dialogueStage()
  const actor = stage.actors[0]
  actor.layoutScope = 'theatre-scope'
  actor.surface.motionGroups = {
    dialogue: [
      { File: 'talk.motion3.json' },
      { File: 'mtn_idle01_C.motion3.json' },
      { File: 'mtn_idle01_L.motion3.json' }
    ]
  }
  const presentation = { model: { id: 'model' } }
  const slotC = { id: 'role', presentation, layout: { direction: 'C' } }
  const slotL = { id: 'role', presentation, layout: { direction: 'L' } }
  const flush = () => new Promise((resolve) => setTimeout(resolve, 0))

  try {
    actor.present(
      slotC,
      { motion: { entry: { File: 'talk.motion3.json' } }, expression: null },
      'g1',
      false,
      null
    )
    await flush()
    expect(adapter.startMotion).toHaveBeenLastCalledWith(
      'dialogue',
      0,
      3,
      150,
      true,
      expect.any(Function)
    )

    actor.present(
      slotL,
      { motion: { entry: { File: 'mtn_idle01_L.motion3.json' } }, expression: null },
      'g2',
      false,
      null
    )
    await flush()
    expect(adapter.startMotion).toHaveBeenLastCalledWith(
      'dialogue',
      2,
      3,
      800,
      true,
      expect.any(Function)
    )

    actor.present(
      slotL,
      { motion: { entry: { File: 'talk.motion3.json' } }, expression: null },
      'g3',
      false,
      null
    )
    await flush()
    expect(adapter.startMotion).toHaveBeenLastCalledWith(
      'dialogue',
      0,
      3,
      150,
      true,
      expect.any(Function)
    )

    actor.present(
      slotL,
      { motion: { entry: { File: 'mtn_idle01_L.motion3.json' } }, expression: null },
      null,
      false,
      null
    )
    await flush()
    expect(adapter.startMotion).toHaveBeenLastCalledWith(
      'dialogue',
      2,
      3,
      800,
      true,
      expect.any(Function)
    )
  } finally {
    stage.dispose()
  }
})

test('思考重播同时等待 SDK 播完和启动后十五秒，台词接管后旧回调失效', async () => {
  vi.useFakeTimers()
  const { stage, report, adapter } = dialogueStage()
  const motion = { member: 'talk.motion3.json' }
  const plan = { motion, thinkingMotions: [motion], idleMotion: null }
  const waiting = {
    current: null,
    defaultPerformances: [{ slotId: 'role', resolvedPerformance: plan }]
  }
  stage.actors[0].surface.setSingleIdle = vi.fn()
  let finish
  const unsubscribe = vi.fn()
  adapter.onceMotionFinish = (callback) => {
    finish = callback
    return unsubscribe
  }
  try {
    report(waiting)
    await vi.advanceTimersByTimeAsync(0)
    expect(adapter.startMotion).toHaveBeenCalledTimes(1)
    finish()
    await vi.advanceTimersByTimeAsync(14999)
    report(waiting)
    stage.tick()
    expect(adapter.startMotion).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(1)
    stage.tick()
    await vi.advanceTimersByTimeAsync(0)
    expect(adapter.startMotion).toHaveBeenCalledTimes(2)
    await vi.advanceTimersByTimeAsync(21000)
    stage.tick()
    expect(adapter.startMotion).toHaveBeenCalledTimes(2)
    finish()
    stage.tick()
    await vi.advanceTimersByTimeAsync(0)
    expect(adapter.startMotion).toHaveBeenCalledTimes(3)
    const oldFinish = finish
    report({
      current: {
        guideId: 'reply',
        guide: {
          slotId: 'role',
          text: '你好',
          audio: null,
          resolvedPerformance: { motion: { member: 'next.motion3.json' } }
        },
        elapsedMs: 0,
        durationMs: 20000
      }
    })
    await vi.advanceTimersByTimeAsync(0)
    expect(unsubscribe).toHaveBeenCalledOnce()
    oldFinish()
    await vi.advanceTimersByTimeAsync(16000)
    stage.tick()
    expect(adapter.startMotion.mock.calls.map(([, index]) => index)).toEqual([0, 0, 0, 1])
  } finally {
    stage.dispose()
  }
})
