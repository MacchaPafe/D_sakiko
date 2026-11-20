import { isReplayableDialogue } from '../../../../shared/chat-history.js'

/** @typedef {{id: string, toolName: string, arguments: object, outcome: object|null, display: string|null}} ToolHistoryItem 调用与结果配对后的展示材料，不执行工具。 */
/** @typedef {import('../../../../shared/contracts/presentation.js').AudioMaterial} AudioMaterial */
/**
 * @typedef {{id: string, name: string, text: string, replayTurnId?: string} & (
 *   {kind: 'user'|'system'} |
 *   {kind: 'assistant', translation: string|null, audio: AudioMaterial|null} |
 *   {kind: 'tools', tools: ToolHistoryItem[]}
 * )} HistoryRow 有序历史投影；相邻工具合并折叠，原始记录不变。
 */

function eventText(message) {
  const text = message.content?.text ?? message.text ?? ''
  if (text.startsWith('【系统内部事件触发')) {
    const theme = text.match(/抽签主题：([\s\S]+?)。用户已点击抽签/)
    const winner = text.match(/结果是：([\s\S]+?)。请你/)
    if (theme && winner) return `抽签结果 · ${theme[1]}：${winner[1]}`
    if (text.includes('抽签')) return '抽签已完成'
    const reminder = text.match(/事件内容：【([^】]+)】/)
    if (reminder) return `定时提醒 · ${reminder[1]}`
    return '系统事件已触发'
  }
  if (message.source?.kind === 'reminder') return `定时提醒 · ${text}`
  if (message.source?.kind === 'async') {
    const results = message.results ?? message.content?.results ?? []
    return (
      results
        .map((item) => {
          if (item.toolName === 'start_lottery') {
            const winner = item.result?.label ?? item.result?.option ?? item.result?.text
            return winner ? `抽签结果 · ${item.request?.title ?? '抽签'}：${winner}` : '抽签已完成'
          }
          return '工具结果已送达'
        })
        .join('\n') || '系统事件已触发'
    )
  }
  return null
}

function toolRow(rows, id, tool) {
  const previous = rows.at(-1)
  if (previous?.kind === 'tools') previous.tools.push(tool)
  else rows.push({ id, name: '工具', text: '调用了工具', kind: 'tools', tools: [tool] })
}

/** @param {object|null} snapshot 正式聊天快照。 @returns {HistoryRow[]} 直接遵循正式消息顺序，前端不解释 Python 位置索引。 */
export function historyRows(snapshot) {
  const rows = []
  let scriptAnchor = null
  const theatre = snapshot?.chat.mode === 'scripted-dialogue'
  for (const turn of snapshot?.chat.turns ?? []) {
    let userRow
    let firstLine
    const paired = new Set()
    const results = (turn.messages ?? []).filter((message) => message.kind === 'tool-result')
    for (const [index, message] of (turn.messages ?? []).entries()) {
      const id = message.id ?? `${turn.id}:${index}`
      if (message.kind === 'user') {
        const event = eventText(message)
        const manual = message.source?.kind === 'manual' && !event
        const row = {
          id,
          name: manual ? '你' : '系统',
          text: event ?? message.content?.text ?? message.text ?? '',
          kind: manual ? 'user' : 'system'
        }
        rows.push(row)
        if (manual && (!userRow || turn.input?.messageId === id)) userRow = row
      } else if (message.kind === 'assistant') {
        for (const [partIndex, part] of (message.parts ?? []).entries()) {
          if (part.kind === 'dialogue') {
            for (const line of part.lines) {
              const row = {
                id: line.id,
                name: line.speakerName ?? line.speakerId,
                text: line.text,
                translation: line.translation?.trim() ? line.translation : null,
                audio: line.audio ?? null,
                kind: 'assistant'
              }
              rows.push(row)
              if (isReplayableDialogue(line)) firstLine ??= row
            }
          } else if (part.kind === 'tool-call') {
            const result = results.find(
              (item) =>
                part.callId && item.assistantMessageId === message.id && item.callId === part.callId
            )
            if (result) paired.add(result.id)
            const key = `${id}:${part.id ?? partIndex}`
            toolRow(rows, key, {
              id: key,
              toolName: part.toolName ?? part.name ?? '工具调用',
              arguments: part.arguments ?? {},
              outcome: result?.outcome ?? null,
              display: result?.extensions?.pythonDisplay?.result_content ?? null
            })
          }
        }
      } else if (message.kind === 'tool-result' && !paired.has(id)) {
        toolRow(rows, id, {
          id,
          toolName: '工具结果',
          arguments: {},
          outcome: message.outcome ?? null,
          display: message.content?.text ?? message.text ?? null
        })
      }
    }
    if (firstLine && snapshot.activeTurnId !== turn.id) {
      if (theatre && !scriptAnchor && !snapshot.activeTurnId) {
        scriptAnchor = firstLine
        scriptAnchor.replayTurnId = turn.id
      } else if (!theatre && userRow) userRow.replayTurnId = turn.id
    }
    if (turn.problem)
      rows.push({
        id: turn.id + ':problem',
        name: '生成问题',
        text: turn.problem.message,
        kind: 'system'
      })
  }
  return rows
}
