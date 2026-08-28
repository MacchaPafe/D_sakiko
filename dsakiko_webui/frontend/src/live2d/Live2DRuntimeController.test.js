import { beforeEach, describe, expect, it, vi } from 'vitest'

const { fromMock } = vi.hoisted(() => ({ fromMock: vi.fn() }))

vi.mock('pixi-live2d-display', () => ({
  Live2DModel: { from: fromMock },
  MotionPriority: { IDLE: 1, NORMAL: 2, FORCE: 3 },
}))

import { Live2DRuntimeController } from './Live2DRuntimeController'

function deferred() {
  let resolve
  let reject
  const promise = new Promise((resolvePromise, rejectPromise) => {
    resolve = resolvePromise
    reject = rejectPromise
  })
  return { promise, resolve, reject }
}

function fakeModel(name) {
  const listeners = new Map()
  return {
    name,
    width: 100,
    height: 200,
    anchor: { set: vi.fn() },
    scale: { set: vi.fn() },
    position: { set: vi.fn() },
    motion: vi.fn().mockResolvedValue(true),
    expression: vi.fn().mockResolvedValue(true),
    destroy: vi.fn(),
    internalModel: {
      coreModel: { setParamFloat: vi.fn(), setParameterValueById: vi.fn() },
      motionManager: {
        stopAllMotions: vi.fn(),
        expressionManager: { resetExpression: vi.fn() },
        once: vi.fn((event, callback) => listeners.set(event, callback)),
        off: vi.fn((event) => listeners.delete(event)),
      },
    },
  }
}

function presentation(targetId, version = 'v2', revision = 'one') {
  return {
    resolution: 'resolved',
    target_id: targetId,
    revision,
    version,
    model_url: `/models/${targetId}.json`,
    layout: { scale: 1, offset_x: 0, offset_y: 0 },
    capabilities: {
      motion_files_by_group: {
        idle_motion: ['idle.motion.json'],
        change_character: ['change.motion.json'],
        happiness: ['happy.motion.json'],
      },
      expressions_by_motion: {},
      semantic_expressions: {},
    },
  }
}

function createController() {
  const stage = {
    addChild: vi.fn(),
    removeChild: vi.fn(),
  }
  const app = {
    stage,
    ticker: { start: vi.fn(), stop: vi.fn() },
  }
  const onStatusChange = vi.fn()
  const controller = new Live2DRuntimeController({
    app,
    onStatusChange,
    onModelChange: vi.fn(),
  })
  return { controller, stage, onStatusChange }
}

describe('Live2DRuntimeController', () => {
  beforeEach(() => {
    fromMock.mockReset()
  })

  it('keeps only the latest asynchronously loaded model', async () => {
    const firstLoad = deferred()
    const secondLoad = deferred()
    const firstModel = fakeModel('first')
    const secondModel = fakeModel('second')
    fromMock.mockReturnValueOnce(firstLoad.promise).mockReturnValueOnce(secondLoad.promise)
    const { controller, stage } = createController()

    const firstRequest = controller.setPresentation(presentation('first'))
    const secondRequest = controller.setPresentation(presentation('second'))
    secondLoad.resolve(secondModel)
    await secondRequest
    firstLoad.resolve(firstModel)
    await firstRequest

    expect(controller.adapter.model).toBe(secondModel)
    expect(firstModel.destroy).toHaveBeenCalled()
    expect(stage.addChild).toHaveBeenCalledTimes(1)
  })

  it('does not reload an unchanged target and revision', async () => {
    fromMock.mockResolvedValue(fakeModel('same'))
    const { controller } = createController()
    const current = presentation('same')

    await controller.setPresentation(current)
    await controller.setPresentation({ ...current })

    expect(fromMock).toHaveBeenCalledTimes(1)
  })

  it('removes the old model after the selected replacement fails', async () => {
    const oldModel = fakeModel('old')
    fromMock.mockResolvedValueOnce(oldModel).mockRejectedValueOnce(new Error('invalid moc'))
    const { controller, onStatusChange } = createController()
    await controller.setPresentation(presentation('old'))

    await controller.setPresentation(presentation('new'), {
      reason: 'semantic_target_change',
    })

    expect(oldModel.destroy).toHaveBeenCalled()
    expect(controller.adapter).toBeNull()
    expect(onStatusChange).toHaveBeenLastCalledWith(expect.objectContaining({
      status: 'error',
      retryable: true,
    }))
  })

  it('skips the entry motion when speech preempts model readiness', async () => {
    const load = deferred()
    const model = fakeModel('speaking')
    fromMock.mockReturnValue(load.promise)
    const { controller } = createController()
    const loading = controller.setPresentation(presentation('costume'), {
      reason: 'semantic_target_change',
    })
    await controller.setCue({
      kind: 'speaking',
      key: 'message:1',
      emotion: 'happiness',
    })

    load.resolve(model)
    await loading

    expect(model.motion).toHaveBeenCalledWith('happiness', 0, 3)
    expect(model.motion).not.toHaveBeenCalledWith('change_character', 0, 3)
  })

  it('reloads the same target when its revision changes without entry motion', async () => {
    const firstModel = fakeModel('first revision')
    const secondModel = fakeModel('second revision')
    fromMock.mockResolvedValueOnce(firstModel).mockResolvedValueOnce(secondModel)
    const { controller } = createController()
    await controller.setPresentation(presentation('same', 'v3', 'one'))

    await controller.setPresentation(presentation('same', 'v3', 'two'), {
      reason: 'semantic_target_change',
    })

    expect(fromMock).toHaveBeenCalledTimes(2)
    expect(secondModel.motion).toHaveBeenCalledWith('idle_motion', 0, 3)
    expect(secondModel.motion).not.toHaveBeenCalledWith('change_character', 0, 3)
  })

  it('applies the absolute desktop layout values to the fitted model', async () => {
    const model = fakeModel('layout')
    fromMock.mockResolvedValue(model)
    const { controller } = createController()
    const current = presentation('layout', 'v3')
    current.layout = { scale: 2.3, offset_x: 0, offset_y: -0.77 }
    await controller.setPresentation(current)

    controller.fit(100, 100)

    expect(model.scale.set.mock.lastCall[0]).toBeCloseTo(1.495)
    expect(model.position.set).toHaveBeenLastCalledWith(50, 88.5)
  })
})
