/** 复制自旧 WebUI 的 SDK 差异适配；每个实例只拥有一个模型。 */
export class Live2DRuntimeAdapter {
  constructor(model, version) {
    this.model = model
    this.version = version
    this.mouthOpen = 0
    this.eyeOpenOverride = null
    this.beforeModelUpdate = () => this.applyParameterOverrides()
    this.model.internalModel?.on?.('beforeModelUpdate', this.beforeModelUpdate)
    if (version === 'v3') {
      this.enableParameterOnlyAutoBreath()
      this.enableSmoothCrossFade()
    }
  }

  enableSmoothCrossFade() {
    const manager = this.model.internalModel?.motionManager
    const queue = manager?.queueManager
    if (!manager || !queue || typeof queue.startMotion !== 'function') return
    // pixi-live2d-display 默认在 _startMotion 中调用 stopAllMotions 清空旧动作，
    // 这会使每帧 saveParameters 在无旧动作权重托底时指数加速叠加新动作，导致淡入变成瞬切；
    // 这里改回 Cubism 原生队列交叉淡入淡出，并把待淡出旧动作时长对齐新动作的淡入时长。
    manager._startMotion = (motion, onFinish) => {
      motion?.setFinishedMotionHandler?.(onFinish)
      const list = Array.isArray(queue._motions) ? queue._motions : []
      for (let i = list.length - 1; i >= 0; i--) {
        if (list[i]?._motion === motion) {
          list[i].release?.()
          list.splice(i, 1)
        }
      }
      const existing = list.filter((entry) => entry && !entry.isTriggeredFadeOut?.())
      const handle = queue.startMotion(motion, false, performance.now())
      const incomingFade = motion?.getFadeInTime?.()
      if (Number.isFinite(incomingFade) && incomingFade > 0) {
        for (const entry of existing) {
          const currentFade = entry._motion?.getFadeOutTime?.()
          const targetFade =
            Number.isFinite(currentFade) && currentFade > 0
              ? Math.min(currentFade, incomingFade)
              : incomingFade
          entry.setFadeOut?.(targetFade)
          entry._motion?.setFadeOutTime?.(targetFade)
        }
      }
      return handle
    }
  }

  enableParameterOnlyAutoBreath() {
    const internalModel = this.model.internalModel
    const breath = internalModel?.breath
    if (typeof breath?.setParameters !== 'function') return
    breath.setParameters([
      {
        parameterId: internalModel.idParamBreath || 'ParamBreath',
        offset: 0.5,
        peak: 0.5,
        cycle: 3.2345,
        weight: 0.5
      }
    ])
  }

  async startMotion(group, index, priority, fadeInMs = 0, single = false, isCurrent = () => true) {
    if (fadeInMs > 0 || single) {
      const motion = await this.model.internalModel?.motionManager?.loadMotion?.(group, index)
      if (!isCurrent()) return false
      if (single && this.version === 'v3') motion?.setIsLoop?.(false)
      if (fadeInMs > 0) {
        if (this.version === 'v3') {
          const seconds = fadeInMs / 1000
          motion?.setFadeInTime?.(seconds)
          motion?.setFadeOutTime?.(seconds)
        } else {
          motion?.setFadeIn?.(fadeInMs)
        }
      }
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

  isPlayingMotion(group, index) {
    const state = this.model.internalModel?.motionManager?.state
    return state?.currentGroup === group && state.currentIndex === index
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
        right: coreModel?.getParameterValueById?.('ParamEyeROpen') ?? 1
      }
    }
    return {
      left: coreModel?.getParamFloat?.('PARAM_EYE_L_OPEN') ?? 1,
      right: coreModel?.getParamFloat?.('PARAM_EYE_R_OPEN') ?? 1
    }
  }

  setEyeOpenOverride(left, right) {
    this.eyeOpenOverride = {
      left: Math.max(0, Math.min(1, left)),
      right: Math.max(0, Math.min(1, right))
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
      baseTexture: true
    })
  }
}

/**
 * 为两代 SDK 提供共同的参数和动作操作。
 * @param {object} model Pixi Live2D 模型实例。
 * @param {'v2'|'v3'} version 已读取模型 JSON 的版本。
 * @returns {Live2DRuntimeAdapter} 独占该模型的适配器。
 */
export function createRuntimeAdapter(model, version) {
  if (version !== 'v2' && version !== 'v3') {
    throw new Error(`不支持的 Live2D 版本：${version}`)
  }
  return new Live2DRuntimeAdapter(model, version)
}
