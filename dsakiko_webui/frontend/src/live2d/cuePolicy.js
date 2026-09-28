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
  if (['playing', 'presenting'].includes(playback.status) && playingMessage) {
    return {
      kind: 'speaking',
      key: `speaking:${playingMessage.id}:${playback.instanceId}`,
      emotion: playingMessage.emotion || 'happiness',
      performance: playingMessage.performance || null,
      turnId: playingMessage.turn_id || turnId,
      silent: playback.status === 'presenting',
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
      turnId,
    }
  }
  if (playback.status === 'loading' && playingMessage) {
    return { kind: 'thinking', key: `loading:${playingMessage.id}`, turnId: playingMessage.turn_id || turnId }
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

export function resolvePerformance(catalog, selection, emotion, direction = 'C', current = {}) {
  const group = emotion || 'happiness'
  if (catalog.version !== 'v3') return { legacy_group: group }
  const motions = catalog.motions || {}
  const expressions = catalog.expressions || {}
  const requested = selection && typeof selection === 'object' ? selection : {}
  const motionFileFor = (id) => {
    const variants = Object.hasOwn(motions, id) ? motions[id] : {}
    return variants[direction] || variants[''] || variants.C || null
  }
  const fallbackReasons = []
  let motionId = motionFileFor(requested.motion) ? requested.motion : null
  if (!motionId) {
    if (typeof requested.motion === 'string' && requested.motion !== 'auto') fallbackReasons.push('motion_unavailable')
    const groups = catalog.groups || {}
    const candidates = [groups[`${group}_${direction}`], groups[group], groups.idle_motion, groups.IDLE, Object.keys(motions)]
      .map((items) => (items || []).filter((id) => motionFileFor(id)))
      .find((items) => items?.length) || []
    motionId = current.emotion === group && candidates.includes(current.motion_id) ? current.motion_id : candidates[0] || null
  }
  const motionFile = motionFileFor(motionId)
  let expression = Object.hasOwn(expressions, requested.expression) ? requested.expression : null
  if (!expression) {
    if (typeof requested.expression === 'string' && requested.expression !== 'auto') fallbackReasons.push('expression_unavailable')
    const candidates = [...(catalog.semantic_expressions?.[group] || []), ...(catalog.semantic_expressions?.idle || [])]
    expression = candidates.find((key) => Object.hasOwn(expressions, key))
      || (Object.hasOwn(expressions, current.expression) ? current.expression : null)
  }
  return {
    state: { motion_id: motionId, motion_file: motionFile, expression, emotion: group },
    change_motion: Boolean(motionFile && motionFile !== current.motion_file),
    change_expression: Boolean(expression && expression !== current.expression),
    fallback_reasons: fallbackReasons,
  }
}
