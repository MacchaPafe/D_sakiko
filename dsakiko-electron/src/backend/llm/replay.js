import { createHash } from 'node:crypto'
import { copy, requireValue } from '../core/support.js'
import { normalizeConnection, requiresStableThinkingPrefix } from './connections.js'

const metadataFields = {
  anthropic: ['signature', 'redactedData'],
  openai: ['itemId', 'reasoningEncryptedContent', 'phase', 'parallelToolCall'],
  google: ['thoughtSignature'],
  openaiCompatible: ['thoughtSignature']
}

/** 按协议允许字段保存元数据，不保存 SDK 响应对象、HTTP 头或凭据。 */
function metadata(value) {
  const result = {}
  for (const [namespace, keys] of Object.entries(metadataFields)) {
    const selected = {}
    for (const key of keys)
      if (value?.[namespace]?.[key] != null) selected[key] = copy(value[namespace][key])
    if (Object.keys(selected).length) result[namespace] = selected
  }
  return result
}

/** 稳定序列化避免存档键顺序变化使协议材料无故失效。 */
function canonical(value) {
  if (Array.isArray(value)) return value.map(canonical)
  if (value && typeof value === 'object')
    return Object.fromEntries(
      Object.keys(value)
        .sort()
        .filter((key) => value[key] !== undefined)
        .map((key) => [key, canonical(value[key])])
    )
  return value
}

/** 计算不可逆摘要，仅用于识别连接来源和内容编辑，不保存敏感配置原文。
 * @param {unknown} value 可序列化材料。
 * @returns {string} 内容摘要。
 */
export function fingerprint(value) {
  return createHash('sha256')
    .update(JSON.stringify(canonical(value)))
    .digest('hex')
}

/** 只比较业务语义，忽略分支重新分配的身份、音频和协议副本。
 * @param {object[]} parts 业务响应片段。
 * @returns {object[]} 可比较的投影。
 */
export function semanticParts(parts) {
  return parts.map((part) => {
    if (part.kind === 'reasoning') return { kind: part.kind, text: part.text }
    if (part.kind === 'tool-call')
      return {
        kind: part.kind,
        callId: part.callId,
        toolName: part.toolName,
        arguments: part.arguments
      }
    return {
      kind: 'dialogue',
      lines: part.lines.map(({ speakerId, formId, text, translation, emotion, performance }) => ({
        speakerId,
        formId,
        text,
        translation,
        emotion,
        performance
      }))
    }
  })
}

/** 供编辑检测和摘要使用的纯业务历史，不夹带密文或签名。
 * @param {object[]} turns 上下文轮次。
 * @returns {object[]} 纯业务投影。
 */
export function semanticHistory(turns) {
  return turns.map((turn) => ({
    characterContext: turn.characterContext ?? null,
    messages: turn.messages.map((message) => {
      if (message.kind === 'assistant')
        return { kind: message.kind, parts: semanticParts(message.parts) }
      if (message.kind === 'user')
        return {
          kind: message.kind,
          source: message.source,
          text: message.text,
          attachments: message.attachments ?? []
        }
      if (message.kind === 'abstract') return { kind: message.kind, text: message.text }
      return {
        kind: message.kind,
        callId: message.callId,
        toolName: message.toolName,
        outcome: message.outcome
      }
    })
  }))
}

/** 本轮输入与此前历史的摘要；临时控制消息另按协议处理，不存入历史。
 * @param {object[]} history 之前的历史。
 * @param {object} context 当前人物资料。
 * @param {object | null} user 本轮用户输入；小剧场为 null。
 * @returns {string} 编辑检测摘要。
 */
export function turnFingerprint(history, context, user) {
  return fingerprint([
    ...semanticHistory(history),
    ...semanticHistory([{ characterContext: context, messages: user ? [user] : [] }])
  ])
}

function origin(connection) {
  const value = normalizeConnection(connection)
  return {
    connectionId: value.connectionId ?? null,
    modelId: value.modelId,
    provider: value.provider,
    protocol: value.protocol,
    endpoint: fingerprint(value.baseUrl)
  }
}

/** 从 SDK 完整内容提取有序可回传块；空文本签名也需要保存。
 * @param {object} response SDK 单步响应。
 * @returns {object[]} SDK assistant 内容块。
 */
export function responseContent(response) {
  // 注入式测试或旧调用方可只提供简化字段；生产 SDK 的 content 是权威来源。
  const source = response.content ?? [
    ...(response.reasoningText != null
      ? [{ type: 'reasoning', text: response.reasoningText }]
      : []),
    ...(response.toolCalls ?? []).map((call) => ({ ...call, type: 'tool-call' })),
    ...(response.text ? [{ type: 'text', text: response.text }] : [])
  ]
  return source.map((part) => {
    requireValue(
      ['reasoning', 'text', 'tool-call'].includes(part.type) && !part.providerExecuted,
      'unsupported_model_output',
      '模型返回了尚未支持的内容或服务端工具结果。'
    )
    const providerOptions = metadata(part.providerMetadata ?? part.providerOptions)
    if (part.type === 'tool-call')
      return {
        type: part.type,
        toolCallId: part.toolCallId,
        toolName: part.toolName,
        input: copy(part.input),
        providerOptions
      }
    return { type: part.type, text: part.text, providerOptions }
  })
}

/** 提取兼容 Completions 的消息级推理材料；不保存响应其他统计和 HTTP 信息。
 * @param {object} response SDK 响应。
 * @returns {object} SDK 消息级 providerOptions。
 */
export function responseOptions(response) {
  const details = response.providerMetadata?.openaiCompatible?.reasoningDetails
  if (!Array.isArray(details)) return {}
  const selected = details.map((part) =>
    Object.fromEntries(
      Object.entries(part).filter(
        ([key, value]) =>
          ['type', 'format', 'index', 'id', 'text', 'data', 'signature', 'summary'].includes(key) &&
          (typeof value === 'string' || (key === 'index' && Number.isInteger(value)))
      )
    )
  )
  return { openaiCompatible: { reasoning_details: selected } }
}

/** 建立独立于厂商原始响应的本地回传记录。
 * @param {object} connection 固定连接。
 * @param {object[]} content SDK 有序内容。
 * @param {object[]} parts 已接受的业务片段。
 * @param {string} contextFingerprint 本轮之前的业务上下文摘要。
 * @param {object} [providerOptions] 消息级协议材料。
 * @returns {object} 可写入 AssistantMessage.protocol 的 JSON 对象。
 */
export function saveReplay(connection, content, parts, contextFingerprint, providerOptions = {}) {
  return {
    version: 1,
    origin: origin(connection),
    content: copy(content),
    providerOptions: copy(providerOptions),
    projectionFingerprint: fingerprint(semanticParts(parts)),
    contextFingerprint
  }
}

/** 恢复有效的原始内容；编辑或切换来源后只使用通用业务投影。
 * @param {object} message 历史 assistant 消息。
 * @param {object} connection 当前连接。
 * @param {string} contextFingerprint 当前重建上下文的摘要。
 * @returns {object[] | null} 可回传块；null 表示应重新投影业务内容。
 */
export function restoreReplay(message, connection, contextFingerprint) {
  const saved = message.protocol
  if (
    saved?.version !== 1 ||
    !Array.isArray(saved.content) ||
    fingerprint(saved.origin) !== fingerprint(origin(connection)) ||
    saved.projectionFingerprint !== fingerprint(semanticParts(message.parts)) ||
    saved.contextFingerprint !== contextFingerprint
  )
    return null
  // 每轮控制消息都被移除，因此官方前缀绑定模型的旧推理必然失效；只过滤发出的副本。
  const content = requiresStableThinkingPrefix(connection)
    ? saved.content.filter((part) => part.type !== 'reasoning')
    : saved.content
  return responseContent({ content })
}
