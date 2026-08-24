import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { WebSocketRuntimeClient } from './webSocketRuntimeClient'

class FakeWebSocket extends EventTarget {
  static CONNECTING = 0

  static OPEN = 1

  static instances = []

  constructor(url) {
    super()
    this.url = url
    this.readyState = FakeWebSocket.CONNECTING
    FakeWebSocket.instances.push(this)
  }

  send() {}

  close() {
    this.readyState = 3
  }

  emitClose(code) {
    this.readyState = 3
    this.dispatchEvent(new CloseEvent('close', { code }))
  }
}

describe('WebSocketRuntimeClient', () => {
  beforeEach(() => {
    FakeWebSocket.instances = []
    vi.stubGlobal('WebSocket', FakeWebSocket)
    vi.useFakeTimers()
    Object.defineProperty(document, 'visibilityState', {
      configurable: true,
      value: 'visible',
    })
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('reports a superseded session separately and does not reconnect after 4409', () => {
    const statuses = []
    const client = new WebSocketRuntimeClient()
    client.connect(() => {}, (status) => statuses.push(status.type))

    FakeWebSocket.instances[0].emitClose(4409)
    vi.runAllTimers()

    expect(statuses).toEqual(['connecting', 'session_superseded'])
    expect(FakeWebSocket.instances).toHaveLength(1)
  })

  it('still reports an invalid cookie as requiring authentication', () => {
    const statuses = []
    const client = new WebSocketRuntimeClient()
    client.connect(() => {}, (status) => statuses.push(status.type))

    FakeWebSocket.instances[0].emitClose(4401)

    expect(statuses).toEqual(['connecting', 'auth_required'])
    expect(FakeWebSocket.instances).toHaveLength(1)
  })

  it('does not run a queued reconnect after its tab moves to the background', () => {
    const statuses = []
    const client = new WebSocketRuntimeClient()
    client.connect(() => {}, (status) => statuses.push(status.type))
    FakeWebSocket.instances[0].emitClose(1006)
    Object.defineProperty(document, 'visibilityState', {
      configurable: true,
      value: 'hidden',
    })

    vi.advanceTimersByTime(500)

    expect(statuses).toEqual(['connecting', 'offline', 'background_suspended'])
    expect(FakeWebSocket.instances).toHaveLength(1)
  })
})
