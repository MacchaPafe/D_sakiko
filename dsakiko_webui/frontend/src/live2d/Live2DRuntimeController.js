import { Live2DModel, MotionPriority } from 'pixi-live2d-display'
import { createRuntimeAdapter } from './runtimeAdapters'
import {
  selectMotion,
  selectSemanticExpression,
} from './cuePolicy'

const THINKING_REPEAT_MS = 15_000
const MODEL_LOAD_TIMEOUT_MS = 30_000

function presentationKey(presentation) {
  if (!presentation?.target_id) return null
  return `${presentation.target_id}:${presentation.revision || ''}`
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
    this.thinkingTimer = null
    this.autoRetryTimer = null
    this.removeMotionFinishListener = null
    this.destroyed = false
  }

  setActive(active) {
    if (active) this.app.ticker.start()
    else this.app.ticker.stop()
  }

  setMouthOpen(value) {
    this.adapter?.setMouthOpen(value)
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

  async setCue(cue) {
    if (!cue) return
    const unchanged = this.cue?.kind === cue.kind && this.cue?.key === cue.key
    this.cue = cue
    if (!unchanged) await this.applyCue(cue)
  }

  async applyCue(cue, force = false) {
    if (!this.adapter || !this.presentation || this.destroyed) return
    const cueGeneration = ++this.cueGeneration
    clearTimeout(this.thinkingTimer)
    this.removeMotionFinishListener?.()
    this.removeMotionFinishListener = null
    this.adapter.setMouthOpen(0)
    this.adapter.stopMotions()

    const capabilities = this.presentation.capabilities
    const motion = selectMotion(capabilities, cue)
    const expression = motion?.expression || selectSemanticExpression(capabilities, cue)
    if (expression) await this.adapter.setExpression(expression).catch(() => false)
    else this.adapter.resetExpression()

    let motionStarted = false
    if (motion) {
      motionStarted = await this.adapter.startMotion(
        motion.group,
        motion.index,
        force ? MotionPriority.FORCE : MotionPriority.NORMAL,
      ).catch(() => false)
    }
    if (cueGeneration !== this.cueGeneration || !motionStarted) return
    if (cue.kind === 'thinking') {
      this.removeMotionFinishListener = this.adapter.onceMotionFinish(() => {
        if (cueGeneration !== this.cueGeneration) return
        this.thinkingTimer = setTimeout(() => {
          if (this.cue?.kind === 'thinking' && this.cue?.key === cue.key) {
            this.applyCue(cue, true)
          }
        }, THINKING_REPEAT_MS)
      })
    } else if (cue.kind === 'change_character') {
      this.removeMotionFinishListener = this.adapter.onceMotionFinish(() => {
        if (cueGeneration === this.cueGeneration) this.applyCue(this.cue, true)
      })
    }
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
    this.removeMotionFinishListener?.()
    this.removeMotionFinishListener = null
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
    clearTimeout(this.thinkingTimer)
    clearTimeout(this.autoRetryTimer)
    this.replaceAdapter(null)
  }
}
