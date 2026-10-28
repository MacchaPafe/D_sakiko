import path from 'node:path'
import { readJson, writeJson, requireValue, copy } from '../core/support.js'
import { providerPresets } from '../../shared/contracts/llm-providers.js'

const DAY = 24 * 60 * 60 * 1000
export const MODEL_CATALOG_URL = 'https://models.dev/api.json'

/** 仅接受可用于预算的正整数，缺失值不表示零或无限容量。 */
function limit(value) {
  return Number.isSafeInteger(value) && value > 0 ? value : null
}

/** 校验目录并只保存预算所需字段，避免在内存和磁盘重复保存价格等无关信息。 */
function normalize(data) {
  requireValue(data && typeof data === 'object' && !Array.isArray(data), 'invalid_model_catalog')
  const providers = []
  for (const [provider, entry] of Object.entries(data)) {
    if (!entry?.models || typeof entry.models !== 'object' || Array.isArray(entry.models)) continue
    const models = []
    for (const [id, model] of Object.entries(entry.models)) {
      const context = limit(model?.limit?.context)
      if (!context) continue
      models.push([
        id,
        { limit: { context, input: limit(model.limit.input), output: limit(model.limit.output) } }
      ])
    }
    if (models.length) providers.push([provider, { models: Object.fromEntries(models) }])
  }
  requireValue(providers.length, 'invalid_model_catalog')
  return Object.fromEntries(providers)
}

/**
 * Models.dev 本地目录：过期只触发刷新，旧值仍有效；网络故障不进入聊天关键路径。
 * @typedef {{version: number, fetchedAt: number, data: object}} CatalogCache
 */
export class ModelCatalog {
  /** @param {{directory: string, fetch?: typeof fetch, now?: () => number}} options 缓存位置及可替换的网络与时钟。 */
  constructor({ directory, fetch: transport = fetch, now = Date.now }) {
    this.file = path.join(directory, 'models-dev.json')
    this.transport = transport
    this.now = now
    this.cache = null
    this.pending = null
    this.retryAt = 0
    this.failures = 0
    this.controller = new AbortController()
  }

  /** 初始化仅读取本地缓存，损坏的缓存不阻止聊天或后续刷新。 */
  async initialize() {
    this.loading ??= (async () => {
      try {
        const saved = await readJson(this.file)
        if (saved?.version === 1 && Number.isFinite(saved.fetchedAt) && saved.fetchedAt > 0)
          this.cache = { ...saved, data: normalize(saved.data) }
      } catch {
        /* 目录不是用户存档，损坏时允许重新获取。 */
      }
    })()
    await this.loading
  }

  /** 合并刷新请求并退避；失败时保留上次已校验的目录。 */
  refresh() {
    if (this.pending) return this.pending
    if (this.controller.signal.aborted || this.now() < this.retryAt) return Promise.resolve()
    this.pending = (async () => {
      try {
        const response = await this.transport(MODEL_CATALOG_URL, {
          signal: AbortSignal.any([this.controller.signal, AbortSignal.timeout(15000)])
        })
        requireValue(response.ok, 'model_catalog_unavailable')
        // 对流逐段限额，避免异常响应无界占用内存。
        const chunks = []
        let bytes = 0
        for await (const chunk of response.body) {
          bytes += chunk.length
          requireValue(bytes <= 16 * 1024 * 1024, 'invalid_model_catalog')
          chunks.push(chunk)
        }
        const data = normalize(JSON.parse(Buffer.concat(chunks).toString('utf8')))
        const next = { version: 1, fetchedAt: this.now(), data }
        this.controller.signal.throwIfAborted()
        await writeJson(this.file, next)
        this.cache = next
        this.failures = 0
        this.retryAt = 0
      } catch {
        this.failures += 1
        this.retryAt = this.now() + Math.min(DAY, 60000 * 2 ** Math.min(this.failures - 1, 10))
      } finally {
        this.pending = null
      }
    })()
    return this.pending
  }

  /**
   * 查询明确映射；只有已识别的官方端点允许按精确模型 ID 自动映射。
   * @param {object} connection 连接定义。
   * @param {object} model 模型声明与可选 catalogMapping。
   * @returns {Promise<object>} 可合并到模型能力的上限及来源。
   */
  async lookup(connection, model) {
    await this.initialize()
    let mapping = model.catalogMapping
    if (!mapping) {
      const preset = providerPresets.find((item) => item.id === connection.provider)
      const ids = { gemini: 'google', moonshot: 'moonshotai' }
      if (
        !preset?.baseUrl ||
        preset.baseUrl !== connection.baseUrl ||
        ['vllm', 'ollama'].includes(connection.provider)
      )
        return {}
      mapping = { provider: ids[connection.provider] ?? connection.provider, model: model.id }
    }
    if (!this.cache || this.now() - this.cache.fetchedAt >= 3 * DAY) void this.refresh()
    const provider = Object.hasOwn(this.cache?.data ?? {}, mapping.provider)
      ? this.cache.data[mapping.provider]
      : null
    const found = Object.hasOwn(provider?.models ?? {}, mapping.model)
      ? provider.models[mapping.model]
      : null
    if (!found) return {}
    return copy({
      contextTokens: found.limit.context,
      inputTokens: found.limit.input,
      outputTokens: found.limit.output,
      contextSource: `models.dev:${mapping.provider}/${mapping.model}`,
      catalogFetchedAt: this.cache.fetchedAt
    })
  }

  /** 停止后台刷新，关闭时不留下网络任务。 */
  async close() {
    this.controller.abort()
    await this.pending
  }
}
