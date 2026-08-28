import { describe, expect, it, vi } from 'vitest'
import { createRuntimeAdapter } from './runtimeAdapters'

function fakeModel(coreModel, breath) {
  return {
    internalModel: {
      coreModel,
      breath,
      motionManager: {
        stopAllMotions: vi.fn(),
      },
    },
    destroy: vi.fn(),
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
