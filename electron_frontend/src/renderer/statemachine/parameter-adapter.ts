/**
 * Tiny Cubism parameter compatibility boundary for the Electron runtime.
 *
 * pixi-live2d-display exposes Cubism 2 and Cubism 3/4 core models with
 * different method names.  Keeping the difference here means the FSM and its
 * audio/gesture code only deal in parameter ids and values.
 */
export interface ParameterAdapter {
  index(name: string): number
  getByIndex(index: number): number | null
  setByIndex(index: number, value: number): boolean
  get(name: string): number | null
  set(name: string, value: number): boolean
}

export function createParameterAdapter(model: any): ParameterAdapter {
  const core = model?.internalModel?.coreModel ?? model?.coreModel ?? model
  const v2 = typeof core?.getParamIndex === 'function'
    && typeof core?.getParamFloat === 'function'
    && typeof core?.setParamFloat === 'function'
  const v3 = typeof core?.getParameterIndex === 'function'
    && typeof core?.getParameterValueByIndex === 'function'
    && typeof core?.setParameterValueByIndex === 'function'

  const v3ParameterCount = (): number => {
    try {
      const count = core?.getParameterCount?.()
      return Number.isInteger(count) && count >= 0 ? Number(count) : -1
    } catch (_) {
      return -1
    }
  }

  const parameterNames = (name: string): string[] => {
    const names = [name]
    // Standard Cubism 3/4 ids are commonly camel-cased (ParamMouthOpenY),
    // while older Cubism 2 packs use PARAM_MOUTH_OPEN_Y.
    if (name.startsWith('PARAM_')) {
      const camel = name.slice(6).toLowerCase().split('_').map((part, index) =>
        index === 0 ? part.charAt(0).toUpperCase() + part.slice(1) : part.charAt(0).toUpperCase() + part.slice(1),
      ).join('')
      names.push(`Param${camel}`)
    }
    return names
  }

  const index = (name: string): number => {
    for (const candidate of parameterNames(name)) {
      try {
        const value = v2
          ? core.getParamIndex(candidate)
          : v3
            ? core.getParameterIndex(candidate)
            : -1
        if (v2 && Number.isFinite(value) && Number(value) >= 0) return Number(value)
        if (v3) {
          const candidateIndex = Number(value)
          const parameterCount = v3ParameterCount()
          // Cubism 4 may return a non-negative virtual slot for an unknown id.
          // Only indices inside the model's real parameter array are usable.
          if (Number.isInteger(candidateIndex) && candidateIndex >= 0
            && parameterCount >= 0 && candidateIndex < parameterCount) return candidateIndex
        }
      } catch (_) { /* try the next spelling */ }
    }
    return -1
  }

  const getByIndex = (parameterIndex: number): number | null => {
    if (!Number.isInteger(parameterIndex) || parameterIndex < 0) return null
    try {
      const value = v2
        ? core.getParamFloat(parameterIndex)
        : v3
          ? core.getParameterValueByIndex(parameterIndex)
          : null
      return typeof value === 'number' && Number.isFinite(value) ? value : null
    } catch (_) {
      return null
    }
  }

  const setByIndex = (parameterIndex: number, value: number): boolean => {
    if (!Number.isInteger(parameterIndex) || parameterIndex < 0 || !Number.isFinite(value)) return false
    try {
      if (v2) core.setParamFloat(parameterIndex, value)
      else if (v3) core.setParameterValueByIndex(parameterIndex, value)
      else return false
      return true
    } catch (_) {
      return false
    }
  }

  return {
    index,
    getByIndex,
    setByIndex,
    get: name => getByIndex(index(name)),
    set: (name, value) => setByIndex(index(name), value),
  }
}
