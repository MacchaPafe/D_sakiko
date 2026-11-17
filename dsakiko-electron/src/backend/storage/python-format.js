import { requireValue } from '../core/support.js'
import { extractAssetReferences } from '../resources/references.js'

/** @param {object} chat Python 原始存档。 @returns {boolean} 是否采用共同轮次结构。 */
export function isTimelineChat(chat) {
  return Object.hasOwn(chat, 'turns') || Object.hasOwn(chat, 'characterIds')
}

/** @param {object} chat Python 原始存档。 @returns {void} 拒绝未知结构，避免成功写入空白替代品。 */
export function validatePythonChat(chat) {
  const check = (valid) => requireValue(valid, 'legacy_chat_invalid', 'Python 聊天存档结构无效。')
  check(chat && typeof chat === 'object')
  if (!isTimelineChat(chat)) {
    check(Array.isArray(chat.message_list))
    for (const message of chat.message_list) {
      check(message && typeof message === 'object' && !Array.isArray(message))
      check(message.text == null || typeof message.text === 'string')
      if (message.attachments != null)
        check(
          Array.isArray(message.attachments) &&
            message.attachments.every(
              (item) => item && typeof item === 'object' && !Array.isArray(item)
            )
        )
    }
    return
  }
  check(typeof chat.id === 'string' && chat.id.length > 0 && typeof chat.title === 'string')
  check(Array.isArray(chat.characterIds) && chat.characterIds.every((id) => typeof id === 'string'))
  check(['single-character', 'scripted-dialogue'].includes(chat.mode))
  check(Array.isArray(chat.turns) && chat.meta && typeof chat.meta === 'object')
  const identities = new Set()
  function identity(value) {
    check(typeof value === 'string' && value.length > 0 && !identities.has(value))
    identities.add(value)
  }
  for (const turn of chat.turns) {
    identity(turn.id)
    check(turn.meta && turn.input && Array.isArray(turn.messages))
    for (const message of turn.messages) {
      identity(message.id)
      check(['user', 'assistant', 'tool-result', 'abstract'].includes(message.kind))
      if (message.kind === 'user')
        check(
          typeof message.content?.text === 'string' && Array.isArray(message.content.attachments)
        )
      if (message.kind === 'assistant') {
        check(Array.isArray(message.parts))
        for (const part of message.parts) {
          identity(part.id)
          check(['dialogue', 'reasoning', 'tool-call'].includes(part.kind))
          if (part.kind === 'dialogue') {
            check(Array.isArray(part.lines))
            for (const line of part.lines) {
              identity(line.id)
              check(typeof line.text === 'string' && typeof line.speakerId === 'string')
            }
          }
        }
      }
    }
    if (turn.input.kind === 'message')
      check(
        turn.messages.some(
          (message) => message.id === turn.input.messageId && message.kind === 'user'
        )
      )
  }
}

/** @param {object} theater Python 的小剧场设置。 @param {string[]} characterIds 按旧剧本顺序保留的角色 ID。 @param {string} fallback 旧版开场白。 @returns {import('../../shared/contracts/settings.js').ChatSettings['scenario']} 供新剧场继续生成的场景设置。 */
export function legacyScenario(theater, characterIds, fallback = '') {
  function string(value) {
    return typeof value === 'string' ? value : ''
  }
  return {
    situation: string(theater?.situation ?? fallback),
    characters: Object.fromEntries(
      characterIds.map((id, index) => {
        const actor = theater?.['character_' + index]
        return [
          id,
          {
            speakingStyle: string(actor?.talk_style),
            interaction: string(actor?.interaction_details)
          }
        ]
      })
    )
  }
}

/** @param {object} chat 共同格式存档。 @returns {{id: string, path: string|null, purpose: string}[]} 只扫描实际引用的文件，不复制整个资源索引。 */
export function pythonMedia(chat) {
  const resources = chat.meta?.extensions?.python?.resources ?? {}
  const audio = new Set()
  for (const turn of chat.turns)
    for (const message of turn.messages)
      for (const part of message.parts ?? [])
        for (const line of part.lines ?? []) if (line.audio?.asset) audio.add(line.audio.asset.id)
  return extractAssetReferences(chat).map(({ id }) => ({
    id,
    path: typeof resources[id] === 'string' ? resources[id] : null,
    purpose: audio.has(id) ? 'audio' : 'attachment'
  }))
}

/** @param {object} original 原始共同格式存档。 @param {object} context 本地资源与身份材料。 @returns {object} 保留轮次和协议历史、清除运行状态的导入草稿。 */
export function importTimeline(original, { id, revision = 0, scan, refs, warnings }) {
  const missing = new Set()
  const paths = original.meta.extensions?.python?.resources ?? {}
  function remap(value) {
    if (Array.isArray(value)) return value.map(remap)
    if (!value || typeof value !== 'object') return value
    if (Object.keys(value).length === 1 && typeof value.id === 'string') {
      const replacement = refs[scan.media[paths[value.id]]]
      if (!replacement) missing.add(value.id)
      return replacement ? structuredClone(replacement) : null
    }
    return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, remap(item)]))
  }
  const chat = remap(original)
  chat.id = id
  chat.revision = revision
  chat.meta.reminders = []
  chat.meta.lotteries = []
  chat.meta.extensions ??= {}
  const extensions = chat.meta.extensions
  for (const key of ['acceptedRequests', 'reminderCalls', 'fileCalls', 'firstTurn'])
    delete extensions[key]
  extensions.characterNames = {
    ...(extensions.python?.characterNames ?? {}),
    ...(extensions.characterNames ?? {})
  }
  extensions.pythonImport = { source: scan.identity, chatId: original.id, format: 1 }
  if (extensions.python) extensions.python.resources = {}
  const theater = extensions.python?.settings?.theater
  if (chat.mode === 'scripted-dialogue' && theater && chat.meta.settings?.scenario == null) {
    chat.meta.settings ??= {}
    chat.meta.settings.scenario = legacyScenario(theater, chat.characterIds)
  }
  // Python 的连接 ID 不属于新目录；新对话设置使用导入时匹配到的连接，历史快照仍完整保留。
  for (const key of ['connectionId', 'modelId']) delete chat.meta.settings?.generation?.[key]
  delete extensions.modelSelectionVersion
  for (const turn of chat.turns) {
    turn.meta.requestId = null
    if (turn.meta.extensions) delete turn.meta.extensions.requestFingerprint
    for (const message of turn.messages) {
      if (message.kind === 'user')
        message.content.attachments = message.content.attachments.filter(
          (attachment) => attachment.asset
        )
      for (const part of message.parts ?? [])
        for (const line of part.lines ?? []) if (line.audio && !line.audio.asset) line.audio = null
    }
  }
  for (const assetId of missing)
    warnings.push({ code: 'legacy_chat_media_unavailable', chatId: id, assetId })
  return chat
}
