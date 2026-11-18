import { legacyRelative } from '../setup/legacy.js'
import { importedOutfitId } from '../characters/legacy-outfits.js'

/** @param {object} chat 导入聊天。 @param {object[]} warnings 迁移诊断。 @returns {number} 已整理的调用数量；只调整可确认边界的旧重建消息。 */
export function normalizePythonToolOrder(chat, warnings) {
  const entries = []
  for (const turn of chat.turns)
    for (const message of turn.messages) {
      if (message.kind === 'user') entries.push({ turn, message, first: true })
      if (message.kind === 'assistant') {
        let first = true
        for (const part of message.parts ?? [])
          if (part.kind === 'dialogue')
            for (const line of part.lines) {
              entries.push({ turn, message, first, lineId: line.id })
              first = false
            }
      }
    }
  let moved = 0
  for (const turn of chat.turns) {
    const removed = new Set()
    const insertions = new Map()
    for (const message of turn.messages) {
      if (
        message.kind !== 'assistant' ||
        !message.extensions?.migration?.reconstructed ||
        !message.parts?.some((part) => part.kind === 'tool-call')
      )
        continue
      const pairs = message.parts.map((part) =>
        turn.messages.filter(
          (result) =>
            result.kind === 'tool-result' &&
            result.assistantMessageId === message.id &&
            result.callId === part.callId
        )
      )
      const results = pairs.flat()
      const positions = new Set(
        results.map((result) => result.extensions?.pythonDisplay?.message_index)
      )
      const position = [...positions][0]
      const target = Number.isInteger(position) ? entries[position] : null
      const safe =
        message.parts.every((part) => part.kind === 'tool-call') &&
        !Object.keys(message.protocol ?? {}).length &&
        pairs.every((pair) => pair.length === 1) &&
        positions.size === 1 &&
        target?.turn === turn &&
        target.first &&
        target.message.kind === 'assistant' &&
        target.message.parts.some((part) => part.kind === 'dialogue')
      if (!safe) {
        warnings.push({
          code: 'legacy_tool_order_unresolved',
          chatId: chat.id,
          messageId: message.id
        })
        continue
      }
      const group = insertions.get(target.message.id) ?? []
      group.push(message, ...results)
      insertions.set(target.message.id, group)
      for (const item of [message, ...results]) removed.add(item.id)
      moved += message.parts.length
    }
    turn.messages = turn.messages.flatMap((message) => {
      if (removed.has(message.id)) return []
      return [...(insertions.get(message.id) ?? []), message]
    })
  }
  return moved
}

/**
 * @param {object} chat 可写导入草稿或已加载的本地聊天。
 * @param {object} scan 原始导入扫描，包含模型路径到目录身份的映射。
 * @param {{get: (id: string) => Promise<object|null>}} characters 本地角色目录。
 * @returns {Promise<{changed: boolean, models: number, calls: number, warnings: object[]}>} 一次性补齐模型选择和消息顺序；不覆盖已有本地选择。
 */
export async function normalizeImportedPythonChat(chat, scan, characters) {
  const provenance = chat.meta.extensions?.pythonImport
  const result = { changed: false, models: 0, calls: 0, warnings: [] }
  if (!provenance || provenance.source !== scan.identity || provenance.normalizationVersion >= 1)
    return result
  const python = chat.meta.extensions.python
  if (!python) return result
  const settings = python.settings ?? {}
  chat.meta.settings.characters ??= {}
  for (const roleId of chat.characterIds) {
    if (Object.hasOwn(chat.meta.settings.characters, roleId)) continue
    const role = scan.roles.find((item) => item.id === roleId)
    const actual = await characters.get(roleId)
    if (!role || !actual) continue
    const requestedForm = settings.character_forms?.[roleId]
    const formId = actual.forms.some((form) => form.id === requestedForm)
      ? requestedForm
      : actual.defaultFormId
    const overrides = {}
    for (const form of role.forms) {
      const configured =
        settings.live2d_form_models?.[roleId]?.[form.id] ?? settings.live2d_models?.[role.name]
      if (!configured) continue
      let model
      try {
        const relative = legacyRelative(scan.root, configured)
        model = form.models.find((item) => item.file === relative)
      } catch {
        /* 无效来源路径不能用文件名猜测另一份模型。 */
      }
      const mapped = importedOutfitId(
        actual.forms.find((item) => item.id === form.id),
        model?.id
      )
      if (mapped) {
        overrides[form.id] = mapped
        result.models += 1
      } else
        result.warnings.push({
          code: 'legacy_chat_model_unavailable',
          chatId: chat.id,
          characterId: roleId,
          formId: form.id
        })
    }
    if (requestedForm || Object.keys(overrides).length)
      chat.meta.settings.characters[roleId] = { formId, modelOverrides: overrides }
  }
  result.calls = normalizePythonToolOrder(chat, result.warnings)
  provenance.normalizationVersion = 1
  provenance.normalizationWarnings = result.warnings
  result.changed = true
  return result
}
