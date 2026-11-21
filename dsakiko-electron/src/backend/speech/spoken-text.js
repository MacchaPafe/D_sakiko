const closingBracket = new Map([
  ['（', '）'],
  ['(', ')'],
  ['[', ']'],
  ['【', '】']
])
const closingMarks = new Set(closingBracket.values())

/**
 * @param {string} text 台词或存档里的朗读文本。
 * @param {Array<{text: string, reading: string}>} [overrides] 按声明顺序应用的读音替换。
 * @returns {string} 可交给语音引擎的文本；只有动作和标点时返回空串。
 */
export function spokenText(text, overrides = []) {
  const stack = []
  const endByStart = new Map()
  for (let i = 0; i < text.length; i++) {
    const mark = text[i]
    if (closingBracket.has(mark)) stack.push(i)
    else if (closingMarks.has(mark)) {
      const start = stack.at(-1)
      if (start !== undefined && closingBracket.get(text[start]) === mark) {
        endByStart.set(stack.pop(), i)
      } else {
        // 交叉配对的范围有歧义，保留其中的文字。
        stack.length = 0
      }
    }
  }
  let result = ''
  for (let i = 0; i < text.length; i++) {
    if (endByStart.has(i)) i = endByStart.get(i)
    else result += text[i]
  }
  result = result.replace(/[「」『』]/gu, '').trim()
  for (const { text: source, reading } of overrides) {
    if (source) result = result.replaceAll(source, () => reading)
  }
  return /[\p{L}\p{N}]/u.test(result) ? result : ''
}
