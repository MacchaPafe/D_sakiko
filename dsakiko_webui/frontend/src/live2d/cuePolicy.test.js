import { describe, expect, it } from 'vitest'
import {
  live2dCueFromState,
  motionCandidatesForCue,
  selectMotion,
  selectSemanticExpression,
} from './cuePolicy'

describe('Live2D cue policy', () => {
  it('uses the approved speaking fallback chain', () => {
    expect(motionCandidatesForCue({ kind: 'speaking', emotion: 'sadness' })).toEqual([
      'sadness',
      'talking_motion',
      'idle_motion',
      'IDLE',
    ])
  })

  it('falls back to talking motion and keeps its motion expression', () => {
    const capabilities = {
      motion_files_by_group: {
        talking_motion: ['nod.motion3.json'],
      },
      expressions_by_motion: {
        talking_motion: ['exp_serious01'],
      },
    }

    expect(selectMotion(
      capabilities,
      { kind: 'speaking', emotion: 'fear' },
      () => 0,
    )).toEqual({
      group: 'talking_motion',
      index: 0,
      expression: 'exp_serious01',
    })
  })

  it('falls back from cue expression to idle expression', () => {
    const capabilities = {
      semantic_expressions: {
        idle: ['exp_idle01'],
      },
    }
    expect(selectSemanticExpression(capabilities, {
      kind: 'speaking',
      emotion: 'fear',
    })).toBe('exp_idle01')
  })

  it('enters speaking only after actual audio playback starts', () => {
    const cue = live2dCueFromState({
      phase: 'tts',
      turnId: 'turn_one',
      currentChatId: 'chat_one',
      playback: { status: 'playing', instanceId: 2, messageId: 'message_one' },
      playingMessage: { id: 'message_one', emotion: 'sadness' },
      cancelledTurnId: null,
    })
    expect(cue).toEqual({
      kind: 'speaking',
      key: 'speaking:message_one:2',
      emotion: 'sadness',
    })
  })

  it('keeps thinking while audio is only loading', () => {
    const cue = live2dCueFromState({
      phase: 'tts',
      turnId: 'turn_one',
      currentChatId: 'chat_one',
      playback: { status: 'loading', instanceId: 0, messageId: 'message_one' },
      playingMessage: { id: 'message_one', emotion: 'sadness' },
      cancelledTurnId: null,
    })
    expect(cue.kind).toBe('thinking')
  })

  it('returns to idle on pause even while the backend remains busy', () => {
    const cue = live2dCueFromState({
      phase: 'tts',
      turnId: 'turn_one',
      currentChatId: 'chat_one',
      playback: { status: 'paused', instanceId: 2, messageId: 'message_one' },
      playingMessage: { id: 'message_one', emotion: 'sadness' },
      cancelledTurnId: null,
    })
    expect(cue.kind).toBe('idle')
  })

  it('lets cancellation preempt thinking immediately', () => {
    const cue = live2dCueFromState({
      phase: 'thinking',
      turnId: 'turn_one',
      currentChatId: 'chat_one',
      playback: { status: 'idle', instanceId: 0, messageId: null },
      playingMessage: null,
      cancelledTurnId: 'turn_one',
    })
    expect(cue).toEqual({ kind: 'idle', key: 'cancelled:turn_one' })
  })
})
