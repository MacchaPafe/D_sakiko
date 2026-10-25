import { createOpenAICompatible } from '@ai-sdk/openai-compatible'
import { createDeepSeek } from '@ai-sdk/deepseek'
import { createOpenAI } from '@ai-sdk/openai'
import { createAnthropic } from '@ai-sdk/anthropic'
import { createGoogleGenerativeAI } from '@ai-sdk/google'
import { requireValue } from '../core/support.js'
import { normalizeConnection } from './connections.js'

/** 将少量厂商扩展添加到共享 Completions 请求，不另造一套消息协议。 */
function completionFetch(connection, reasoning, transport) {
  return async (url, init) => {
    const body = JSON.parse(init.body)
    if (connection.provider === 'moonshot' || connection.provider === 'volcengine')
      body.thinking = { type: reasoning.enabled ? 'enabled' : 'disabled' }
    return transport(url, { ...init, body: JSON.stringify(body) })
  }
}

/** 创建选定协议的 SDK 模型；fetch 可替换为本地模拟服务，不接触业务对话。
 * @param {object} connection 固定模型连接。
 * @param {string} apiKey 本次请求凭据。
 * @param {object} [reasoning] 本次思考选项。
 * @param {typeof fetch} [transport] 请求传输。
 * @returns {import('ai').LanguageModel} SDK 模型。
 */
export function createModel(connection, apiKey, reasoning = { enabled: false }, transport = fetch) {
  const value = normalizeConnection(connection)
  const options = { apiKey, baseURL: value.baseUrl, fetch: transport }
  if (value.protocol === 'responses') return createOpenAI(options).responses(value.modelId)
  if (value.protocol === 'anthropic-messages') return createAnthropic(options)(value.modelId)
  if (value.protocol === 'google-generative-ai')
    return createGoogleGenerativeAI(options)(value.modelId)
  if (value.provider === 'deepseek') return createDeepSeek(options)(value.modelId)
  // 复用已为 Responses 安装的包，处理 OpenAI 的 max_completion_tokens 等 Chat 差异。
  if (value.provider === 'openai') return createOpenAI(options).chat(value.modelId)
  return createOpenAICompatible({
    ...options,
    name: 'openaiCompatible',
    fetch: completionFetch(value, reasoning, transport),
    metadataExtractor: {
      // OpenRouter 等服务通过消息级 reasoning_details 回传签名；SDK 的 reasoningText 不包含它。
      extractMetadata: ({ parsedBody }) => {
        const details = parsedBody.choices?.[0]?.message?.reasoning_details
        if (!Array.isArray(details)) return {}
        return {
          openaiCompatible: {
            reasoningDetails: details.map((part) =>
              Object.fromEntries(
                Object.entries(part).filter(
                  ([key, value]) =>
                    [
                      'type',
                      'format',
                      'index',
                      'id',
                      'text',
                      'data',
                      'signature',
                      'summary'
                    ].includes(key) &&
                    (typeof value === 'string' || (key === 'index' && Number.isInteger(value)))
                )
              )
            )
          }
        }
      }
    }
  }).chatModel(value.modelId)
}

/** 校验通用思考配置并转换为各协议选项；Responses 始终无服务端会话。
 * @param {object} connection 固定模型连接。
 * @param {object} reasoning 思考开关、强度与可选预算。
 * @returns {object} SDK providerOptions。
 */
export function reasoningOptions(connection, reasoning) {
  const value = normalizeConnection(connection)
  const { enabled, effort, budgetTokens } = reasoning
  if (enabled) {
    requireValue(value.capabilities?.reasoning?.status === 'supported', 'unsupported_reasoning')
    if (effort)
      requireValue(value.capabilities.reasoningEfforts?.includes(effort), 'unsupported_reasoning')
  }
  requireValue(
    !budgetTokens || (enabled && !effort && Number.isInteger(budgetTokens) && budgetTokens >= 1024),
    'unsupported_reasoning',
    '思考预算与强度不能同时设置，且预算仅用于开启思考。'
  )
  if (value.protocol === 'responses') {
    requireValue(!budgetTokens, 'unsupported_reasoning', 'Responses 不支持手动思考 Token 预算。')
    const options = { store: false, include: ['reasoning.encrypted_content'] }
    if (!enabled) options.reasoningEffort = 'none'
    else if (effort) options.reasoningEffort = effort
    return { openai: options }
  }
  if (value.protocol === 'anthropic-messages') {
    let thinking = { type: 'disabled' }
    if (enabled) thinking = budgetTokens ? { type: 'enabled', budgetTokens } : { type: 'adaptive' }
    return { anthropic: { thinking, ...(enabled && effort ? { effort } : {}) } }
  }
  if (value.protocol === 'google-generative-ai') {
    const thinkingConfig = { includeThoughts: true }
    if (!enabled) thinkingConfig.thinkingBudget = 0
    else if (budgetTokens) thinkingConfig.thinkingBudget = budgetTokens
    else if (effort) thinkingConfig.thinkingLevel = effort
    return { google: { thinkingConfig } }
  }
  requireValue(!budgetTokens, 'unsupported_reasoning', 'Completions 不支持统一的思考 Token 预算。')
  if (value.provider === 'deepseek')
    return {
      deepseek: {
        thinking: { type: enabled ? 'enabled' : 'disabled' },
        ...(enabled && effort ? { reasoningEffort: effort } : {})
      }
    }
  const options = {}
  if (enabled && effort) options.reasoningEffort = effort
  if (value.provider === 'openai') {
    if (!enabled && value.capabilities?.reasoning?.status === 'supported')
      options.reasoningEffort = 'none'
    return { openai: options }
  }
  return { openaiCompatible: options }
}
