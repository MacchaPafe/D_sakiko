/**
 * 创建或切换角色时固定默认形态，同一角色已有的明确选择不得被覆盖。
 * @param {import('../../../../shared/contracts/persona.js').UserPersonaDefinition['source']} source 当前编辑的身份来源。
 * @param {{id: string, defaultFormId: string} | undefined} character 所选角色摘要；目录为空时无选择。
 * @returns {import('../../../../shared/contracts/persona.js').CharacterPersonaSource} 编辑中的形态引用，空目录下禁止提交。
 */
export function selectPersonaCharacter(source, character) {
  if (source.kind === 'character' && character && source.characterId === character.id)
    return { ...source }
  return {
    kind: 'character',
    characterId: character?.id ?? '',
    formId: character?.defaultFormId ?? ''
  }
}

/** @param {import('../../../../shared/contracts/persona.js').UserPersonaSummary[]} personas 身份目录。 @returns {Array<{value: string, label: string, disabled: boolean}>} 只在选择列表里区分同名身份。 */
export function personaOptions(personas) {
  const counts = new Map()
  return personas.map((item) => {
    const name = item.displayName || '来源不可用'
    const count = (counts.get(name) ?? 0) + 1
    counts.set(name, count)
    return {
      value: item.id,
      label: count === 1 ? name : name + ' (' + count + ')',
      disabled: Boolean(item.problem)
    }
  })
}

/** @param {import('../../../../shared/contracts/persona.js').UserPersonaDefinition['source']} source 自定义或角色形态来源。 @returns {import('../../../../shared/contracts/persona.js').UserPersonaDefinition} 尚未写入目录的身份。 */
export function newPersona(source) {
  return { id: crypto.randomUUID(), revision: 0, source }
}
