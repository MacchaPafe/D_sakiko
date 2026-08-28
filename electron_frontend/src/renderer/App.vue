<script setup lang="ts">
import { shallowRef, ref, reactive, onUnmounted, computed, onMounted, provide } from 'vue'
import Live2DStage from './components/Live2DStage.vue'
import ResizeHandler from './components/ResizeHandler.vue'
import ControlsIsland from './components/controls-island/index.vue'
import type { Live2DRendererController } from './renderer-controller'
import type { ProtocolMessage } from './renderer-controller/constants'
import type { ElectronWindowState } from './composables/electronWindowState'

const rendererController = shallowRef<Live2DRendererController | null>(null)
const wsConnected = ref(false)
const textBubble = computed(() => rendererController.value?.textBubble.value ?? null)
const userBubble = computed(() => rendererController.value?.userBubble.value ?? null)
const isThinking = computed(() => rendererController.value?.isThinking.value ?? false)

// 模型切换（由 WS 事件驱动）
const currentCharKey = ref('')
// 启动时不选择黑祥/白祥；Python controller 会在 renderer hello 后下发模型。
const customModelPath = ref('')
const pendingModelToken = ref('')
const themeColor = ref('#7799CC')
const windowState = reactive<ElectronWindowState>({
  cursor: { x: 0, y: 0 },
  bounds: { x: 0, y: 0, width: 0, height: 0 },
})
const nearBorder = computed(() => {
  const x = windowState.cursor.x - windowState.bounds.x
  const y = windowState.cursor.y - windowState.bounds.y
  const threshold = 12
  const nearWindow = windowState.bounds.width > 0 && windowState.bounds.height > 0
    && x >= -threshold && x <= windowState.bounds.width + threshold
    && y >= -threshold && y <= windowState.bounds.height + threshold
  return nearWindow && (
    x <= threshold || x >= windowState.bounds.width - threshold
    || y <= threshold || y >= windowState.bounds.height - threshold
  )
})
const rendererId = sessionStorage.getItem('live2d-renderer-id') || (() => {
  const id = crypto.randomUUID(); sessionStorage.setItem('live2d-renderer-id', id); return id
})()
// Stable renderer identity routes fan-out commands; this instance identity
// changes on a page reload so the Python owner can replay conversion facts.
const rendererInstanceId = crypto.randomUUID()
const stageKey = ref(0)

// ── 悬停淡出（airi fade-on-hover）──
const fadeOnHoverEnabled = ref(false)
const mouseX = ref(0)
const mouseY = ref(0)
const mouseInWindow = ref(true)
const isOverModel = computed(() => {
  if (!mouseInWindow.value) return false
  const mx = 0.2
  return mouseX.value > window.innerWidth * mx
      && mouseX.value < window.innerWidth * (1 - mx)
      && mouseY.value > window.innerHeight * mx
      && mouseY.value < window.innerHeight * (1 - mx)
})
const shouldFade = computed(() => fadeOnHoverEnabled.value && isOverModel.value)
let stopWindowStateListener: (() => void) | null = null

provide('electronWindowState', windowState)

onMounted(() => {
  // 悬停淡出鼠标追踪
  window.addEventListener('mousemove', (e) => {
    mouseX.value = e.clientX; mouseY.value = e.clientY
  })
  document.addEventListener('mouseleave', () => { mouseInWindow.value = false })
  document.addEventListener('mouseenter', () => { mouseInWindow.value = true })

  stopWindowStateListener = window.electronAPI.onWindowState((next) => {
    windowState.cursor.x = next.cursor.x
    windowState.cursor.y = next.cursor.y
    windowState.bounds.x = next.bounds.x
    windowState.bounds.y = next.bounds.y
    windowState.bounds.width = next.bounds.width
    windowState.bounds.height = next.bounds.height
  })
  connectWebSocket()
})

function toggleFadeOnHover() {
  fadeOnHoverEnabled.value = !fadeOnHoverEnabled.value
}

function setThemeColor(color: unknown) {
  if (typeof color !== 'string' || !/^#[0-9a-f]{6}$/i.test(color)) return
  themeColor.value = color.toUpperCase()
}

provide('fadeOnHoverEnabled', fadeOnHoverEnabled)
provide('toggleFadeOnHover', toggleFadeOnHover)

function reloadCustomModel(path: string, charKey?: string, nextThemeColor?: unknown) {
  rendererController.value = null
  customModelPath.value = path
  if (charKey) currentCharKey.value = charKey
  setThemeColor(nextThemeColor)
  stageKey.value++
}

function onRendererControllerReady(controller: Live2DRendererController) {
  rendererController.value = controller
  for (const command of pendingSnapshotCommands) controller.pushCommand(command as any)
  pendingSnapshotCommands = []
  if (ws?.readyState === WebSocket.OPEN) controller.reportReady()
  else connectWebSocket()
}

function createProtocolMessage(type: string, data: Record<string, any>): ProtocolMessage {
  return {
    v: 1,
    type,
    event_id: crypto.randomUUID(),
    session_id: sessionStorage.getItem('live2d-session-id') || (() => {
      const id = crypto.randomUUID(); sessionStorage.setItem('live2d-session-id', id); return id
    })(),
    source: 'electron-renderer',
    timestamp: Date.now() / 1000,
    data,
  }
}

// ── WebSocket ──
let ws: WebSocket | null = null
let reconnectTimer: ReturnType<typeof setTimeout> | null = null
let reconnectDelay = 1000
let pendingSnapshotCommands: Array<{ type: string; data?: Record<string, any> }> = []

function connectWebSocket() {
  if (ws?.readyState === WebSocket.OPEN || ws?.readyState === WebSocket.CONNECTING) return
  if (ws) { try { ws.onopen=null; ws.onclose=null; ws.onerror=null; ws.onmessage=null; ws.close() } catch(_){}; ws=null }
  try {
    const token = encodeURIComponent(window.electronAPI.bridgeToken || '')
    ws = new WebSocket(`ws://127.0.0.1:9876/?token=${token}`)
  } catch(e) { scheduleReconnect(); return }
  ws.onopen = () => {
      wsConnected.value = true
      reconnectDelay = 1000
      ws?.send(JSON.stringify(createProtocolMessage('renderer_hello', {
      capabilities: ['motion', 'audio', 'lipsync', 'snapshot'],
      model_key: currentCharKey.value,
      model_token: pendingModelToken.value,
      renderer_id: rendererId,
      renderer_role: 'electron',
      renderer_instance_id: rendererInstanceId,
    })))
    rendererController.value?.reportReady()
  }
  ws.onmessage = (event) => {
    try {
      const msg = JSON.parse(event.data) as Partial<ProtocolMessage> & { data?: any }
      if (msg.type === 'live2d_command') {
        const command = msg.data?.command || msg.data
        const targets = command?.data?.target_renderer_ids
        const target = command?.data?.target_renderer_id
        if ((Array.isArray(targets) && targets.length > 0 && !targets.includes(rendererId))
          || (target && target !== rendererId)) return
        if (command?.type === 'set_theme_color') {
          setThemeColor(command.data?.theme_color)
          return
        }
        if (command?.type) {
          const commandData = { ...(command.data || command) }
          if (command.type === 'switch_live2d' && commandData.model_url) {
            pendingModelToken.value = String(commandData.model_token || '')
            reloadCustomModel(String(commandData.model_url), String(commandData.character_folder ?? commandData.character_folder_name ?? currentCharKey.value))
            return
          }
          rendererController.value?.pushCommand({ type: command.type, event_id: msg.event_id, session_id: msg.session_id, data: commandData })
        }
        return
      }
      if (msg.type === 'renderer_snapshot' && Array.isArray(msg.data?.commands)) {
        const commands = msg.data.commands as Array<{ type: string; data?: Record<string, any> }>
        const hasModelSwitch = commands.some((command) => command?.type === 'switch_live2d' && (command.data || {}).model_url)
        for (const command of commands) {
          if (!command?.type) continue
          const commandData = { ...(command.data || command) }
          if (command.type === 'switch_live2d' && commandData.model_url) {
            pendingModelToken.value = String(commandData.model_token || '')
            reloadCustomModel(String(commandData.model_url), String(commandData.character_folder ?? commandData.character_folder_name ?? currentCharKey.value))
            continue
          }
          if (hasModelSwitch || !rendererController.value) {
            pendingSnapshotCommands.push({ ...command, data: commandData })
            continue
          }
          rendererController.value?.pushCommand({ ...command, data: commandData })
        }
      }
    } catch(e) { console.warn('[WS] Parse:', e) }
  }
  ws.onclose = () => {
    wsConnected.value = false
    ws = null
    rendererController.value?.abortTransportPlayback()
    scheduleReconnect()
  }
  ws.onerror = () => { ws?.close() }
}

function onRendererFact(fact: { type: string; event_id?: string; data: Record<string, any> }) {
  if (!ws || ws.readyState !== WebSocket.OPEN) return
  const message = createProtocolMessage(fact.type, {
    ...fact.data,
    renderer_id: rendererId,
    renderer_instance_id: rendererInstanceId,
  })
  ws.send(JSON.stringify(message))
}

provide('sendRendererIntent', (intent: string) => {
  onRendererFact({ type: 'renderer_intent', data: { intent } })
})

function scheduleReconnect() {
  if (reconnectTimer) clearTimeout(reconnectTimer)
  reconnectTimer = setTimeout(() => { reconnectDelay = Math.min(reconnectDelay*2, 30000); connectWebSocket() }, reconnectDelay)
}

function disconnectWebSocket() {
  if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null }
  if (ws) { ws.onopen=null; ws.onclose=null; ws.onerror=null; ws.onmessage=null; try{ws.close()}catch(_){}; ws=null }
}

onUnmounted(() => {
  disconnectWebSocket()
  stopWindowStateListener?.()
  stopWindowStateListener = null
})
</script>

<template>
  <div class="app-root">
    <div class="stage-area" :class="{ 'pointer-events-none': fadeOnHoverEnabled }" :style="{ transition: 'opacity 0.25s ease-in-out', opacity: shouldFade ? 0 : 1 }">
      <Live2DStage :key="stageKey" :model-path="customModelPath" :model-key="currentCharKey" :model-token="pendingModelToken" :renderer-id="rendererId" @renderer-controller-ready="onRendererControllerReady" @renderer-fact="onRendererFact" />
    </div>
    <Transition name="fade"><div v-if="textBubble" class="text-bubble character">{{ textBubble }}</div></Transition>
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
.app-root {
  --window-radius: 16px;
  --window-edge-inset: 3px;
  width:100%; height:100%; position:relative; overflow:hidden; background:transparent;
}
.stage-area { width:100%; height:100%; position:absolute; top:0; left:0; }
.text-bubble { position:absolute; padding:.5rem 1rem; max-width:80%; text-align:center; font-size:16px; border-radius:.75rem; background:rgba(38,38,38,.8); color:#d4d4d4; pointer-events:none; }
.text-bubble.character { bottom:4rem; left:50%; transform:translateX(-50%); }
.text-bubble.user { top:1rem; right:1rem; }
.thinking-indicator { position:absolute; top:.5rem; left:50%; transform:translateX(-50%); padding:.25rem .75rem; font-size:12px; border-radius:.5rem; background:rgba(38,38,38,.8); color:#f59e0b; pointer-events:none; }
.fade-enter-active,.fade-leave-active { transition:opacity .3s ease; }
.fade-enter-from,.fade-leave-to { opacity:0; }

.window-edge-highlight {
  position: fixed;
  inset: var(--window-edge-inset);
  z-index: 9999;
  box-sizing: border-box;
  pointer-events: none;
  border: 2px solid var(--window-theme-color);
  border-radius: var(--window-radius);
  clip-path: inset(0 round var(--window-radius));
  opacity: 0;
  visibility: hidden;
  transition: opacity 280ms ease, visibility 0s linear 280ms,
    border-color 700ms ease;
}
.window-edge-highlight::before {
  content: '';
  position: absolute;
  inset: 1px;
  border: 1px solid color-mix(in srgb, var(--window-theme-color) 28%, white 72%);
  border-radius: calc(var(--window-radius) - 1px);
  opacity: .42;
  pointer-events: none;
}
.window-edge-highlight::after {
  content: '';
  position: absolute;
  inset: 0;
  border: 1px solid color-mix(in srgb, var(--window-theme-color) 45%, transparent);
  border-radius: inherit;
  box-shadow: 0 0 9px 1px color-mix(in srgb, var(--window-theme-color) 22%, transparent);
  opacity: .3;
  pointer-events: none;
}
.window-edge-highlight.is-visible {
  opacity: 0.7;
  visibility: visible;
}
.window-edge-highlight.is-visible::after {
  animation: window-edge-glow-breathe 3.8s ease-in-out infinite;
}
@keyframes window-edge-glow-breathe {
  0%, 100% {
    opacity: 0.24;
  }
  50% {
    opacity: 0.42;
  }
}
@media (prefers-reduced-motion: reduce) {
  .window-edge-highlight.is-visible {
    animation: none;
  }
  .window-edge-highlight.is-visible::after {
    animation: none;
    opacity: .3;
  }
}
</style>
