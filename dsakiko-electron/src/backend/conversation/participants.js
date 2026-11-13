import { requireValue } from '../core/support.js'

/** 旧存档优先使用留存的显示名，其次使用历史轮次中的人物快照。 */
export function historicalCharacterNames(chat) {
  const names = new Map(Object.entries(chat.meta.extensions?.characterNames ?? {}))
  for (const turn of chat.turns)
    for (const character of turn.meta.characterContext?.characters ?? [])
      if (character.name) names.set(character.id, character.name)
  return Object.fromEntries(names)
}

/** 依据当前目录判断是否允许继续对话，历史媒体仍可独立读取。 */
export function participantIssue(chat, existingIds, names) {
  const ids = new Set(chat.characterIds)
  if (chat.mode === 'scripted-dialogue' && (ids.size !== 2 || chat.characterIds.length !== 2))
    return { code: 'ambiguous-theater-characters', characters: [] }
  const missing = [...ids]
    .filter((id) => !existingIds.has(id))
    .map((id) => ({ id, name: Object.hasOwn(names, id) ? names[id] : null }))
  return missing.length ? { code: 'missing-characters', characters: missing } : null
}

/** 使用与对话快照一致的业务错误阻止生成或修改历史。 */
export function requireParticipants(issue) {
  requireValue(
    !issue,
    issue?.code === 'ambiguous-theater-characters'
      ? 'ambiguous_theater_characters'
      : 'character_missing',
    issue?.code === 'ambiguous-theater-characters'
      ? '无法区分原小剧场的两位角色'
      : '对话关联的角色已不存在。'
  )
}
