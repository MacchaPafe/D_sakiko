<script setup lang="ts">
import { ref, onMounted, onUnmounted } from 'vue'
import type { Application, Ticker } from 'pixi.js'
import { Live2DRendererController } from '../renderer-controller'

const props = defineProps<{ modelPath?: string; modelKey?: string; modelToken?: string; rendererId?: string }>()
const emit = defineEmits<{
  rendererControllerReady: [controller: Live2DRendererController]
  rendererFact: [fact: { type: string; event_id?: string; data: Record<string, any> }]
}>()

const canvasContainer = ref<HTMLDivElement>()
let app: Application | null = null
let controller: Live2DRendererController | null = null
let resizeObserver: ResizeObserver | null = null
let resizeFrame: number | null = null
let disposed = false

function onCanvasClick(e: MouseEvent) {
  if (controller && canvasContainer.value) {
    controller.handleClick(e.clientX, canvasContainer.value.clientWidth)
  }
}

onMounted(async () => {
  disposed = false
  const { Application, Ticker } = await import('pixi.js')
  const { Live2DModel } = await import('pixi-live2d-display')

  if (disposed) return

  const canvas = canvasContainer.value?.querySelector('canvas') as HTMLCanvasElement
  if (!canvas) return

  app = new Application({
    view: canvas,
    width: canvasContainer.value!.clientWidth,
    height: canvasContainer.value!.clientHeight,
    backgroundAlpha: 0,
    antialias: true,
    resolution: window.devicePixelRatio || 1,
    autoDensity: true,
  })

  if (disposed) {
    app.destroy(true)
    app = null
    return
  }

  try {
    // Model selection is authoritative Python state.  A cold Electron
    // renderer stays an inert runtime until the owner sends switch_live2d.
    if (!props.modelPath) {
      console.log('[Live2DStage] Waiting for authoritative model command')
      return
    }
    const modelSrc = props.modelPath
    const key = props.modelKey || 'live2d'
    // The package root combines Cubism 2 and Cubism 4 adapters and selects
    // the correct loader from the model JSON, preserving V2 and V3/V4.
    Live2DModel.registerTicker(Ticker)
    const live2dModel = await Live2DModel.from(modelSrc, { autoInteract: false })

    if (disposed) {
      ;(live2dModel as any).destroy?.({ children: true })
      return
    }

    // Electron 默认窗口尺寸是 450x600。这个基准必须在换模时保持稳定；
    // 如果把当前窗口尺寸当作基准，窗口缩小后换模会把模型缩放重置回 0.3。
    // 窗口尺寸改变和模型重载都通过同一公式计算，确保换模不会改变视觉比例。
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
      const ratio = Math.min(width / referenceWidth, height / referenceHeight)
      live2dModel.scale.set(baseScale * ratio)
      live2dModel.x = width / 2
      live2dModel.y = height / 2
    }
    const scheduleResize = () => {
      if (resizeFrame !== null) return
      resizeFrame = requestAnimationFrame(applyResize)
    }
    live2dModel.anchor.set(0.5, 0.5)
    applyResize()
    resizeObserver = new ResizeObserver(scheduleResize)
    resizeObserver.observe(canvasContainer.value!)
    app.stage.addChild(live2dModel)

    // 行为选择由 Python controller 完成；renderer 只执行指定 group/index。
    controller = new Live2DRendererController(live2dModel, Ticker.shared, key, (fact) => emit('renderer-fact', {
      ...fact,
      data: { ...fact.data, model_token: props.modelToken || '' },
    }), props.rendererId || key, props.modelToken || '', /\.model3\.json$/i.test(modelSrc) ? 'v3' : 'v2', modelSrc)
    controller.start()
    emit('rendererControllerReady', controller)
    console.log('[Live2DStage] Model loaded, renderer controller started')
  } catch (e) {
    console.error('[Live2DStage] Failed to load model:', e)
    emit('renderer-fact', {
      type: 'renderer_unavailable',
      data: {
        model_token: props.modelToken || '',
        renderer_id: props.rendererId || props.modelKey || 'electron-renderer',
        reason: String(e),
      },
    })
  }
})

onUnmounted(() => {
  disposed = true
  resizeObserver?.disconnect()
  resizeObserver = null
  if (resizeFrame !== null) cancelAnimationFrame(resizeFrame)
  resizeFrame = null
  controller?.destroy()
  controller = null
  app?.destroy(true)
})
</script>

<template>
  <div ref="canvasContainer" class="live2d-container" @click="onCanvasClick">
    <canvas></canvas>
  </div>
</template>

<style scoped>
.live2d-container {
  width: 100%;
  height: 100%;
  position: absolute;
  top: 0;
  left: 0;
}
.live2d-container canvas {
  width: 100%;
  height: 100%;
  display: block;
}
</style>
