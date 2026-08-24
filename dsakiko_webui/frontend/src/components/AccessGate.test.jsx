import { render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AccessGate } from './AccessGate'

describe('AccessGate cooldown', () => {
  afterEach(() => {
    vi.useRealTimers()
  })

  it('disables code inputs and renders the server countdown', () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-08-24T00:00:00Z'))
    render(<AccessGate
      state={{
        connection: 'needs_auth',
        authRetryUntil: Date.now() + 65_000,
        error: null,
      }}
      actions={{ authenticate: vi.fn() }}
    />)
    expect(screen.getByText('请等待 1 分 5 秒后再试')).toBeTruthy()
    for (const input of screen.getAllByRole('textbox')) {
      expect(input.disabled).toBe(true)
    }
  })
})
