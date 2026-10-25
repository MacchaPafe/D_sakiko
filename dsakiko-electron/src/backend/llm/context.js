import { copy } from '../core/support.js'

/** @typedef {import('../../shared/contracts/characters.js').CharacterContext} CharacterContext */

/**
 * 从能力快照提取人物资料；形态来回切换不改变此投影。
 * @param {Pick<import('./interface.js').GenerationRequest, 'characters' | 'userPersona'>} request 本轮材料。
 * @returns {CharacterContext} 可保存和比较的人物资料。
 */
export function characterContext(request) {
  return {
    characters: request.characters.map((character) => ({
      id: character.id,
      name: character.name ?? character.displayName,
      forms: copy(
        character.forms ?? [
          {
            id: character.formId,
            displayName: character.displayName,
            description: character.description
          }
        ]
      )
    })),
    persona: copy(request.userPersona)
  }
}

/**
 * 历史台词对 LLM 只暴露当时的角色名与内容，内部身份留在存档中。
 * @param {object[]} lines 已保存的台词。
 * @param {CharacterContext} context 该轮人物快照。
 * @returns {object[]} 面向模型的历史输出。
 */
export function dialogueProjection(lines, context) {
  return lines.map((line) => ({
    speaker:
      context.characters.find((character) => character.id === line.speakerId)?.name ??
      line.speakerId,
    text: line.text,
    translation: line.translation,
    emotion: line.emotion,
    ...(line.performance &&
    (line.performance.motion !== 'auto' || line.performance.expression !== 'auto')
      ? { performance: copy(line.performance) }
      : {})
  }))
}
