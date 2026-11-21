import path from 'node:path'
import { readJson, writeJson, SerialQueue, merge } from '../core/support.js'
import { officialModelCapabilities } from '../llm/connections.js'
import { fetchJson } from '../tools/network.js'

/** 保存官方能力目录的最近一次成功结果；离线打开控件时仍可立即使用。 */
export class ReasoningCatalog {
  constructor(directory, transport) {
    this.filename = path.join(directory, 'reasoning-models.json')
    this.transport = transport
    this.queue = new SerialQueue()
    this.pending = new Map()
  }
  async records() {
    this.cache ??= readJson(this.filename, {}).catch(() => ({}))
    return this.cache
  }
  key(connection, modelId) {
    return JSON.stringify([connection.id, connection.provider, connection.baseUrl, modelId])
  }
  async resolve(connection, model) {
    const records = await this.records()
    const saved = records[this.key(connection, model.id)]?.value
    return merge(
      officialModelCapabilities(connection, model.id),
      model.declared,
      saved,
      model.overrides
    )
  }
  async refresh(connection, key) {
    if (!key) return
    const ds =
      connection.provider === 'deepseek' &&
      ['https://api.deepseek.com', 'https://api.deepseek.com/v1'].includes(connection.baseUrl)
    const claude =
      connection.provider === 'anthropic' &&
      ['https://api.anthropic.com', 'https://api.anthropic.com/v1'].includes(connection.baseUrl)
    if (!ds && !claude) return
    const records = await this.records()
    if (
      connection.models.every(
        (m) => Date.now() - (records[this.key(connection, m.id)]?.at ?? 0) < 86400000
      )
    )
      return
    const scope = this.key(connection, connection.revision)
    if (this.pending.has(scope)) return this.pending.get(scope)
    const load = async () => {
      let updates = []
      if (ds) {
        const body = await fetchJson(
          this.transport,
          'https://api.deepseek.com/models',
          { headers: { Authorization: 'Bearer ' + key } },
          undefined,
          5000
        )
        for (const m of body.data ?? [])
          if (Array.isArray(m.effort?.supported_levels))
            updates.push([
              m.id,
              {
                reasoning: { status: 'supported', source: 'deepseek-api' },
                reasoningEfforts: m.effort.supported_levels,
                reasoningDefault: m.effort.default_level,
                reasoningToggle: true
              }
            ])
      } else {
        updates = await Promise.all(
          connection.models.map(async (m) => {
            const data = await fetchJson(
              this.transport,
              'https://api.anthropic.com/v1/models/' + encodeURIComponent(m.id),
              { headers: { 'x-api-key': key, 'anthropic-version': '2023-06-01' } },
              undefined,
              5000
            )
            const caps = data.capabilities
            if (typeof caps?.thinking?.supported !== 'boolean') return null
            const efforts = ['low', 'medium', 'high', 'xhigh', 'max'].filter(
              (level) => caps.effort?.[level]?.supported
            )
            return [
              m.id,
              {
                reasoning: {
                  status: caps.thinking.supported ? 'supported' : 'unsupported',
                  source: 'anthropic-api'
                },
                reasoningEfforts: efforts,
                reasoningToggle: Boolean(caps.thinking.types?.disabled?.supported),
                reasoningAdaptive: Boolean(caps.thinking.types?.adaptive?.supported)
              }
            ]
          })
        )
      }
      await this.queue.run('cache', async () => {
        for (const entry of updates.filter(Boolean))
          records[this.key(connection, entry[0])] = { at: Date.now(), value: entry[1] }
        await writeJson(this.filename, records)
      })
    }
    const task = load()
      .catch(() => {})
      .finally(() => this.pending.delete(scope))
    this.pending.set(scope, task)
    return task
  }
}
