import { describe, expect, it } from 'vitest'
import { timeDomainRms } from './useAudioController'

describe('audio loudness', () => {
  it('calculates RMS from float time-domain samples', () => {
    expect(timeDomainRms(new Float32Array([0.5, -0.5]))).toBeCloseTo(0.5)
  })

  it('calculates RMS from byte time-domain samples', () => {
    expect(timeDomainRms(new Uint8Array([192, 64]), true)).toBeCloseTo(0.5)
  })
})
