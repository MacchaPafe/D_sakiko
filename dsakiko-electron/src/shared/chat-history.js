/** @param {import('./contracts/conversation.js').DialogueLine} line 历史台词。 @returns {boolean} 是否加入历史演出。 */
export function isReplayableDialogue(line) {
  return !(
    line.extensions?.pythonToolPlaceholder === true &&
    line.text === '...' &&
    !line.translation?.trim() &&
    !line.audio
  )
}

/**
 * @param {import('./contracts/conversation.js').Chat|null} chat 聊天存档。
 * @returns {{line: import('./contracts/conversation.js').DialogueLine, ref: import('./contracts/conversation.js').LineRef}|null} 最后一条非空角色台词及其存档位置。
 */
export function lastDialogueEntry(chat) {
  for (const turn of [...(chat?.turns ?? [])].reverse())
    for (const message of [...turn.messages].reverse())
      if (message.kind === 'assistant')
        for (const part of [...(message.parts ?? [])].reverse())
          if (part.kind === 'dialogue')
            for (const line of [...part.lines].reverse())
              if (line.text?.trim())
                return {
                  line,
                  ref: { turnId: turn.id, messageId: message.id, partId: part.id, lineId: line.id }
                }
  return null
}

/** @param {import('./contracts/conversation.js').Chat|null} chat 聊天存档。 @returns {import('./contracts/conversation.js').DialogueLine|null} 最后一条非空角色台词。 */
export function lastDialogue(chat) {
  return lastDialogueEntry(chat)?.line ?? null
}
