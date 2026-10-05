/**
 * 根据演出进度显示台词和翻译；没有运行态的历史记录完整展示。
 * @param {import('../../../../shared/contracts/conversation.js').Message} entry 持久消息。
 * @param {import('../../../../shared/contracts/conversation.js').MessageDisplay|undefined} display 运行态显示进度。
 * @returns {{visible: boolean, text: string, translation: string|null}} 用于消息气泡的显示内容。
 */
export function displayMessage(entry, display) {
  if (entry.kind !== 'character' || !display || display.mode === 'full')
    return { visible: true, text: entry.text, translation: entry.translation }
  if (display.mode === 'pending' || display.revealedCharacters <= 0)
    return { visible: false, text: '', translation: null }
  const characters = [...entry.text]
  const count = Math.min(characters.length, display.revealedCharacters)
  let translation = null
  if (entry.translation) {
    const translated = [...entry.translation]
    const translatedCount = Math.ceil((translated.length * count) / Math.max(1, characters.length))
    translation = translated.slice(0, translatedCount).join('')
  }
  return { visible: true, text: characters.slice(0, count).join(''), translation }
}
