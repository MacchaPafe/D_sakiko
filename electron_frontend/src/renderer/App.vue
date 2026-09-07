<script setup lang="ts">
import { computed, onMounted, onUnmounted, provide, reactive, ref, shallowRef, watch } from 'vue'
import Live2DStage from './components/Live2DStage.vue'
import ResizeHandler from './components/ResizeHandler.vue'
import ControlsIsland from './components/controls-island/index.vue'
import type { Live2DStateMachine } from './statemachine'
import type { StateMachineEvent } from './statemachine/constants'
import type { BasePresentation, SakikoPresentationState } from './statemachine/presentation-policy'
import type { ElectronWindowState } from './composables/electronWindowState'

interface ElectronModelLayout {
  scale: number
  offset_x: number
  offset_y: number
}

export interface SubtitleSettings {
  enabled: boolean
  fontSize: number
  bottomOffset: number
  maxWidth: number
  textColor: string
  backgroundColor: string
  backgroundOpacity: number
}

const SUBTITLE_SETTINGS_KEY = 'saki-subtitle-settings-v1'
const defaultSubtitleSettings: SubtitleSettings = {
  enabled: true,
  fontSize: 16,
  bottomOffset: 64,
  maxWidth: 80,
  textColor: '#D4D4D4',
  backgroundColor: '#262626',
  backgroundOpacity: 0.8,
}

function clamp(value: unknown, minimum: number, maximum: number, fallback: number) {
  const number = Number(value)
  return Number.isFinite(number) ? Math.min(maximum, Math.max(minimum, number)) : fallback
}

function validHexColor(value: unknown, fallback: string) {
  return typeof value === 'string' && /^#[0-9a-f]{6}$/i.test(value) ? value.toUpperCase() : fallback
}

function rgbaFromHex(color: string, opacity: number) {
  const value = Number.parseInt(color.slice(1), 16)
  return `rgba(${(value >> 16) & 255}, ${(value >> 8) & 255}, ${value & 255}, ${opacity})`
}

function readSubtitleSettings(): SubtitleSettings {
  try {
    const stored = JSON.parse(localStorage.getItem(SUBTITLE_SETTINGS_KEY) || '{}') as Partial<SubtitleSettings>
    return {
      enabled: typeof stored.enabled === 'boolean' ? stored.enabled : defaultSubtitleSettings.enabled,
      fontSize: clamp(stored.fontSize, 12, 28, defaultSubtitleSettings.fontSize),
      bottomOffset: clamp(stored.bottomOffset, 24, 160, defaultSubtitleSettings.bottomOffset),
      maxWidth: clamp(stored.maxWidth, 40, 95, defaultSubtitleSettings.maxWidth),
      textColor: validHexColor(stored.textColor, defaultSubtitleSettings.textColor),
      backgroundColor: validHexColor(stored.backgroundColor, defaultSubtitleSettings.backgroundColor),
      backgroundOpacity: clamp(stored.backgroundOpacity, 0, 0.95, defaultSubtitleSettings.backgroundOpacity),
    }
  } catch (_) {
    return { ...defaultSubtitleSettings }
  }
}

const stateMachine = shallowRef<Live2DStateMachine | null>(null)
const wsConnected = ref(false)
const textBubble = computed(() => stateMachine.value?.textBubble.value ?? null)
const userBubble = computed(() => stateMachine.value?.userBubble.value ?? null)
const isThinking = computed(() => stateMachine.value?.isThinking.value ?? false)

const currentCharKey = ref('sakiko')
const currentSakikoState = ref<SakikoPresentationState>()
const currentBasePresentation = ref<BasePresentation>('idle')
const currentLayout = ref<ElectronModelLayout>()
const initialEntrance = ref(true)
// The backend announces the configured model after the bridge connects.  The
// formal character assets are not bundled into the Electron renderer.
const modelPath = ref('')
const lastModelSelection = ref<{
  path: string
  key: string
  color: string
  sakikoState: SakikoPresentationState | undefined
  basePresentation: BasePresentation
  layout: ElectronModelLayout | undefined
}>()
const stageKey = ref(0)
const themeColor = ref('#7799CC')
const pendingEvents: StateMachineEvent[] = []
const MAX_PENDING_EVENTS = 64
const modelLoadFailure = ref('')
const bridgeReady = ref(false)
const subtitleSettings = reactive<SubtitleSettings>(readSubtitleSettings())
const subtitleStyle = computed(() => ({
  fontSize: `${subtitleSettings.fontSize}px`,
  bottom: `${subtitleSettings.bottomOffset}px`,
  maxWidth: `${subtitleSettings.maxWidth}%`,
  color: subtitleSettings.textColor,
  backgroundColor: rgbaFromHex(subtitleSettings.backgroundColor, subtitleSettings.backgroundOpacity),
}))

const windowState = reactive<ElectronWindowState>({
  cursor: { x: 0, y: 0 },
  bounds: { x: 0, y: 0, width: 0, height: 0 },
})
const nearBorder = computed(() => {
  const x = windowState.cursor.x - windowState.bounds.x
  const y = windowState.cursor.y - windowState.bounds.y
  const threshold = 12
  const inside = windowState.bounds.width > 0 && windowState.bounds.height > 0
    && x >= -threshold && x <= windowState.bounds.width + threshold
    && y >= -threshold && y <= windowState.bounds.height + threshold
  return inside && (x <= threshold || x >= windowState.bounds.width - threshold
    || y <= threshold || y >= windowState.bounds.height - threshold)
})

const fadeOnHoverEnabled = ref(false)
const mouseX = ref(0)
const mouseY = ref(0)
const mouseInWindow = ref(true)
const isOverModel = computed(() => {
  if (!mouseInWindow.value) return false
  const margin = 0.2
  return mouseX.value > window.innerWidth * margin
    && mouseX.value < window.innerWidth * (1 - margin)
    && mouseY.value > window.innerHeight * margin
    && mouseY.value < window.innerHeight * (1 - margin)
})
const shouldFade = computed(() => fadeOnHoverEnabled.value && isOverModel.value)

provide('electronWindowState', windowState)
provide('fadeOnHoverEnabled', fadeOnHoverEnabled)
provide('toggleFadeOnHover', () => { fadeOnHoverEnabled.value = !fadeOnHoverEnabled.value })
provide('sendUiIntent', (intent: string) => sendUiIntent(intent))
provide('bridgeReady', bridgeReady)
provide('subtitleSettings', subtitleSettings)
provide('resetSubtitleSettings', () => Object.assign(subtitleSettings, defaultSubtitleSettings))

let stopWindowStateListener: (() => void) | null = null
let ws: WebSocket | null = null
let reconnectTimer: ReturnType<typeof setTimeout> | null = null
const reconnectDelay = ref(1000)
const connecting = ref(false)
const hasConnectedOnce = ref(false)
let expectedBridgeInstanceId = ''
let reconnectEnabled = true

function setThemeColor(value: unknown) {
  if (typeof value === 'string' && /^#[0-9a-f]{6}$/i.test(value)) themeColor.value = value.toUpperCase()
}

function presentationFromBridge(data: Record<string, unknown>): BasePresentation {
  const declared = data.presentation_base ?? data.base_presentation
  if (declared === 'serious' || declared === 'idle') return declared
  const sakikoState = data.sakiko_state ?? data.value
  return sakikoState === 'black' || sakikoState === 1 || sakikoState === '1' ? 'serious' : 'idle'
}

function desktopLayoutFromBridge(value: unknown): ElectronModelLayout | undefined {
  if (!value || typeof value !== 'object') return undefined
  const raw = value as Record<string, unknown>
  const scale = Number(raw.scale)
  const offsetX = Number(raw.offset_x)
  const offsetY = Number(raw.offset_y)
  if (!Number.isFinite(scale) || scale <= 0 || !Number.isFinite(offsetX) || !Number.isFinite(offsetY)) return undefined
  return { scale, offset_x: offsetX, offset_y: offsetY }
}

function toEvent(message: unknown): StateMachineEvent | null {
  if (!message || typeof message !== 'object') return null
  const raw = message as { type?: unknown; data?: unknown }
  if (typeof raw.type !== 'string') return null
  return { type: raw.type as StateMachineEvent['type'], data: raw.data || {} }
}

function queuePendingEvent(event: StateMachineEvent) {
  if (modelLoadFailure.value) return
  if (pendingEvents.length >= MAX_PENDING_EVENTS) pendingEvents.shift()
  pendingEvents.push(event)
}

function onBackendMessage(message: unknown) {
  const event = toEvent(message)
  if (!event) return
  const data = event.data || {}
  if (event.type === 'bridge_ready') {
    bridgeReady.value = data.authenticated === true
      && data.protocol === 'dsakiko.bridge.v1'
      && typeof data.instance_id === 'string'
      && data.instance_id === expectedBridgeInstanceId
    if (!bridgeReady.value) {
      console.warn('[WS] bridge readiness identity mismatch')
      try { ws?.close() } catch (_) { /* reconnect below */ }
    } else {
      // bridge_ready is the first renderer lifecycle boundary.  The bridge
      // sends any exact disconnect recovery immediately after this frame.
    }
    return
  }
  // Only the hello snapshot follows a valid bridge_ready. A browser may
  // receive a queued frame just before hello is processed; never let that
  // frame create renderer presentation state.
  if (!bridgeReady.value) return
  if (event.type === 'bye') {
    pendingEvents.length = 0
    if (stateMachine.value) stateMachine.value.pushEvent(event)
    else {
      // Model loading may already have failed, so bye cannot depend on a
      // local FSM existing in order to close the Electron process.
      try { void window.electronAPI.closeWindow() } catch (_) { try { window.close() } catch (_) {} }
    }
    return
  }
  if (event.type === 'renderer_recovery') {
    // A refreshed page cannot resume another page's local audio/FIFO. The
    // bridge has asked Qt to cancel that backend turn; recover our own
    // presentation immediately rather than silently accumulating old cues.
    pendingEvents.length = 0
    stateMachine.value?.pushEvent({ type: 'cancel', data })
    return
  }
  if (event.type === 'assistant_turn_complete') return
  if (event.type === 'theme') {
    setThemeColor(data.color || data.theme_color)
    return
  }
  const modelPathFromEvent = typeof data.model_url === 'string' && data.model_url
    ? data.model_url
    : typeof data.model_path === 'string' && data.model_path
      ? data.model_path
      : ''
  if ((event.type === 'initial_model' || event.type === 'switch_character' || event.type === 'switch_live2d' || event.type === 'sakiko_state')
    && modelPathFromEvent) {
    const key = String(data.character_folder || data.character_key || (event.type === 'sakiko_state' ? 'sakiko' : currentCharKey.value))
    const reportedState = data.sakiko_state === 'black' || data.sakiko_state === 'white'
      ? data.sakiko_state
      : data.value === 'black' || data.value === 'white'
        ? data.value
        : undefined
    reloadModel(
      modelPathFromEvent, key, data.theme_color, reportedState, presentationFromBridge(data),
      desktopLayoutFromBridge(data.layout), event.type === 'initial_model',
    )
    if (event.type !== 'initial_model') queuePendingEvent(event)
    return
  }
  if (stateMachine.value) stateMachine.value.pushEvent(event)
  else queuePendingEvent(event)
}

function onStateMachineReady(next: Live2DStateMachine) {
  stateMachine.value = next
  while (pendingEvents.length) next.pushEvent(pendingEvents.shift()!)
}

function reloadModel(
  path: string,
  key?: string,
  color?: unknown,
  sakikoState?: SakikoPresentationState,
  basePresentation: BasePresentation = 'idle',
  layout?: ElectronModelLayout,
  shouldPlayInitialEntrance = true,
) {
  stateMachine.value = null
  pendingEvents.length = 0
  modelLoadFailure.value = ''
  modelPath.value = path
  if (key) currentCharKey.value = key
  currentSakikoState.value = sakikoState
  currentBasePresentation.value = basePresentation
  currentLayout.value = layout
  initialEntrance.value = shouldPlayInitialEntrance
  setThemeColor(color)
  lastModelSelection.value = {
    path,
    key: currentCharKey.value,
    color: themeColor.value,
    sakikoState,
    basePresentation,
    layout,
  }
  stageKey.value += 1
}

function retryModel() {
  const selection = lastModelSelection.value
  if (!selection) return
  reloadModel(
    selection.path, selection.key, selection.color, selection.sakikoState,
    selection.basePresentation, selection.layout, false,
  )
}

function onModelError(error: unknown) {
  console.error('[Live2D] model unavailable; waiting for a later backend model selection:', error)
  stateMachine.value = null
  pendingEvents.length = 0
  modelLoadFailure.value = 'Live2D 模型加载失败。'
  // Destroy the failed Stage instance so no renderer keeps enqueueing work
  // without a local state machine. The next model business fact clears this
  // fallback and mounts a fresh Stage.
  // Keep the model selection locally so the user can retry without forcing a
  // backend character switch, then unmount the failed Stage immediately.
  modelPath.value = ''
}

async function connectWebSocket() {
  if (connecting.value || ws?.readyState === WebSocket.OPEN || ws?.readyState === WebSocket.CONNECTING) return
  connecting.value = true
  const session = await window.electronAPI.getBridgeSession().catch(() => null)
  connecting.value = false
  if (!session || session.protocol !== 'dsakiko.bridge.v1' || !session.token || !session.ws_url || !session.instance_id) {
    scheduleReconnect()
    return
  }
  expectedBridgeInstanceId = session.instance_id
  let socket: WebSocket
  try { socket = new WebSocket(session.ws_url, session.protocol) } catch { scheduleReconnect(); return }
  ws = socket
  socket.onopen = () => {
    if (ws !== socket) return
    wsConnected.value = true
    hasConnectedOnce.value = true
    reconnectDelay.value = 1000
    socket.send(JSON.stringify({
      type: 'electron_hello',
      data: {
        capabilities: ['model', 'motion', 'audio', 'lipsync', 'ui'],
      },
    }))
  }
  socket.onmessage = event => {
    try { onBackendMessage(JSON.parse(event.data)) } catch (error) { console.warn('[WS] invalid event', error) }
  }
  socket.onclose = () => {
    if (ws !== socket) return
    wsConnected.value = false
    bridgeReady.value = false
    ws = null
    if (reconnectEnabled) scheduleReconnect()
  }
  socket.onerror = () => socket.close()
}

function scheduleReconnect() {
  if (!reconnectEnabled) return
  if (reconnectTimer) clearTimeout(reconnectTimer)
  reconnectTimer = setTimeout(() => {
    reconnectDelay.value = Math.min(reconnectDelay.value * 2, 30000)
    void connectWebSocket()
  }, reconnectDelay.value)
}

function sendUiIntent(intent: string): boolean {
  if (!bridgeReady.value || ws?.readyState !== WebSocket.OPEN) return false
  try {
    ws.send(JSON.stringify({ type: 'ui_intent', data: { intent } }))
    return true
  } catch (_) {
    return false
  }
}

function retryConnection() {
  bridgeReady.value = false
  if (reconnectTimer) clearTimeout(reconnectTimer)
  reconnectTimer = null
  const previousSocket = ws
  ws = null
  wsConnected.value = false
  if (previousSocket) {
    previousSocket.onopen = null
    previousSocket.onmessage = null
    previousSocket.onclose = null
    previousSocket.onerror = null
    try { previousSocket.close() } catch (_) { /* reconnect below */ }
  }
  void connectWebSocket()
}

const connectionStatus = computed(() => {
  if (!wsConnected.value) {
    if (reconnectDelay.value >= 8000) return '无法连接后端，正在重试'
    return hasConnectedOnce.value ? '连接已断开，正在重连…' : '正在连接后端…'
  }
  if (!bridgeReady.value) return '正在验证连接…'
  if (!stateMachine.value && !modelLoadFailure.value) return '正在加载 Live2D…'
  return ''
})

const connectionRetryVisible = computed(() => !wsConnected.value && reconnectDelay.value >= 8000)

watch(subtitleSettings, () => {
  try { localStorage.setItem(SUBTITLE_SETTINGS_KEY, JSON.stringify(subtitleSettings)) } catch (_) { /* local preferences are best effort */ }
}, { deep: true })

function disconnectWebSocket() {
  reconnectEnabled = false
  if (reconnectTimer) clearTimeout(reconnectTimer)
  reconnectTimer = null
  if (!ws) return
  ws.onopen = null
  ws.onclose = null
  ws.onerror = null
  ws.onmessage = null
  try { ws.close() } catch (_) { /* best effort */ }
  ws = null
}

function onMouseMove(event: MouseEvent) {
  mouseX.value = event.clientX
  mouseY.value = event.clientY
}

function onMouseLeave() { mouseInWindow.value = false }
function onMouseEnter() { mouseInWindow.value = true }

onMounted(() => {
  window.addEventListener('mousemove', onMouseMove)
  document.addEventListener('mouseleave', onMouseLeave)
  document.addEventListener('mouseenter', onMouseEnter)
  stopWindowStateListener = window.electronAPI.onWindowState(next => {
    Object.assign(windowState.cursor, next.cursor)
    Object.assign(windowState.bounds, next.bounds)
  })
  void connectWebSocket()
})

onUnmounted(() => {
  window.removeEventListener('mousemove', onMouseMove)
  document.removeEventListener('mouseleave', onMouseLeave)
  document.removeEventListener('mouseenter', onMouseEnter)
  stopWindowStateListener?.()
  stopWindowStateListener = null
  disconnectWebSocket()
})
</script>

<template>
  <div class="app-root">
    <div
      class="stage-area"
      :class="{ 'pointer-events-none': fadeOnHoverEnabled }"
      :style="{ transition: 'opacity 0.25s ease-in-out', opacity: shouldFade ? 0 : 1 }"
    >
      <Live2DStage v-if="modelPath"
        :key="stageKey"
        :model-path="modelPath"
        :model-key="currentCharKey"
        :sakiko-state="currentSakikoState"
        :base-presentation="currentBasePresentation"
        :layout="currentLayout"
        :initial-entrance="initialEntrance"
        @state-machine-ready="onStateMachineReady"
        @model-error="onModelError"
      />
    </div>
    <Transition name="fade">
      <div v-if="connectionStatus" class="connection-status" role="status">
        <span>{{ connectionStatus }}</span>
        <button v-if="connectionRetryVisible" type="button" @click="retryConnection">立即重试</button>
      </div>
    </Transition>
    <div v-if="modelLoadFailure" class="model-load-fallback" role="alert">
      <p>{{ modelLoadFailure }}</p>
      <div class="recovery-actions">
        <button type="button" :disabled="!lastModelSelection" @click="retryModel">重试模型</button>
        <button type="button" @click="sendUiIntent('open_python_settings')">打开 Python 设置</button>
        <button v-if="!bridgeReady" type="button" @click="retryConnection">重新连接</button>
      </div>
    </div>
    <Transition name="fade"><div v-if="subtitleSettings.enabled && textBubble" class="text-bubble character" :style="subtitleStyle">{{ textBubble }}</div></Transition>
    <Transition name="fade"><div v-if="userBubble" class="text-bubble user">{{ userBubble }}</div></Transition>
    <Transition name="fade"><div v-if="isThinking" class="thinking-indicator">思考中...</div></Transition>
    <ResizeHandler />
    <ControlsIsland />
    <div
      class="window-edge-highlight"
      :class="{ 'is-visible': nearBorder }"
      :style="{ '--window-theme-color': themeColor }"
      aria-hidden="true"
    />
  </div>
</template>

<style scoped>
.app-root { --window-radius: 16px; --window-edge-inset: 3px; width: 100%; height: 100%; position: relative; overflow: hidden; background: transparent; }
.stage-area { width: 100%; height: 100%; position: absolute; inset: 0; }
.text-bubble { position: absolute; padding: .5rem 1rem; max-width: 80%; text-align: center; font-size: 16px; border-radius: .75rem; background: rgba(38,38,38,.8); color: #d4d4d4; pointer-events: none; }
.text-bubble.character { left: 50%; transform: translateX(-50%); }
.text-bubble.user { top: 1rem; right: 1rem; }
.thinking-indicator { position: absolute; top: .5rem; left: 50%; transform: translateX(-50%); padding: .25rem .75rem; font-size: 12px; border-radius: .5rem; background: rgba(38,38,38,.8); color: #f59e0b; pointer-events: none; }
.connection-status { position: absolute; z-index: 5; top: .75rem; left: 50%; transform: translateX(-50%); display: flex; align-items: center; gap: .5rem; max-width: calc(100% - 2rem); padding: .35rem .65rem; color: #e5e7eb; font-size: 12px; background: rgba(20, 20, 20, .72); border-radius: .5rem; pointer-events: auto; }
.connection-status button, .recovery-actions button { border: 0; border-radius: .375rem; padding: .25rem .5rem; color: #f8fafc; background: rgba(71, 85, 105, .86); cursor: pointer; font: inherit; }
.model-load-fallback { position: absolute; inset: 0; z-index: 5; display: grid; place-content: center; gap: .75rem; padding: 2rem; text-align: center; color: #e5e7eb; background: rgba(20, 20, 20, .62); pointer-events: auto; }
.model-load-fallback p { margin: 0; }
.recovery-actions { display: flex; flex-wrap: wrap; justify-content: center; gap: .5rem; }
.recovery-actions button:disabled { cursor: not-allowed; opacity: .5; }
.fade-enter-active, .fade-leave-active { transition: opacity .3s ease; }
.fade-enter-from, .fade-leave-to { opacity: 0; }
.window-edge-highlight { position: fixed; inset: var(--window-edge-inset); z-index: 9999; box-sizing: border-box; pointer-events: none; border: 2px solid var(--window-theme-color); border-radius: var(--window-radius); clip-path: inset(0 round var(--window-radius)); opacity: 0; visibility: hidden; transition: opacity 280ms ease, visibility 0s linear 280ms, border-color 700ms ease; }
.window-edge-highlight::before { content: ''; position: absolute; inset: 1px; border: 1px solid color-mix(in srgb, var(--window-theme-color) 28%, white 72%); border-radius: calc(var(--window-radius) - 1px); opacity: .42; pointer-events: none; }
.window-edge-highlight::after { content: ''; position: absolute; inset: 0; border: 1px solid color-mix(in srgb, var(--window-theme-color) 45%, transparent); border-radius: inherit; box-shadow: 0 0 9px 1px color-mix(in srgb, var(--window-theme-color) 22%, transparent); opacity: .3; pointer-events: none; }
.window-edge-highlight.is-visible { opacity: .7; visibility: visible; }
.window-edge-highlight.is-visible::after { animation: window-edge-glow-breathe 3.8s ease-in-out infinite; }
@keyframes window-edge-glow-breathe { 0%, 100% { opacity: .24; } 50% { opacity: .42; } }
@media (prefers-reduced-motion: reduce) { .window-edge-highlight.is-visible::after { animation: none; opacity: .3; } }
</style>
