import entries from './bang-dream.json' with { type: 'json' }

/** @typedef {{id: string, name: string, displayName: string, band: string | null, aliases: string[], bestdoriId: number | null}} BangDreamCharacter 邦角色目录身份、显示名、乐队分组及资源索引。 */
/** @type {Map<string, BangDreamCharacter>} */
export const bangDreamCharacters = new Map(entries.map((entry) => [entry.id, entry]))

function normalize(name) {
  return name.normalize('NFKC').replace(/\s+/gu, '').toLowerCase()
}

const names = new Map()
for (const entry of entries) {
  const candidates = [entry.id, entry.name, entry.displayName, ...entry.aliases]
  for (const name of new Set(candidates.map(normalize))) {
    if (!names.has(name)) names.set(name, new Set())
    names.get(name).add(entry.id)
  }
}

/**
 * 只为尚未确认归属的角色建议导入匹配；显式归属和精确 ID 优先占用匹配。
 * @param {Array<{id: string, name: string, bangDreamId?: string|null}>} incoming 本次待导入角色。
 * @param {Array<{id: string, name: string, bangDreamId?: string|null}>} existing 参与消歧的现有角色。
 * @returns {Map<string, string>} 本次可安全应用的本地 ID 到邦角色 ID 对应关系。
 */
export function matchBangDreamCharacters(incoming, existing = []) {
  const roles = new Map(incoming.map((role) => [role.id, role]))
  for (const role of existing) roles.set(role.id, role)
  const occupied = new Set()
  const byId = new Map()
  const byName = new Map()
  const suggestions = new Map()
  for (const role of roles.values()) {
    if (role.bangDreamId !== undefined) {
      if (role.bangDreamId) occupied.add(role.bangDreamId)
      continue
    }
    if (bangDreamCharacters.has(role.id)) {
      occupied.add(role.id)
      byId.set(role.id, role.id)
      continue
    }
    for (const id of names.get(normalize(role.name)) ?? []) {
      if (!byName.has(id)) byName.set(id, new Set())
      byName.get(id).add(role.id)
    }
  }
  for (const role of incoming) {
    if (byId.has(role.id)) {
      suggestions.set(role.id, role.id)
      continue
    }
    const matches = names.get(normalize(roles.get(role.id).name))
    if (roles.get(role.id).bangDreamId !== undefined || matches?.size !== 1) continue
    const [id] = matches
    if (!occupied.has(id) && byName.get(id)?.size === 1) suggestions.set(role.id, id)
  }
  return suggestions
}
