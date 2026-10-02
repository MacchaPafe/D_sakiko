import { Live2DModel, MotionPriority } from 'pixi-live2d-display'
import { createRuntimeAdapter } from './runtimeAdapters'
import {
  selectMotion,
  selectSemanticExpression,
  resolvePerformance,
} from './cuePolicy'

const THINKING_REPEAT_MS = 15_000
const IDLE_RECOVERY_DELAY_MS = 2_500
const IDLE_FADE_IN_MS = 1_500
const SPEAKING_FADE_IN_MS = 1_000
const IDLE_REPEAT_MS = 25_000
const LONG_AUDIO_THRESHOLD_SECONDS = 6
const LONG_AUDIO_REPEAT_DELAY_MS = 2_500
const LONG_AUDIO_MAX_REPEATS = 2
const EYE_OPEN_TRANSITION_MS = 100
const MODEL_LOAD_TIMEOUT_MS = 30_000

function presentationKey(presentation) {
  if (!presentation?.target_id) return null
  return `${presentation.target_id}:${presentation.revision || ''}:${presentation.current_form || ''}`
}

function errorMessage(error) {
  return error instanceof Error ? error.message : String(error)
}

function isTransientLoadError(error) {
  return /network|fetch|load failed|http\s*5\d\d|status\s*5\d\d/i.test(errorMessage(error))
}

async function loadModelWithTimeout(modelUrl) {
  let timedOut = false
  let timeoutId
  const modelPromise = Live2DModel.from(modelUrl, {
    autoInteract: false,
  })
  modelPromise.then((model) => {
    if (timedOut) {
      model.destroy({ children: true, texture: true, baseTexture: true })
    }
  }).catch(() => {})

  const timeoutPromise = new Promise((_, reject) => {
    timeoutId = window.setTimeout(() => {
      timedOut = true
      reject(new Error(`Live2D 模型加载超过 ${MODEL_LOAD_TIMEOUT_MS / 1000} 秒`))
    }, MODEL_LOAD_TIMEOUT_MS)
  })

  try {
    return await Promise.race([modelPromise, timeoutPromise])
  } finally {
    window.clearTimeout(timeoutId)
  }
}

export class Live2DRuntimeController {
  constructor({ app, onStatusChange, onModelChange }) {
    this.app = app
    this.onStatusChange = onStatusChange
    this.onModelChange = onModelChange
    this.adapter = null
    this.presentation = null
    this.requestedPresentation = null
    this.cue = { kind: 'idle', key: 'idle' }
    this.generation = 0
    this.cueGeneration = 0
    this.motionTimer = null
    this.autoRetryTimer = null
    this.removeMotionFinishListener = null
    this.activeMotion = null
    this.pendingIdleCue = null
    this.eyeTransition = null
    this.destroyed = false
  }

  setActive(active) {
    if (active) this.app.ticker.start()
    else this.app.ticker.stop()
  }

  setMouthOpen(value) {
    this.adapter?.setMouthOpen(value)
  }

  updateFrame(mouthOpen, now = Date.now()) {
    this.adapter?.setSpeechActive?.(!this.performanceState || (this.cue?.kind === 'speaking' && !this.cue.silent))
    this.setMouthOpen(mouthOpen)
    if (!this.adapter || !this.eyeTransition) return
    if (this.eyeTransition.complete) {
      this.adapter.clearEyeOpenOverride()
      this.eyeTransition = null
      return
    }
    const progress = Math.max(
      0,
      Math.min(1, (now - this.eyeTransition.startedAt) / EYE_OPEN_TRANSITION_MS),
    )
    const left = this.eyeTransition.left + (1 - this.eyeTransition.left) * progress
    const right = this.eyeTransition.right + (1 - this.eyeTransition.right) * progress
    this.adapter.setEyeOpenOverride(left, right)
    if (progress >= 1) this.eyeTransition.complete = true
  }

  async setPresentation(presentation, options = {}) {
    const { reason = 'snapshot', force = false, retry = false, autoRetry = false } = options
    this.requestedPresentation = presentation
    clearTimeout(this.autoRetryTimer)

    if (!presentation || presentation.resolution === 'absent') {
      this.generation += 1
      this.replaceAdapter(null)
      this.presentation = presentation || null
      this.onStatusChange({ status: 'absent', error: '', retryable: false })
      return
    }
    if (presentation.resolution === 'configured_error') {
      this.generation += 1
      this.replaceAdapter(null)
      this.presentation = presentation
      this.onStatusChange({
        status: 'error',
        error: presentation.error?.message || '当前 Live2D 模型配置不可用',
        retryable: true,
      })
      return
    }

    const nextKey = presentationKey(presentation)
    if (!force && nextKey && nextKey === presentationKey(this.presentation) && this.adapter) {
      this.presentation = presentation
      return
    }

    const previousPresentation = this.presentation
    const semanticTargetChanged = (
      previousPresentation?.target_id
      && previousPresentation.target_id !== presentation.target_id
    )
    const generation = ++this.generation
    this.onStatusChange({ status: 'loading', error: '', retryable: false })

    try {
      console.info('Live2D model loading started', {
        version: presentation.version,
        targetId: presentation.target_id,
        modelUrl: presentation.model_url,
      })
      const model = await loadModelWithTimeout(presentation.model_url)
      if (this.destroyed || generation !== this.generation) {
        model.destroy({ children: true, texture: true, baseTexture: true })
        return
      }

      model.anchor.set(0.5, 0.5)
      const adapter = createRuntimeAdapter(model, presentation.version)
      this.replaceAdapter(adapter)
      this.presentation = presentation
      this.onModelChange()
      this.onStatusChange({ status: 'ready', error: '', retryable: false })

      const shouldPlayEntry = (
        semanticTargetChanged
        && reason === 'semantic_target_change'
        && !retry
        && !autoRetry
        && this.cue.kind !== 'speaking'
      )
      if (shouldPlayEntry) {
        await this.applyCue({
          kind: 'change_character',
          key: `change:${nextKey}`,
        }, true)
      } else {
        await this.applyCue(this.cue, true)
      }
    } catch (error) {
      if (this.destroyed || generation !== this.generation) return
      console.error('Live2D model loading failed', {
        version: presentation.version,
        targetId: presentation.target_id,
        modelUrl: presentation.model_url,
        error,
      })
      this.replaceAdapter(null)
      this.presentation = presentation
      if (!autoRetry && isTransientLoadError(error)) {
        this.onStatusChange({
          status: 'loading',
          error: '角色资源加载失败，正在重试',
          retryable: false,
        })
        this.autoRetryTimer = setTimeout(() => {
          this.setPresentation(presentation, {
            reason,
            force: true,
            retry: true,
            autoRetry: true,
          })
        }, 250)
        return
      }
      this.onStatusChange({
        status: 'error',
        error: errorMessage(error),
        retryable: true,
      })
    }
  }

  async retry() {
    if (!this.requestedPresentation) return
    await this.setPresentation(this.requestedPresentation, {
      reason: 'retry',
      force: true,
      retry: true,
    })
  }

  async playMaskAction(request) {
    if (!this.adapter || this.destroyed || request.target_id !== this.presentation?.target_id
      || request.current_form !== 'black' || this.presentation?.current_form !== 'black'
      || this.cue?.kind === 'speaking' || this.cue?.kind === 'thinking') return false
    const adapter = this.adapter
    const generation = ++this.cueGeneration
    clearTimeout(this.motionTimer)
    this.motionTimer = null
    this.pendingIdleCue = null
    this.removeMotionFinishListener?.()
    this.removeMotionFinishListener = null
    const isCurrent = () => this.adapter === adapter && generation === this.cueGeneration && !this.destroyed
    const started = await adapter.startMotion('__dsakiko_mask__', request.index, MotionPriority.FORCE, 0, true, isCurrent).catch(() => false)
    if (started && isCurrent()) {
      this.activeMotion = { kind: 'mask', key: request.id, startedAt: Date.now(), finished: false, finishedAt: 0 }
      this.removeMotionFinishListener = adapter.onceMotionFinish(() => {
        if (isCurrent()) {
          this.activeMotion.finished = true
          this.activeMotion.finishedAt = Date.now()
          this.scheduleIdleRecovery(this.cue, Date.now())
        }
      })
    }
    return started
  }

  async setCue(cue) {
    if (!cue) return
    const unchanged = this.cue?.kind === cue.kind && this.cue?.key === cue.key
    this.cue = cue
    if (unchanged) return
    if (cue.kind === 'thinking' && this.performanceState && this.performanceTurn === cue.turnId) {
      // 同一回复的分段等待期间保持演出。
      clearTimeout(this.motionTimer)
      this.adapter?.setMouthOpen(0)
      return
    }

    const shouldWaitForSpeakingMotion = (
      cue.kind === 'idle'
      && cue.key?.startsWith('idle:')
      && this.activeMotion?.kind === 'speaking'
    )
    if (shouldWaitForSpeakingMotion) {
      this.pendingIdleCue = cue
      this.adapter?.setMouthOpen(0)
      if (this.activeMotion.finished) {
        this.scheduleIdleRecovery(cue, this.performanceState ? Date.now() : this.activeMotion.finishedAt)
      }
      return
    }
    await this.applyCue(cue)
  }

  async applyCue(cue, force = false, options = {}) {
    if (!this.adapter || !this.presentation || this.destroyed) return
    if (cue.kind === 'speaking' && this.presentation.version === 'v3' && this.presentation.capabilities?.performance) {
      await this.applyPerformanceCue(cue)
      return
    }
    this.performanceState = null
    this.performanceTurn = null
    this.adapter.setPerformanceExpression?.(false)
    const cueGeneration = ++this.cueGeneration
    const adapter = this.adapter
    const isCurrent = () => cueGeneration === this.cueGeneration && adapter === this.adapter && !this.destroyed
    clearTimeout(this.motionTimer)
    this.motionTimer = null
    this.removeMotionFinishListener?.()
    this.removeMotionFinishListener = null
    this.activeMotion = null
    this.pendingIdleCue = null
    this.resetEyeOpenTransition()
    this.adapter.setMouthOpen(0)

    const capabilities = this.presentation.capabilities
    const motion = selectMotion(capabilities, cue)
    const expression = motion?.expression || selectSemanticExpression(capabilities, cue)
    if (expression) await adapter.setExpression(expression, isCurrent).catch(() => false)
    else adapter.resetExpression()
    if (!isCurrent()) return

    const startedAt = Date.now()
    let motionStarted = false
    if (motion) {
      motionStarted = await adapter.startMotion(
        motion.group,
        motion.index,
        force ? MotionPriority.FORCE : MotionPriority.NORMAL,
        cue.kind === 'idle'
          ? IDLE_FADE_IN_MS
          : (cue.kind === 'speaking' ? SPEAKING_FADE_IN_MS : 0),
        false,
        isCurrent,
      ).catch(() => false)
    }
    if (!isCurrent()) return
    if (!motionStarted) {
      this.handleMissingMotion(cue, startedAt)
      return
    }

    this.activeMotion = {
      kind: cue.kind,
      key: cue.key,
      startedAt,
      finished: false,
      finishedAt: 0,
      repeatCount: options.repeatCount || 0,
    }
    this.removeMotionFinishListener = this.adapter.onceMotionFinish(() => {
      this.handleMotionFinish(cueGeneration)
    })
  }

  handleMotionFinish(cueGeneration) {
    if (cueGeneration !== this.cueGeneration || !this.activeMotion) return
    this.removeMotionFinishListener = null
    const motion = this.activeMotion
    motion.finished = true
    motion.finishedAt = Date.now()
    if (!this.performanceState) this.queueEyeOpenTransition()

    if (motion.kind === 'thinking') {
      this.scheduleMotionFromStart(THINKING_REPEAT_MS, motion, () => {
        if (this.cue?.kind === 'thinking' && this.cue?.key === motion.key) {
          this.applyCue(this.cue, true)
        }
      })
      return
    }
    if (motion.kind === 'speaking') {
      if (this.performanceState) {
        if (this.cue?.kind === 'idle') this.scheduleIdleRecovery(this.cue, motion.finishedAt)
        return
      }
      if (this.cue?.kind !== 'speaking' || this.cue?.key !== motion.key) {
        this.resumeCurrentCue(motion.finishedAt)
        return
      }
      const duration = Number(this.cue.duration) || 0
      if (
        duration >= LONG_AUDIO_THRESHOLD_SECONDS
        && motion.repeatCount < LONG_AUDIO_MAX_REPEATS
      ) {
        this.motionTimer = setTimeout(() => {
          if (this.cue?.kind === 'speaking' && this.cue?.key === motion.key) {
            this.applyCue(this.cue, true, { repeatCount: motion.repeatCount + 1 })
          }
        }, LONG_AUDIO_REPEAT_DELAY_MS)
      }
      return
    }
    if (motion.kind === 'change_character') {
      this.resumeCurrentCue(motion.finishedAt)
      return
    }
    if (motion.kind === 'idle_random') {
      this.scheduleIdleRecovery(this.cue, motion.finishedAt)
      return
    }
    if (motion.kind === 'idle') {
      this.scheduleIdleCycle(motion)
    }
  }

  handleMissingMotion(cue, startedAt) {
    if (cue.kind === 'thinking') {
      this.motionTimer = setTimeout(() => {
        if (this.cue?.kind === 'thinking' && this.cue?.key === cue.key) {
          this.applyCue(this.cue, true)
        }
      }, THINKING_REPEAT_MS)
    } else if (cue.kind === 'change_character') {
      this.resumeCurrentCue(startedAt)
    } else if (cue.kind === 'idle' || cue.kind === 'idle_random') {
      this.scheduleIdleCycle({ startedAt })
    }
  }

  async applyPerformanceCue(cue) {
    const adapter = this.adapter
    const capabilities = this.presentation.capabilities
    const previous = this.performanceTurn === cue.turnId ? this.performanceState || {} : {}
    const plan = resolvePerformance(capabilities.performance, cue.performance, cue.emotion, 'C', previous)
    const generation = ++this.cueGeneration
    const isCurrent = () => generation === this.cueGeneration && adapter === this.adapter && !this.destroyed
    clearTimeout(this.motionTimer)
    this.motionTimer = null
    this.pendingIdleCue = null
    this.resetEyeOpenTransition()
    adapter.setMouthOpen(0)
    let motionOk = !plan.change_motion
    let expressionOk = !plan.change_expression
    if (plan.change_motion) {
      this.removeMotionFinishListener?.()
      this.removeMotionFinishListener = null
      const index = capabilities.motion_files_by_group?.__dsakiko_performance__?.indexOf(plan.state.motion_file) ?? -1
      if (index >= 0) motionOk = await adapter.startMotion('__dsakiko_performance__', index, MotionPriority.FORCE, SPEAKING_FADE_IN_MS, true, isCurrent).catch(() => false)
      if (!isCurrent()) return
      this.activeMotion = { kind: 'speaking', key: cue.key, startedAt: Date.now(), finished: !motionOk, finishedAt: motionOk ? 0 : Date.now() }
    }
    // 表情资源可能加载较慢，先监听动作结束，避免漏掉唯一的完成事件。
    this.removeMotionFinishListener?.()
    this.removeMotionFinishListener = !this.activeMotion || this.activeMotion.finished ? null : adapter.onceMotionFinish(() => {
      if (isCurrent() && this.activeMotion) {
        this.activeMotion.finished = true
        this.activeMotion.finishedAt = Date.now()
        if (this.cue?.kind === 'idle') this.scheduleIdleRecovery(this.cue, Date.now())
      }
    })
    if (plan.change_expression) {
      expressionOk = await adapter.setExpression(plan.state.expression, isCurrent).catch(() => false)
      if (!isCurrent()) return
    }
    this.performanceState = {
      ...plan.state,
      motion_id: motionOk ? plan.state.motion_id : previous.motion_id,
      motion_file: motionOk ? plan.state.motion_file : previous.motion_file,
      expression: expressionOk ? plan.state.expression : previous.expression,
    }
    this.performanceTurn = cue.turnId
    adapter.setPerformanceExpression?.(Boolean(this.performanceState.expression))
    if (!this.activeMotion) this.activeMotion = { kind: 'speaking', key: cue.key, finished: true, finishedAt: Date.now() }
    // 相同动作继续播放时也更新回调代号，但不重新开始动作。
    this.removeMotionFinishListener?.()
    this.removeMotionFinishListener = this.activeMotion.finished ? null : adapter.onceMotionFinish(() => this.handleMotionFinish(generation))
  }

  scheduleMotionFromStart(interval, motion, callback) {
    const delay = Math.max(0, motion.startedAt + interval - Date.now())
    this.motionTimer = setTimeout(callback, delay)
  }

  scheduleIdleRecovery(cue, motionFinishedAt) {
    const cueGeneration = ++this.cueGeneration
    clearTimeout(this.motionTimer)
    this.motionTimer = null
    this.removeMotionFinishListener?.()
    this.removeMotionFinishListener = null
    this.pendingIdleCue = null
    const delay = Math.max(0, motionFinishedAt + IDLE_RECOVERY_DELAY_MS - Date.now())
    this.motionTimer = setTimeout(() => {
      if (
        cueGeneration === this.cueGeneration
        && this.cue?.kind === 'idle'
        && this.cue?.key === cue.key
      ) {
        this.applyCue(cue, true)
      }
    }, delay)
  }

  scheduleIdleCycle(motion) {
    this.scheduleMotionFromStart(IDLE_REPEAT_MS, motion, () => {
      if (this.cue?.kind !== 'idle') return
      this.applyCue({
        kind: 'idle_random',
        key: `idle-random:${this.cue.key}:${Date.now()}`,
      }, true)
    })
  }

  resumeCurrentCue(motionFinishedAt) {
    if (this.cue?.kind === 'idle') {
      this.scheduleIdleRecovery(this.pendingIdleCue || this.cue, motionFinishedAt)
      return
    }
    if (this.cue) this.applyCue(this.cue, true)
  }

  queueEyeOpenTransition() {
    const eyeOpen = this.adapter?.getEyeOpen()
    if (!eyeOpen || eyeOpen.left > 0.5) {
      this.resetEyeOpenTransition()
      return
    }
    this.eyeTransition = {
      left: eyeOpen.left,
      right: eyeOpen.right,
      startedAt: Date.now(),
      complete: false,
    }
  }

  resetEyeOpenTransition() {
    this.eyeTransition = null
    this.adapter?.clearEyeOpenOverride()
  }

  fit(width, height) {
    const model = this.adapter?.model
    if (!model || width <= 0 || height <= 0) return
    model.scale.set(1)
    const fitScale = Math.min(
      (width * 1.2) / model.width,
      (height * 1.3) / model.height,
    )
    const layout = this.presentation?.layout || {}
    const layoutScale = Number.isFinite(layout.scale) ? layout.scale : 1
    const offsetX = Number.isFinite(layout.offset_x) ? layout.offset_x : 0
    const offsetY = Number.isFinite(layout.offset_y) ? layout.offset_y : 0
    model.scale.set(fitScale * layoutScale)
    model.position.set(
      width / 2 + offsetX * width / 2,
      height * 0.5 - offsetY * height / 2,
    )
  }

  replaceAdapter(nextAdapter) {
    this.cueGeneration += 1
    this.performanceState = null
    this.performanceTurn = null
    clearTimeout(this.motionTimer)
    this.motionTimer = null
    this.removeMotionFinishListener?.()
    this.removeMotionFinishListener = null
    this.activeMotion = null
    this.pendingIdleCue = null
    this.resetEyeOpenTransition()
    if (this.adapter) {
      this.app.stage.removeChild(this.adapter.model)
      this.adapter.destroy()
    }
    this.adapter = nextAdapter
    if (nextAdapter) this.app.stage.addChild(nextAdapter.model)
    this.onModelChange()
  }

  destroy() {
    this.destroyed = true
    this.generation += 1
    this.cueGeneration += 1
    clearTimeout(this.motionTimer)
    clearTimeout(this.autoRetryTimer)
    this.replaceAdapter(null)
  }
}
