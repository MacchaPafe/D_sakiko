import { parameters, syncTool, textParameter } from './definition.js'
import { fetchJson } from './network.js'
import { requireValue } from '../core/support.js'

// 迁移自 Python 端内置默认密钥，开箱即用；仍优先采用私有凭据或环境变量覆盖。
const DEFAULT_BOCHA_API_KEY = 'sk-28078debe6d74828a57e63164fe0f23e'

/**
 * 搜索厂商由后端配置选择，模型不接触凭据或任意接口地址。
 * @param {import('../settings/interface.js').Settings} settings 全局配置与私有凭据。
 * @param {typeof fetch} transport 网络传输。
 * @returns {object} 搜索工具条目。
 */
export function webSearchTool(settings, transport) {
  return syncTool(
    {
      name: 'web_search',
      description: '通过搜索引擎查询互联网公开信息，返回相关网页的标题、URL、摘要与发布时间。',
      requiredCapabilities: ['network'],
      parametersSchema: parameters(
        {
          query: {
            ...textParameter,
            description: '搜索关键词或自然语言查询词。'
          },
          top_k: {
            type: 'integer',
            minimum: 1,
            maximum: 20,
            description: '期望返回的最大结果条数（受后端配置的上限约束）。'
          }
        },
        ['query']
      )
    },
    async (_, input, source, signal) => {
      const options = (await settings.get()).tools.webSearch
      requireValue(
        options.provider === 'bocha',
        'search_provider_unavailable',
        '尚未实现该搜索厂商。'
      )
      const secret =
        (await settings.toolCredential('bocha')).trim() ||
        process.env.BOCHA_API_KEY?.trim() ||
        DEFAULT_BOCHA_API_KEY
      const count = Math.min(input.top_k ?? options.maxResults, options.maxResults)
      const data = await fetchJson(
        transport,
        new URL('/v1/web-search', options.baseUrl),
        {
          method: 'POST',
          headers: { Authorization: `Bearer ${secret}`, 'Content-Type': 'application/json' },
          body: JSON.stringify({ query: input.query, count, summary: true })
        },
        signal,
        options.timeoutMs,
        {
          403: (body) => {
            if (body?.message === 'You do not have enough money') return '博查账户余额不足'
          }
        }
      )
      requireValue(
        data.code === undefined || data.code === 200,
        'search_failed',
        '搜索服务未成功返回结果。'
      )
      const items = (data.data ?? data).webPages?.value
      requireValue(Array.isArray(items), 'invalid_search_response')
      return {
        provider: 'bocha',
        query: input.query,
        results: items.slice(0, count).map((item) => ({
          title: String(item.name ?? '').slice(0, 500),
          url: String(item.url ?? '').slice(0, 2000),
          summary: String(item.summary ?? item.snippet ?? '').slice(0, 4000),
          publishedAt: item.datePublished ?? null
        }))
      }
    }
  )
}
