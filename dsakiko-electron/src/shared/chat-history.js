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
 * @returns {{line: import('./contracts/conversation.js').DialogueLine, translation: string|null, ref: import('./contracts/conversation.js').LineRef}|null} 最后一条非空角色台词、可用的中文译文及其存档位置。
 */
export function lastDialogueEntry(chat, now = Infinity) {
  const hidden = new Set(
    (chat?.meta?.lotteries ?? [])
      .filter((item) => item.revealAt > now)
      .map((item) => item.deliveredTurnId)
  )
  for (const turn of [...(chat?.turns ?? [])].reverse().filter((turn) => !hidden.has(turn.id)))
    for (const message of [...turn.messages].reverse())
      if (message.kind === 'assistant')
        for (const part of [...(message.parts ?? [])].reverse())
          if (part.kind === 'dialogue')
            for (const line of [...part.lines].reverse())
              if (line.text?.trim()) {
                const generation = turn.meta?.generation
                const language = line.spoken?.language ?? generation?.language
                // 早期存档只有原文与译文。缺少语言标记时沿用旧版的中文译文字段。
                const useTranslation =
                  !['Chinese', 'zh', 'all_zh'].includes(language) &&
                  (!generation?.translationLanguage || generation.translationLanguage === 'Chinese')
                return {
                  line,
                  translation: useTranslation ? line.translation?.trim() || null : null,
                  ref: { turnId: turn.id, messageId: message.id, partId: part.id, lineId: line.id }
                }
              }
  return null
}

/** @param {import('./contracts/conversation.js').Chat|null} chat 聊天存档。 @returns {import('./contracts/conversation.js').DialogueLine|null} 最后一条非空角色台词。 */
export function lastDialogue(chat, now = Infinity) {
  return lastDialogueEntry(chat, now)?.line ?? null
}

/**
 * @param {import('./contracts/conversation.js').Chat} chat 台词所在的存档。
 * @param {NonNullable<ReturnType<typeof lastDialogueEntry>>} entry 历史末句。
 * @returns {{personalityName: string|undefined, name: string}} 当时使用的人格名称及存档中的角色名称。
 */
export function dialogueSpeakerNames(chat, { line, ref }) {
  const character = chat.turns
    .find((turn) => turn.id === ref.turnId)
    ?.meta?.characterContext?.characters?.find((actor) => actor.id === line.speakerId)
  return {
    personalityName: character?.forms.find((form) => form.id === line.formId)?.displayName,
    name: chat.meta.extensions?.characterNames?.[line.speakerId] ?? line.speakerId
  }
}
