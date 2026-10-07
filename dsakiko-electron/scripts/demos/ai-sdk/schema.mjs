import { z } from 'zod'

// Schema 保持固定；角色和演出目录变化只影响提示词及业务校验。
export const replySchema = z
  .object({
    lines: z
      .array(
        z
          .object({
            speakerId: z.string().min(1),
            text: z.string().trim().min(1).max(1000),
            translation: z.string().nullable(),
            emotion: z.string().nullable(),
            performance: z.object({ motion: z.string(), expression: z.string() }).strict()
          })
          .strict()
      )
      .min(1)
      .max(12)
  })
  .strict()

/**
 * 在结构校验之外核对本轮允许的身份和演出。
 * @param {unknown} value JSON 值或 SDK 结构化结果。
 * @param {import('../../../src/backend/llm/interface.js').AgentCharacter[]} characters 角色快照。
 * @returns {import('../../../src/backend/llm/interface.js').GeneratedLine[]} 完整合法台词。
 */
export function validateReply(value, characters) {
  const reply = replySchema.parse(value)
  return reply.lines.map((line) => {
    const character = characters.find((item) => item.id === line.speakerId)
    if (!character) throw new Error('speakerId 不属于本轮角色。')
    for (const [field, catalog] of [
      ['motion', 'motions'],
      ['expression', 'expressions']
    ]) {
      const id = line.performance[field]
      if (id !== 'auto' && !character.performances[catalog].some((item) => item.id === id)) {
        throw new Error('演出编号不属于该角色目录。')
      }
    }
    return { kind: 'character', ...line }
  })
}

/** @type {import('../../../src/backend/llm/interface.js').AgentCharacter[]} */
export const demoCharacters = [
  {
    id: 'sakiko',
    displayName: '祥子',
    description: '说话简洁有礼，用一句中文回答。',
    performances: {
      motions: [{ id: 'greeting', description: '挥手问候' }],
      expressions: [{ id: 'smile', description: '微笑' }]
    }
  }
]

export const timeParametersSchema = {
  type: 'object',
  properties: {},
  additionalProperties: false
}

/**
 * 只读时间工具，可供自动循环及受控循环共用。
 * @param {'visible'|'hidden'} [visibility] 是否交付聊天记录。
 * @returns {import('../../../src/backend/llm/interface.js').AgentTool} 有作用域的工具。
 */
export function createTimeTool(visibility = 'visible') {
  return {
    name: 'get_current_time',
    description: '读取当前日期、时间和系统时区。',
    parametersSchema: timeParametersSchema,
    visibility,
    async execute(_parameters, signal) {
      signal.throwIfAborted()
      const now = new Date()
      return {
        iso: now.toISOString(),
        local: now.toLocaleString('zh-CN', { hour12: false }),
        timeZone: Intl.DateTimeFormat().resolvedOptions().timeZone
      }
    }
  }
}
