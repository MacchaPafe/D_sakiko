import { providerPresets } from '../../shared/contracts/llm-providers.js'
import { requireValue } from '../core/support.js'

/** 统一旧协议别名，返回可验证的连接配置；不接触凭据。
 * @param {object} connection 保存定义或解析快照。
 * @returns {object} 规范化的连接副本。
 */
export function normalizeConnection(connection) {
  const preset = providerPresets.find((item) => item.id === connection.provider)
  requireValue(preset, 'unsupported_provider')
  let protocol = connection.protocol || preset.protocol
  if (['chat', 'openai-compatible'].includes(protocol)) protocol = 'chat-completions'
  requireValue(
    ['chat-completions', 'responses', 'anthropic-messages', 'google-generative-ai'].includes(
      protocol
    ),
    'unsupported_protocol'
  )
  const baseUrl = (connection.baseUrl || preset.baseUrl).replace(/\/+$/, '')
  let url
  try {
    url = new URL(baseUrl)
  } catch {
    requireValue(false, 'invalid_connection', '需要有效的模型服务地址。')
  }
  requireValue(
    ['http:', 'https:'].includes(url.protocol) &&
      !url.username &&
      !url.password &&
      !url.search &&
      !url.hash,
    'invalid_connection',
    '服务地址不得包含凭据、查询参数或片段。'
  )
  const behavior = connection.behavior ?? 'auto'
  requireValue(['auto', 'generic', 'anthropic'].includes(behavior), 'invalid_connection')
  requireValue(behavior !== 'anthropic' || protocol === 'anthropic-messages', 'invalid_connection')
  return {
    ...connection,
    baseUrl,
    protocol,
    behavior,
    attachmentTransport: connection.attachmentTransport ?? 'auto'
  }
}

/** 判断是否启用 Claude 官方行为；自定义代理需显式选择，不能靠模型名或 signature 猜测。
 * @param {object} connection 模型连接。
 * @returns {boolean} 是否采用官方规则。
 */
export function usesAnthropicRules(connection) {
  const value = normalizeConnection(connection)
  return (
    value.protocol === 'anthropic-messages' &&
    (value.behavior === 'anthropic' ||
      (value.behavior === 'auto' && new URL(value.baseUrl).hostname === 'api.anthropic.com'))
  )
}

/** 仅对明确声明或官方已知模型启用前缀绑定，不扩散到所有兼容服务。
 * @param {object} connection 模型连接。
 * @returns {boolean} 历史前缀变化是否使推理块失效。
 */
export function requiresStableThinkingPrefix(connection) {
  if (!usesAnthropicRules(connection)) return false
  const declared = connection.capabilities?.requiresStablePrefixForThinking
  if (typeof declared === 'boolean') return declared
  return /^claude-(?:fable-5[.-]1|(?:opus|sonnet|haiku)-5[.-]5)(?:-|$)/.test(connection.modelId)
}
