import { afterEach, expect, test, vi } from 'vitest'
import { resolveTheatreStageState } from '../../src/renderer/src/features/home/theatre-stage.js'

vi.mock('pixi.js', () => ({ Application: class {}, RenderTexture: {}, Ticker: {} }))
vi.mock('pixi-live2d-display', () => ({
  Live2DModel: { registerTicker() {} },
  MotionPriority: { FORCE: 3 }
}))
vi.mock('../../src/renderer/src/performance/live2d-preview.js', () => ({
  Live2DPreview: class {
    constructor() {
      this.app = { ticker: { add() {} } }
    }
    setVisible() {}
    fit() {}
    clear() {}
    destroy() {}
  }
}))

const { StageController } = await import('../../src/renderer/src/features/home/stage-controller.js')

const actors = [
  { id: 'sakiko', displayName: '祥子' },
  { id: 'anon', displayName: '爱音' }
]

afterEach(() => {
  vi.unstubAllGlobals()
})

test('resolveTheatreStageState 在首帧未就绪与双方支持时准确决定朝向页签', () => {
  expect(
    resolveTheatreStageState({
      actors,
      sides: null,
      orientation: 'face-to-face',
      requestedPanel: 'position'
    })
  ).toEqual({
    canFace: false,
    inward: false,
    panel: 'position',
    degradedNote: ''
  })

  expect(
    resolveTheatreStageState({
      actors,
      sides: [undefined, undefined],
      orientation: 'face-to-face',
      requestedPanel: 'position'
    })
  ).toEqual({
    canFace: false,
    inward: false,
    panel: 'position',
    degradedNote: ''
  })

  expect(
    resolveTheatreStageState({
      actors,
      sides: [true, true],
      orientation: 'screen',
      requestedPanel: 'orientation'
    })
  ).toEqual({
    canFace: true,
    inward: false,
    panel: 'orientation',
    degradedNote: ''
  })

  expect(
    resolveTheatreStageState({
      actors,
      sides: [true, true],
      orientation: 'face-to-face',
      requestedPanel: 'orientation'
    })
  ).toEqual({
    canFace: true,
    inward: true,
    panel: 'orientation',
    degradedNote: ''
  })
})

test('resolveTheatreStageState 在不支持面对面时隐藏朝向并按保存偏好输出降级说明', () => {
  expect(
    resolveTheatreStageState({
      actors,
      sides: [false, true],
      orientation: 'screen',
      requestedPanel: 'orientation'
    })
  ).toEqual({
    canFace: false,
    inward: false,
    panel: 'position',
    degradedNote: ''
  })

  expect(
    resolveTheatreStageState({
      actors,
      sides: [false, true],
      orientation: 'face-to-face',
      requestedPanel: 'orientation'
    })
  ).toEqual({
    canFace: false,
    inward: false,
    panel: 'position',
    degradedNote: '祥子的服装不支持面对面，已暂改为面向屏幕'
  })

  expect(
    resolveTheatreStageState({
      actors,
      sides: [false, false],
      orientation: 'face-to-face',
      requestedPanel: 'position'
    })
  ).toEqual({
    canFace: false,
    inward: false,
    panel: 'position',
    degradedNote: '祥子、爱音的服装不支持面对面，已暂改为面向屏幕'
  })

  expect(
    resolveTheatreStageState({
      actors,
      sides: [false, true],
      orientation: 'face-to-face',
      requestedPanel: null
    })
  ).toEqual({
    canFace: false,
    inward: false,
    panel: null,
    degradedNote: '祥子的服装不支持面对面，已暂改为面向屏幕'
  })
})

test('StageController.select 切换小剧场时清空旧的 sides 能力缓存', async () => {
  vi.stubGlobal('document', {
    createElement: () => ({ className: '', remove() {} })
  })
  const element = {
    appendChild() {},
    classList: { toggle() {} },
    addEventListener() {},
    removeEventListener() {}
  }
  const listeners = new Map()
  const stage = new StageController(
    {
      performance: {
        observe(sequenceId, callback) {
          listeners.set(sequenceId, callback)
          return () => listeners.delete(sequenceId)
        }
      }
    },
    () => {}
  )
  stage.mount(element)

  try {
    await stage.select(null, 'chat-1', 'seq-1', undefined, true)
    listeners.get('seq-1')({
      scene: {
        slots: [
          { id: 'sakiko', supportsInward: true, presentation: { model: { id: 'm1' } } },
          { id: 'anon', supportsInward: true, presentation: { model: { id: 'm2' } } }
        ]
      }
    })
    expect(stage.state.sides).toEqual([true, true])

    await stage.select(null, 'chat-2', 'seq-2', undefined, true)
    expect(stage.state.sides).toBeNull()

    listeners.get('seq-2')({
      scene: {
        slots: [
          { id: 'sakiko', supportsInward: false, presentation: { model: { id: 'm3' } } },
          { id: 'anon', supportsInward: true, presentation: { model: { id: 'm2' } } }
        ]
      }
    })
    expect(stage.state.sides).toEqual([false, true])
  } finally {
    stage.dispose()
  }
})
