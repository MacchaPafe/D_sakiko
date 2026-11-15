/** @param {object|null} chat 聊天存档。 @returns {object|null} 最后一条非空角色台词，排除工具、思考与事件输入。 */
export function lastDialogue(chat) {
  for (const turn of [...(chat?.turns ?? [])].reverse())
    for (const message of [...turn.messages].reverse())
      if (message.kind === 'assistant')
        for (const part of [...(message.parts ?? [])].reverse())
          if (part.kind === 'dialogue')
            for (const line of [...part.lines].reverse()) if (line.text?.trim()) return line
  return null
}
