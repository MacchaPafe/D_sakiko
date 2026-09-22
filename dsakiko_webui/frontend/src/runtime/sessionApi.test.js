import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createSession } from './sessionApi'

describe('createSession', () => {
  beforeEach(() => {
    const values = new Map()
    Object.defineProperty(window, 'localStorage', {
      configurable: true,
      value: {
        getItem: (key) => values.get(key) || null,
        setItem: (key, value) => values.set(key, value),
      },
    })
  })

  afterEach(() => {
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
  })

  it('preserves the server retry duration on a rate-limited response', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      error: {
        code: 'AUTH_RATE_LIMITED',
        message: '尝试次数过多，请等待后再试。',
        details: { retry_after_seconds: 75 },
      },
    }), {
      status: 429,
      headers: {
        'Content-Type': 'application/json',
        'Retry-After': '75',
      },
    })))

    await expect(createSession('000000')).rejects.toMatchObject({
      code: 'AUTH_RATE_LIMITED',
      retryAfterSeconds: 75,
    })
  })
})
