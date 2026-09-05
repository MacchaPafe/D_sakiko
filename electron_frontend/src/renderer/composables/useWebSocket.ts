import { ref, onUnmounted } from 'vue'
import type { Live2DStateMachine } from '../statemachine/Live2DStateMachine'

interface BridgeSession {
  protocol: string
  token: string
  ws_url: string
  instance_id: string
}

/**
 * Compatibility helper for Electron views. Callers obtain the ephemeral
 * session through preload; there is deliberately no unauthenticated URL default.
 */
export function useWebSocket(
  stateMachine: Live2DStateMachine,
  getSession: () => Promise<BridgeSession | null>,
) {
  const connected = ref(false)
  const retryCount = ref(0)
  let ws: WebSocket | null = null
  let bridgeReady = false
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null
  let reconnectDelay = 1000

  async function connect() {
    if (ws?.readyState === WebSocket.OPEN || ws?.readyState === WebSocket.CONNECTING) return
    const session = await getSession().catch(() => null)
    if (!session || session.protocol !== 'dsakiko.bridge.v1' || !session.token || !session.ws_url || !session.instance_id) {
      scheduleReconnect()
      return
    }
    try { ws = new WebSocket(session.ws_url, session.protocol) } catch (_) { scheduleReconnect(); return }
    ws.onopen = () => {
      bridgeReady = false
      connected.value = true
      retryCount.value = 0
      reconnectDelay = 1000
      ws?.send(JSON.stringify({ type: 'electron_hello', data: { capabilities: ['model', 'motion', 'audio', 'lipsync', 'ui'] } }))
    }
    ws.onmessage = event => {
      try {
        const message = JSON.parse(event.data)
        if (message?.type === 'bridge_ready') {
          bridgeReady = message.data?.authenticated === true
            && message.data?.protocol === session.protocol
            && message.data?.instance_id === session.instance_id
          if (!bridgeReady) ws?.close()
          return
        }
        if (!bridgeReady) return
        stateMachine.pushEvent({ type: message.type, data: message.data })
      } catch (_) { /* malformed bridge events are ignored */ }
    }
    ws.onclose = () => { connected.value = false; bridgeReady = false; ws = null; retryCount.value++; scheduleReconnect() }
    ws.onerror = () => ws?.close()
  }

  function scheduleReconnect() {
    if (reconnectTimer) clearTimeout(reconnectTimer)
    reconnectTimer = setTimeout(() => { reconnectDelay = Math.min(reconnectDelay * 2, 30000); void connect() }, reconnectDelay)
  }

  function disconnect() {
    if (reconnectTimer) clearTimeout(reconnectTimer)
    reconnectTimer = null
    if (!ws) return
    ws.onopen = null; ws.onclose = null; ws.onerror = null; ws.onmessage = null
    try { ws.close() } catch (_) { /* best effort */ }
    ws = null
  }

  onUnmounted(disconnect)
  void connect()
  return { connected, retryCount, connect, disconnect }
}
