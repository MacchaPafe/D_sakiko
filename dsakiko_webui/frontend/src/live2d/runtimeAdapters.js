export class Live2DRuntimeAdapter {
  constructor(model, version) {
    this.model = model
    this.version = version
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

  async startMotion(group, index, priority) {
    return this.model.motion(group, index, priority)
  }

  async setExpression(expressionId) {
    if (!expressionId) {
      this.resetExpression()
      return false
    }
    return this.model.expression(expressionId)
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
    const clamped = Math.max(0, Math.min(1, value))
    const coreModel = this.model.internalModel?.coreModel
    if (this.version === 'v3') {
      coreModel?.setParameterValueById?.('ParamMouthOpenY', clamped)
      return
    }
    coreModel?.setParamFloat?.('PARAM_MOUTH_OPEN_Y', clamped)
  }

  destroy() {
    this.setMouthOpen(0)
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
