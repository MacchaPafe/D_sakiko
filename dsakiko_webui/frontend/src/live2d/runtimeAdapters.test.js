import { describe, expect, it, vi } from 'vitest'
import { createRuntimeAdapter } from './runtimeAdapters'

function fakeModel(coreModel, breath) {
  const listeners = new Map()
  return {
    internalModel: {
      coreModel,
      breath,
      on: vi.fn((event, callback) => listeners.set(event, callback)),
      off: vi.fn((event) => listeners.delete(event)),
      motionManager: {
        stopAllMotions: vi.fn(),
      },
    },
    destroy: vi.fn(),
    emitBeforeModelUpdate: () => listeners.get('beforeModelUpdate')?.(),
  }
}

describe('Live2D runtime adapters', () => {
  it('writes the Cubism 2 mouth parameter', () => {
    const coreModel = { setParamFloat: vi.fn() }
    createRuntimeAdapter(fakeModel(coreModel), 'v2').setMouthOpen(0.7)
    expect(coreModel.setParamFloat).toHaveBeenCalledWith('PARAM_MOUTH_OPEN_Y', 0.7)
  })

  it('writes the Cubism 3+ mouth parameter', () => {
    const coreModel = { setParameterValueById: vi.fn() }
    createRuntimeAdapter(fakeModel(coreModel), 'v3').setMouthOpen(0.8)
    expect(coreModel.setParameterValueById).toHaveBeenCalledWith('ParamMouthOpenY', 0.8)
  })

  it('uses the runtime fade-in support when returning to idle', async () => {
    const motion = { setFadeIn: vi.fn() }
    const model = fakeModel({})
    model.motion = vi.fn().mockResolvedValue(true)
    model.internalModel.motionManager.loadMotion = vi.fn().mockResolvedValue(motion)
    const adapter = createRuntimeAdapter(model, 'v2')

    await adapter.startMotion('idle_motion', 0, 3, 1_500)

    expect(motion.setFadeIn).toHaveBeenCalledWith(1_500)
    expect(model.motion).toHaveBeenCalledWith('idle_motion', 0, 3)
  })

  it('converts the idle fade-in duration to seconds for Cubism 3+', async () => {
    const motion = { setFadeInTime: vi.fn() }
    const model = fakeModel({})
    model.motion = vi.fn().mockResolvedValue(true)
    model.internalModel.motionManager.loadMotion = vi.fn().mockResolvedValue(motion)
    const adapter = createRuntimeAdapter(model, 'v3')

    await adapter.startMotion('Idle', 0, 3, 1_500)

    expect(motion.setFadeInTime).toHaveBeenCalledWith(1.5)
  })

  it('reapplies mouth openness immediately before the model update', () => {
    const coreModel = { setParamFloat: vi.fn() }
    const model = fakeModel(coreModel)
    const adapter = createRuntimeAdapter(model, 'v2')
    adapter.setMouthOpen(0.65)
    coreModel.setParamFloat.mockClear()

    model.emitBeforeModelUpdate()

    expect(coreModel.setParamFloat).toHaveBeenCalledWith('PARAM_MOUTH_OPEN_Y', 0.65)
  })

  it('reads and overrides Cubism 2 eye parameters', () => {
    const values = {
      PARAM_EYE_L_OPEN: 0.2,
      PARAM_EYE_R_OPEN: 0.4,
    }
    const coreModel = {
      getParamFloat: vi.fn((id) => values[id]),
      setParamFloat: vi.fn(),
    }
    const adapter = createRuntimeAdapter(fakeModel(coreModel), 'v2')

    expect(adapter.getEyeOpen()).toEqual({ left: 0.2, right: 0.4 })
    adapter.setEyeOpenOverride(0.6, 0.7)

    expect(coreModel.setParamFloat).toHaveBeenCalledWith('PARAM_EYE_L_OPEN', 0.6)
    expect(coreModel.setParamFloat).toHaveBeenCalledWith('PARAM_EYE_R_OPEN', 0.7)
  })

  it('keeps only ParamBreath in the Cubism 3+ automatic breath', () => {
    const breath = { setParameters: vi.fn() }
    const model = fakeModel({}, breath)
    model.internalModel.idParamBreath = 'ResolvedParamBreath'

    createRuntimeAdapter(model, 'v3')

    expect(breath.setParameters).toHaveBeenCalledWith([{
      parameterId: 'ResolvedParamBreath',
      offset: 0.5,
      peak: 0.5,
      cycle: 3.2345,
      weight: 0.5,
    }])
  })

  it('does not change the Cubism 2 runtime breath state', () => {
    const breath = { setParameters: vi.fn() }

    createRuntimeAdapter(fakeModel({}, breath), 'v2')

    expect(breath.setParameters).not.toHaveBeenCalled()
  })

  it('rejects an unknown runtime version', () => {
    expect(() => createRuntimeAdapter(fakeModel({}), 'v6')).toThrow('不支持的 Live2D 版本')
  })
})
