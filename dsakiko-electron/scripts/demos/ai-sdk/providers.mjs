import { createAnthropic } from '@ai-sdk/anthropic'
import { createDeepSeek } from '@ai-sdk/deepseek'
import { createGoogleGenerativeAI } from '@ai-sdk/google'
import { createOpenAICompatible } from '@ai-sdk/openai-compatible'

/**
 * 一次运行固定的连接快照；凭据和 SDK 模型均留在 Node 内。
 * @typedef {object} DemoConnection
 * @property {'deepseek'|'anthropic'|'google'|'openai-compatible'} provider 厂商适配器。
 * @property {string} model 厂商原生模型名。
 * @property {string} apiKey 当前连接凭据。
 * @property {string} [baseURL] 可覆盖的服务地址。
 * @property {object} [providerOptions] 本轮固定的厂商专属选项。
 */

/**
 * 选择真实厂商适配器，调用方仍然统一使用 generateText。
 * @param {DemoConnection} connection 连接快照。
 * @param {typeof fetch} [fetcher] 测试可替换 HTTP 传输，SDK 本身不替换。
 * @returns {import('ai').LanguageModel} 当前连接绑定的模型。
 */
export function createModel(connection, fetcher) {
  if (!connection.model || !connection.apiKey) throw new Error('需要模型名称和 API Key。')
  const options = { apiKey: connection.apiKey, fetch: fetcher }
  if (connection.baseURL) options.baseURL = connection.baseURL.replace(/\/$/, '')
  switch (connection.provider) {
    case 'deepseek':
      return createDeepSeek(options)(connection.model)
    case 'anthropic':
      return createAnthropic(options)(connection.model)
    case 'google':
      return createGoogleGenerativeAI(options)(connection.model)
    case 'openai-compatible':
      if (!options.baseURL) throw new Error('自定义兼容服务需要 baseURL。')
      return createOpenAICompatible({ ...options, name: 'demo-compatible' })(connection.model)
    default:
      throw new Error('不支持的 demo 供应商。')
  }
}
