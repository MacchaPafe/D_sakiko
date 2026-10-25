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
