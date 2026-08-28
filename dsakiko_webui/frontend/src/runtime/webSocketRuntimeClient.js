import { randomId } from './ids'

const RECONNECT_DELAYS = [500, 1000, 2000, 4000, 8000]

function requestId() {
  return randomId('req')
}

export class WebSocketRuntimeClient {
  constructor() {
    this.socket = null
    this.listener = null
    this.connectionListener = null
    this.pending = new Map()
    this.reconnectTimer = null
    this.reconnectAttempt = 0
    this.closedByClient = false
  }

  connect(listener, connectionListener) {
    this.listener = listener
    this.connectionListener = connectionListener
    this.closedByClient = false
    this.open()
  }

  open() {
    if (
      this.socket?.readyState === WebSocket.OPEN
      || this.socket?.readyState === WebSocket.CONNECTING
    ) return

    window.clearTimeout(this.reconnectTimer)
    this.connectionListener?.({ type: 'connecting' })
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const socket = new WebSocket(`${protocol}//${window.location.host}/api/v1/ws`)
    this.socket = socket

    socket.addEventListener('open', () => {
      if (this.socket !== socket) return
      this.reconnectAttempt = 0
      this.connectionListener?.({ type: 'connected' })
    })

    socket.addEventListener('message', (message) => {
      let envelope
      try {
        envelope = JSON.parse(message.data)
      } catch {
        return
      }

      if (envelope.kind === 'response' && envelope.type === 'command_result') {
        const pending = this.pending.get(envelope.request_id)
        if (!pending) return
        this.pending.delete(envelope.request_id)
        if (envelope.ok) {
          pending.resolve(envelope.data)
        } else {
          const error = new Error(envelope.error?.message || '后端没有完成这次操作。')
          error.code = envelope.error?.code || 'COMMAND_FAILED'
          error.retryable = Boolean(envelope.error?.retryable)
          pending.reject(error)
        }
        return
      }

      if (envelope.kind !== 'event') return
      this.listener?.(envelope)
      if (envelope.type === 'runtime_ready') {
        this.command('sync', {}).catch((error) => {
          this.connectionListener?.({ type: 'command_error', error })
        })
      }
    })

    socket.addEventListener('close', (event) => {
      if (this.socket !== socket) return
      this.socket = null
      for (const pending of this.pending.values()) {
        pending.reject(new Error('连接已中断，请稍后重试。'))
      }
      this.pending.clear()

      if (this.closedByClient) return
      if (event.code === 4401) {
        this.connectionListener?.({
          type: 'auth_required',
          message: '登录失效，重新输入访问码～',
        })
        return
      }
      if (event.code === 4409) {
        this.connectionListener?.({ type: 'session_superseded' })
        return
      }

      this.connectionListener?.({ type: 'offline' })
      const delay = RECONNECT_DELAYS[
        Math.min(this.reconnectAttempt, RECONNECT_DELAYS.length - 1)
      ]
      this.reconnectAttempt += 1
      this.reconnectTimer = window.setTimeout(() => {
        this.reconnectTimer = null
        if (document.visibilityState === 'hidden') {
          this.connectionListener?.({ type: 'background_suspended' })
          return
        }
        this.open()
      }, delay)
    })
  }

  disconnect() {
    this.closedByClient = true
    window.clearTimeout(this.reconnectTimer)
    this.socket?.close(1000, '页面关闭')
    this.socket = null
    for (const pending of this.pending.values()) {
      pending.reject(new Error('连接已关闭。'))
    }
    this.pending.clear()
  }

  command(type, payload) {
    if (this.socket?.readyState !== WebSocket.OPEN) {
      return Promise.reject(new Error('还未连接到电脑端服务呢。'))
    }
    const id = requestId()
    const result = new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject })
    })
    this.socket.send(JSON.stringify({
      protocol_version: 1,
      kind: 'command',
      type,
      request_id: id,
      payload,
    }))
    return result
  }

  getChatList() {
    return this.command('get_chat_list', {})
  }

  switchChat(chatId) {
    return this.command('switch_chat', { chat_id: chatId })
  }

  createChat({ characterId, name, userPersonaId }) {
    return this.command('create_chat', {
      character_id: characterId,
      name: name.trim() || null,
      user_persona_id: userPersonaId || null,
    })
  }

  sendMessage(chatId, text, clientMessageId, imageUploadIds = []) {
    return this.command('send_message', {
      chat_id: chatId,
      text,
      client_message_id: clientMessageId,
      image_upload_ids: imageUploadIds,
    })
  }

  cancelTurn(chatId, turnId) {
    return this.command('cancel_turn', {
      chat_id: chatId,
      turn_id: turnId,
    })
  }

  nextBackground() {
    return this.command('next_background', {})
  }

  retryLive2D(chatId) {
    return this.command('retry_live2d', { chat_id: chatId })
  }
}
