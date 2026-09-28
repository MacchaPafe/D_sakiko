<script setup lang="ts">
import { ref, onMounted, onUnmounted } from 'vue'
import type { Application } from 'pixi.js'
import type { Live2DModel } from 'pixi-live2d-display'
import { Live2DStateMachine } from '../statemachine'
import {
  parseLive2DPresentationMetadata,
  type BasePresentation,
  type SakikoPresentationState,
} from '../statemachine/presentation-policy'

interface DesktopLayout {
  scale: number
  offset_x: number
  offset_y: number
}

// Global Cubism 3/4 calibration. These values apply only after V3 desktop
// layout scale has been selected; V2 retains its historical display mapping.
const v3ScaleTune = 0.07
const v3XTune = 0
const v3YTune = -290

const props = defineProps<{
  modelPath: string
  modelKey?: string
  sakikoState?: SakikoPresentationState
  basePresentation?: BasePresentation
  layout?: DesktopLayout
  initialEntrance?: boolean
}>()
const emit = defineEmits<{
  stateMachineReady: [stateMachine: Live2DStateMachine]
  modelError: [error: unknown]
}>()

const canvasContainer = ref<HTMLDivElement>()
let app: Application | null = null
let stateMachine: Live2DStateMachine | null = null
let live2dModel: Live2DModel | null = null
let resizeObserver: ResizeObserver | null = null
let resizeFrame: number | null = null
let disposed = false

function destroyLive2DModel() {
  const model = live2dModel
  live2dModel = null
  if (!model) return
  try { app?.stage.removeChild(model) } catch (_) { /* best effort */ }
  // pixi-live2d-display keeps model textures outside ordinary child traversal
  // on some adapters. Release both display and base textures before a reload.
  try {
    const textures = (model.internalModel as any)?.textures
    if (Array.isArray(textures)) {
      for (const texture of textures) {
        try { texture?.destroy?.(true) } catch (_) { /* best effort */ }
        try { texture?.baseTexture?.destroy?.() } catch (_) { /* best effort */ }
      }
    }
    ;(model.internalModel as any)?.textureManager?.release?.()
  } catch (_) { /* optional adapter internals */ }
  try { (model as any).destroy({ children: true, texture: true, baseTexture: true }) } catch (_) { /* best effort */ }
}

function onCanvasClick(event: MouseEvent) {
  if (stateMachine && canvasContainer.value) {
    stateMachine.handleClick(event.clientX, canvasContainer.value.clientWidth)
  }
}

function waitForFirstRenderedFrame(currentApp: Application): Promise<void> {
  // `Live2DModel.from` resolves before the model has traversed Pixi's render
  // loop. `postrender` is deliberately later than an app ticker callback, so
  // the model has actually reached the canvas before its entrance begins.
  // This is a real readiness boundary, not a timed startup delay.
  return new Promise(resolve => currentApp.renderer.once('postrender', resolve))
}

onMounted(async () => {
  const { Application, Ticker } = await import('pixi.js')
  const { Live2DModel, config } = await import('pixi-live2d-display')
  if (disposed) return
  const canvas = canvasContainer.value?.querySelector('canvas') as HTMLCanvasElement | null
  if (!canvas || !canvasContainer.value) return

  app = new Application({
    view: canvas,
    width: canvasContainer.value.clientWidth,
    height: canvasContainer.value.clientHeight,
    backgroundAlpha: 0,
    antialias: true,
    resolution: window.devicePixelRatio || 1,
    autoDensity: true,
  })

  try {
    // The package root selects the Cubism 2 or Cubism 3/4 adapter from the
    // model definition. SDK differences stay inside this Electron runtime.
    Live2DModel.registerTicker(Ticker)
    // Electron owns every presentation/audio cue. The package otherwise
    // requests its configured idle group after a motion and plays manifest
    // Sound entries through its own SoundManager.
    config.sound = false
    const model = await Live2DModel.from(props.modelPath, {
      autoInteract: false,
      idleMotionGroup: '__dsakiko_electron_idle__',
    })
    live2dModel = model
    if (disposed) {
      destroyLive2DModel()
      return
    }

    // Cubism 2 assets in the application were authored for the historical
    // 450x600 pet window and retain the upstream Electron scale. Cubism 3
    // desktop layout values are already expressed in the same normalized
    // display space as the upstream renderer; do not apply a second fit.
    let runtimeKind: 'v2' | 'v3' = 'v2'
    const baseScale = 0.3
    const referenceWidth = 450
    const referenceHeight = 600
    let lastWidth = 0
    let lastHeight = 0
    const applyResize = () => {
      resizeFrame = null
      if (!app || !canvasContainer.value) return
      const width = Math.max(1, canvasContainer.value.clientWidth)
      const height = Math.max(1, canvasContainer.value.clientHeight)
      if (width === lastWidth && height === lastHeight) return
      lastWidth = width
      lastHeight = height
      app.renderer.resize(width, height)
      const layout = props.layout
      if (runtimeKind === 'v3') {
        if (layout) {
          // Existing upstream desktop layout values include the full V3
          // reference-envelope scale and normalized landing point.
          model.scale.set(layout.scale)
        } else if (model.width > 0 && model.height > 0) {
          // External models without layout metadata still use the same desktop
          // reference envelope rather than a model-size-specific auto-fit.
          const ratio = Math.min(width / referenceWidth, height / referenceHeight)
          model.scale.set(2.3 * ratio)
        } else {
          // Preserve the existing zero-bounds fallback until the V3 adapter
          // exposes a measurable model envelope.
          const ratio = Math.min(width / referenceWidth, height / referenceHeight)
          model.scale.set(baseScale * ratio)
        }

        // Apply the V3-wide display calibration after the original layout
        // scale, then use the resulting on-screen bounds for layout offsets.
        model.scale.set(model.scale.x * v3ScaleTune)
        const displayedWidth = model.width
        const displayedHeight = model.height
        model.x = width / 2 + (layout?.offset_x ?? 0) * displayedWidth / 2 + v3XTune
        model.y = height / 2 - (layout?.offset_y ?? 0) * displayedHeight / 2 + v3YTune
      } else {
        // V2 retains the historical scale and offset behavior unchanged.
        const ratio = Math.min(width / referenceWidth, height / referenceHeight)
        model.scale.set(baseScale * ratio)
        const displayedWidth = model.width
        const displayedHeight = model.height
        model.x = width / 2 + (layout?.offset_x ?? 0) * displayedWidth / 2
        model.y = height / 2 - (layout?.offset_y ?? 0) * displayedHeight / 2
      }
    }
    const scheduleResize = () => {
      if (resizeFrame === null) resizeFrame = requestAnimationFrame(applyResize)
    }
    let presentationMetadata = parseLive2DPresentationMetadata(null)
    try {
      const response = await fetch(props.modelPath)
      if (response.ok) {
        // Cubism 2 stores `motions`/`expressions` at the root, while Cubism
        // 3/4 keeps them in `FileReferences`.  Keep files by index so the
        // local runtime can select the expression for the exact motion chosen.
        const definition = await response.json()
        if (disposed) {
          destroyLive2DModel()
          return
        }
        presentationMetadata = parseLive2DPresentationMetadata(definition)
        runtimeKind = presentationMetadata.runtimeKind
      }
    } catch (error) {
      console.warn('[Live2DStage] presentation metadata unavailable:', error)
    }

    if (disposed) {
      destroyLive2DModel()
      return
    }
    stateMachine = new Live2DStateMachine(
      model,
      Ticker.shared,
      props.modelKey || 'sakiko',
      presentationMetadata,
      props.sakikoState,
      props.basePresentation,
    )
    model.anchor.set(0.5, 0.5)
    // Keep the model off-stage while the first supported expression is loaded
    // and the runtime kind/layout are known. The first visible frame now has
    // the same base state as the Qt switch path.
    await stateMachine.prepareInitialPresentation()
    if (disposed) {
      destroyLive2DModel()
      return
    }
    app.stage.addChild(model)
    applyResize()
    resizeObserver = new ResizeObserver(scheduleResize)
    resizeObserver.observe(canvasContainer.value)
    await waitForFirstRenderedFrame(app)
    if (disposed) {
      destroyLive2DModel()
      return
    }
    stateMachine.start({ initialEntrance: props.initialEntrance !== false })
    emit('stateMachineReady', stateMachine)
    console.log('[Live2DStage] model loaded:', props.modelPath)
  } catch (error) {
    // The parent may already have mounted B while A's dynamic import/model
    // load rejects. Do not emit A's failure into B's active presentation.
    if (disposed) return
    console.error('[Live2DStage] model load failed:', error)
    emit('modelError', error)
  }
})

onUnmounted(() => {
  disposed = true
  resizeObserver?.disconnect()
  resizeObserver = null
  if (resizeFrame !== null) cancelAnimationFrame(resizeFrame)
  resizeFrame = null
  stateMachine?.destroy()
  stateMachine = null
  destroyLive2DModel()
  app?.destroy(true)
  app = null
})
</script>

<template>
  <div ref="canvasContainer" class="live2d-container" @click="onCanvasClick">
    <canvas></canvas>
  </div>
</template>

<style scoped>
.live2d-container {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
}
.live2d-container canvas {
  display: block;
  width: 100%;
  height: 100%;
}
</style>
