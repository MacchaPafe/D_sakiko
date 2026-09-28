export class Live2DRuntimeAdapter {
  constructor(model, version) {
    this.model = model
    this.version = version
    this.mouthOpen = 0
    this.eyeOpenOverride = null
    this.beforeModelUpdate = () => this.applyParameterOverrides()
    this.model.internalModel?.on?.('beforeModelUpdate', this.beforeModelUpdate)
    if (version === 'v3') this.enableParameterOnlyAutoBreath()
  }

  enableParameterOnlyAutoBreath() {
    const internalModel = this.model.internalModel
    const breath = internalModel?.breath
    if (typeof breath?.setParameters !== 'function') return
    breath.setParameters([{
      parameterId: internalModel.idParamBreath || 'ParamBreath',
      offset: 0.5,
      peak: 0.5,
      cycle: 3.2345,
      weight: 0.5,
    }])
  }

  async startMotion(group, index, priority, fadeInMs = 0, single = false, isCurrent = () => true) {
    if (fadeInMs > 0 || single) {
      const motion = await this.model.internalModel?.motionManager?.loadMotion?.(group, index)
      if (!isCurrent()) return false
      if (single && this.version === 'v3') motion?.setIsLoop?.(false)
      if (this.version === 'v3') motion?.setFadeInTime?.(fadeInMs / 1000)
      else motion?.setFadeIn?.(fadeInMs)
    }
    return this.model.motion(group, index, priority)
  }

  async setExpression(expressionId, isCurrent = () => true) {
    if (!expressionId) {
      this.resetExpression()
      return false
    }
    const manager = this.model.internalModel?.motionManager?.expressionManager
    const index = manager?.getExpressionIndex?.(expressionId)
    if (Number.isInteger(index) && index >= 0) {
      await manager.loadExpression(index)
      if (!isCurrent()) return false
      if (manager.expressions?.[index] === manager.currentExpression) return true
    }
    if (!isCurrent()) return false
    return this.model.expression(expressionId)
  }

  setPerformanceExpression(enabled) {
    const internal = this.model.internalModel
    if (!internal || this.version !== 'v3') return
    if (enabled && internal.eyeBlink) {
      this.savedEyeBlink = internal.eyeBlink
      internal.eyeBlink = null
    } else if (!enabled && this.savedEyeBlink) {
      internal.eyeBlink = this.savedEyeBlink
      this.savedEyeBlink = null
    }
  }

  resetExpression() {
    this.model.internalModel?.motionManager?.expressionManager?.resetExpression?.()
  }

  stopMotions() {
    this.model.internalModel?.motionManager?.stopAllMotions?.()
  }

  onceMotionFinish(callback) {
    const motionManager = this.model.internalModel?.motionManager
    if (!motionManager?.once) return () => {}
    motionManager.once('motionFinish', callback)
    return () => motionManager.off?.('motionFinish', callback)
  }

  setMouthOpen(value) {
    this.mouthOpen = Math.max(0, Math.min(1, value))
    this.applyMouthOpen()
  }

  applyMouthOpen() {
    if (this.preserveExpressionMouth) return
    const coreModel = this.model.internalModel?.coreModel
    if (this.version === 'v3') {
      coreModel?.setParameterValueById?.('ParamMouthOpenY', this.mouthOpen)
      return
    }
    coreModel?.setParamFloat?.('PARAM_MOUTH_OPEN_Y', this.mouthOpen)
  }

  getEyeOpen() {
    const coreModel = this.model.internalModel?.coreModel
    if (this.version === 'v3') {
      return {
        left: coreModel?.getParameterValueById?.('ParamEyeLOpen') ?? 1,
        right: coreModel?.getParameterValueById?.('ParamEyeROpen') ?? 1,
      }
    }
    return {
      left: coreModel?.getParamFloat?.('PARAM_EYE_L_OPEN') ?? 1,
      right: coreModel?.getParamFloat?.('PARAM_EYE_R_OPEN') ?? 1,
    }
  }

  setEyeOpenOverride(left, right) {
    this.eyeOpenOverride = {
      left: Math.max(0, Math.min(1, left)),
      right: Math.max(0, Math.min(1, right)),
    }
    this.applyEyeOpenOverride()
  }

  clearEyeOpenOverride() {
    this.eyeOpenOverride = null
  }

  applyEyeOpenOverride() {
    if (!this.eyeOpenOverride) return
    const coreModel = this.model.internalModel?.coreModel
    if (this.version === 'v3') {
      coreModel?.setParameterValueById?.('ParamEyeLOpen', this.eyeOpenOverride.left)
      coreModel?.setParameterValueById?.('ParamEyeROpen', this.eyeOpenOverride.right)
      return
    }
    coreModel?.setParamFloat?.('PARAM_EYE_L_OPEN', this.eyeOpenOverride.left)
    coreModel?.setParamFloat?.('PARAM_EYE_R_OPEN', this.eyeOpenOverride.right)
  }

  applyParameterOverrides() {
    this.applyMouthOpen()
    this.applyEyeOpenOverride()
  }

  setSpeechActive(active) {
    this.preserveExpressionMouth = this.version === 'v3' && !active
  }

  destroy() {
    this.setMouthOpen(0)
    this.model.internalModel?.off?.('beforeModelUpdate', this.beforeModelUpdate)
    this.stopMotions()
    this.model.destroy({
      children: true,
      texture: true,
      baseTexture: true,
    })
  }
}

export function createRuntimeAdapter(model, version) {
  if (version !== 'v2' && version !== 'v3') {
    throw new Error(`不支持的 Live2D 版本：${version}`)
  }
  return new Live2DRuntimeAdapter(model, version)
}
