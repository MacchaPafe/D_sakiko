import { beforeEach, describe, expect, it } from 'vitest'
import {
  nextAuthenticationStep,
  readAndClearPairingToken,
} from './pairingBootstrap'

describe('readAndClearPairingToken', () => {
  beforeEach(() => {
    window.history.replaceState(null, '', '/')
  })

  it('returns a valid token and clears the fragment synchronously', () => {
    const token = 'a'.repeat(43)
    window.history.replaceState(null, '', `/?mode=mobile#pair=${token}`)
    expect(readAndClearPairingToken()).toBe(token)
    expect(window.location.hash).toBe('')
    expect(window.location.search).toBe('?mode=mobile')
  })

  it('clears and rejects malformed pairing fragments', () => {
    window.history.replaceState(null, '', '/#pair=123456')
    expect(readAndClearPairingToken()).toBeNull()
    expect(window.location.hash).toBe('')
  })
})

describe('nextAuthenticationStep', () => {
  it('prefers an existing authenticated cookie over pairing redemption', () => {
    expect(nextAuthenticationStep(true, 'a'.repeat(43))).toBe('connect')
  })

  it('redeems pairing only when the cookie is not authenticated', () => {
    expect(nextAuthenticationStep(false, 'a'.repeat(43))).toBe('redeem_pairing')
    expect(nextAuthenticationStep(false, null)).toBe('request_access_code')
  })
})
