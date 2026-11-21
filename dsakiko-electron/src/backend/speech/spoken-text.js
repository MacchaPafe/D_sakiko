import { dialogueMarkdown } from '../../shared/dialogue-markdown.js'

/** @param {string} text 存档或新收到的角色台词。 @returns {string} 可朗读原稿。 */
export function spokenText(text) {
  return dialogueMarkdown(text).speech
}

/** @param {string} text 可朗读原稿。
 * @param {Array<{text: string, reading: string}>} overrides 按声明顺序应用的读音。
 * @returns {string} 实际送给语音引擎的文本。
 */
export function applyPronunciation(text, overrides = []) {
  for (const { text: source, reading } of overrides) {
    if (source) text = text.replaceAll(source, () => reading)
  }
  return /[\p{L}\p{N}]/u.test(text) ? text : ''
}
