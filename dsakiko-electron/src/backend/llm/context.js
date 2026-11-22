import { copy } from '../core/support.js'

/** @typedef {import('../../shared/contracts/characters.js').CharacterContext} CharacterContext */

/**
 * 从能力快照提取当前激活人格的人物资料；仅包含本轮选用形态与自称模式。
 * @param {Pick<import('./interface.js').GenerationRequest, 'characters' | 'userPersona'>} request 本轮材料。
 * @returns {CharacterContext} 可保存和比较的人物资料。
 */
export function characterContext(request) {
  return {
    characters: request.characters.map((character) => {
      const activeForm = character.forms?.find((form) => form.id === character.formId) ?? {
        id: character.formId,
        displayName: character.displayName,
        description: character.description
      }
      return {
        id: character.id,
        name: character.name ?? character.displayName,
        selfNameMode: character.selfNameMode ?? 'character',
        forms: [copy(activeForm)]
      }
    }),
    persona: copy(request.userPersona)
  }
}

/**
 * @param {object[]} characters 本轮参与角色与所选人格。
 * @returns {object[]} 含自我介绍名称及唯一台词署名的本轮角色。
 */
export function speakerNames(characters) {
  const actors = characters.map((character) => {
    const name = character.name ?? character.displayName
    const selfNameMode = character.selfNameMode ?? 'character'
    const selfName = selfNameMode === 'form' ? character.displayName : name
    return { ...character, name, selfNameMode, selfName, speaker: selfName }
  })
  for (const actor of actors) {
    if (actors.some((other) => other !== actor && other.selfName === actor.selfName))
      actor.speaker = actor.name + '（' + actor.displayName + '）'
  }
  return actors
}

/**
 * 历史台词对 LLM 只暴露当时的角色名与内容，内部身份留在存档中。
 * @param {object[]} lines 已保存的台词。
 * @param {CharacterContext|null} context 该轮可用的人物快照。
 * @returns {object[]} 面向模型的历史输出。
 */
export function dialogueProjection(lines, context) {
  return lines.map((line) => ({
    speaker:
      line.speakerLabel ??
      context?.characters.find((character) => character.id === line.speakerId)?.name ??
      '未知角色',
    text: line.text,
    translation: line.translation,
    emotion: line.emotion,
    ...(line.performance &&
    (line.performance.motion !== 'auto' || line.performance.expression !== 'auto')
      ? { performance: copy(line.performance) }
      : {})
  }))
}
