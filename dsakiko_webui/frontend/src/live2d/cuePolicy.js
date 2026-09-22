const FALLBACKS = {
  speaking: ['talking_motion', 'idle_motion', 'IDLE'],
  thinking: ['text_generating', 'idle_motion', 'IDLE'],
  change_character: ['change_character', 'idle_motion', 'IDLE'],
  idle: ['idle_motion', 'IDLE'],
  idle_random: ['IDLE', 'idle_motion'],
}

export function live2dCueFromState({
  phase,
  turnId,
  currentChatId,
  playback,
  playingMessage,
  cancelledTurnId,
}) {
  if (cancelledTurnId && cancelledTurnId === turnId) {
    return {
      kind: 'idle',
      key: `cancelled:${cancelledTurnId}`,
    }
  }
  if (playback.status === 'playing' && playingMessage) {
    return {
      kind: 'speaking',
      key: `speaking:${playingMessage.id}:${playback.instanceId}`,
      emotion: playingMessage.emotion || 'happiness',
      duration: Number.isFinite(playback.duration) ? playback.duration : 0,
    }
  }
  if (['paused', 'blocked', 'error'].includes(playback.status)) {
    return {
      kind: 'idle',
      key: `audio-idle:${playback.messageId || 'none'}:${playback.instanceId}`,
    }
  }
  if (phase !== 'idle') {
    return {
      kind: 'thinking',
      key: `thinking:${turnId || 'pending'}`,
    }
  }
  return {
    kind: 'idle',
    key: `idle:${currentChatId || 'none'}`,
  }
}

export function motionCandidatesForCue(cue) {
  if (!cue) return FALLBACKS.idle
  if (cue.kind === 'speaking') {
    return [cue.emotion, ...FALLBACKS.speaking].filter(Boolean)
  }
  return FALLBACKS[cue.kind] || FALLBACKS.idle
}

export function semanticExpressionForCue(cue) {
  if (!cue) return 'idle'
  if (cue.kind === 'speaking') return cue.emotion || 'idle'
  if (cue.kind === 'thinking') return 'text_generating'
  return 'idle'
}

export function selectMotion(capabilities, cue, random = Math.random) {
  const filesByGroup = capabilities?.motion_files_by_group || {}
  for (const group of motionCandidatesForCue(cue)) {
    const files = filesByGroup[group]
    if (!Array.isArray(files) || files.length === 0) continue
    const index = Math.min(files.length - 1, Math.floor(random() * files.length))
    const expression = capabilities?.expressions_by_motion?.[group]?.[index] || null
    return { group, index, expression }
  }
  return null
}

export function selectSemanticExpression(capabilities, cue) {
  const semantic = semanticExpressionForCue(cue)
  const preferred = capabilities?.semantic_expressions?.[semantic]
  if (Array.isArray(preferred) && preferred.length) return preferred[0]
  const idle = capabilities?.semantic_expressions?.idle
  return Array.isArray(idle) && idle.length ? idle[0] : null
}
