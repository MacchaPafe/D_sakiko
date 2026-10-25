import path from 'node:path'
import { mkdir, writeFile } from 'node:fs/promises'
import {
  SerialQueue,
  readJson,
  writeJson,
  uuid,
  copy,
  requireValue,
  abortable,
  problem
} from '../core/support.js'
import { defaultAttachmentRules, readAttachmentRules } from './policy.js'

/** @typedef {import('../../shared/contracts/common.js').AssetRef} AssetRef */
/**
 * 后端私有远端文件记录；不进入 Chat、导出包或前端快照。
 * @typedef {object} RemoteFileRef
 * @property {string} id 本地记录身份，用于精确失效旧副本。
 * @property {string} scopeId 服务地址、协议、用途与凭据的摘要。
 * @property {string} assetId 本地资源身份。
 * @property {Record<string, string> | null} reference SDK 消息引用。
 * @property {string | null} deleteId 厂商删除标识，与消息 URI 分开。
 * @property {number | null} expiresAt 已知的厂商过期时间。
 * @property {string} status 上传、有效、失效及删除状态。
 * @property {boolean} [reused] 本次调用是否直接复用，运行信息不写入索引。
 */

/** 管理持久记录和并发任务；厂商协议由绑定适配器承担。 */
export class RemoteFiles {
  /**
   * @param {object} options 装配能力。
   * @param {string} options.directory 应用数据下的独立目录。
   * @param {import('../resources/interface.js').MediaAssets} options.assets 本地资源管理。
   * @param {() => Promise<object[]>} [options.adapters] 获取当前仍配置的服务适配器，不保存旧密钥。
   * @param {number} [options.uploadTimeoutMs] 独立上传任务的时间上限。
   */
  constructor({ directory, assets, adapters = async () => [], uploadTimeoutMs = 300000 }) {
    this.file = path.join(directory, 'index.json')
    this.rulesFile = path.join(directory, 'rules.json')
    this.assets = assets
    this.adapters = adapters
    this.uploadTimeoutMs = uploadTimeoutMs
    this.queue = new SerialQueue()
    this.keys = new SerialQueue()
    this.flights = new Map()
    this.closed = false
  }

  /** 首次写入默认规则；已有规则始终保留，损坏时明确拒绝。
   * @returns {Promise<void>}
   */
  async initialize() {
    await mkdir(path.dirname(this.file), { recursive: true })
    await writeFile(this.rulesFile, JSON.stringify(defaultAttachmentRules(), null, 2), {
      flag: 'wx',
      mode: 0o600
    }).catch((error) => {
      if (error.code !== 'EEXIST') throw error
    })
    await readAttachmentRules(this.rulesFile)
    await this.state()
  }

  async state() {
    const state = await readJson(this.file, { version: 1, records: [] })
    requireValue(state?.version === 1 && Array.isArray(state.records), 'invalid_remote_index')
    const ids = new Set()
    for (const item of state.records) {
      requireValue(
        item &&
          typeof item.id === 'string' &&
          !ids.has(item.id) &&
          /^[a-f0-9]{64}$/.test(item.scopeId) &&
          typeof item.assetId === 'string' &&
          [
            'uploading',
            'failed',
            'ready',
            'invalid',
            'pending-delete',
            'unsupported',
            'deleted'
          ].includes(item.status) &&
          (item.expiresAt === null || Number.isFinite(item.expiresAt)) &&
          (item.reference === null ||
            (typeof item.reference === 'object' &&
              !Array.isArray(item.reference) &&
              Object.keys(item.reference).length > 0 &&
              Object.values(item.reference).every(
                (value) => typeof value === 'string' && value
              ))) &&
          (!['ready', 'invalid', 'pending-delete', 'unsupported', 'deleted'].includes(
            item.status
          ) ||
            item.reference !== null) &&
          (item.deleteId === null || typeof item.deleteId === 'string') &&
          Number.isFinite(item.createdAt),
        'invalid_remote_index'
      )
      ids.add(item.id)
    }
    return state
  }

  async change(operation) {
    return this.queue.run('index', async () => {
      const state = await this.state()
      const result = await operation(state)
      await writeJson(this.file, state)
      return copy(result)
    })
  }

  /** 共享任务不使用调用方 signal；取消只结束该调用方等待，迟到结果仍落盘。
   * @param {object} adapter 与生成绑定同一连接的厂商适配器。
   * @param {AssetRef} asset 本地资源。
   * @param {AbortSignal} [signal] 本次等待的取消信号。
   * @returns {Promise<RemoteFileRef>} 有效记录。
   */
  async ensureUploaded(adapter, asset, signal) {
    signal?.throwIfAborted()
    requireValue(!this.closed, 'backend_closed')
    const key = `${adapter.scopeId}:${asset.id}`
    let pending = this.flights.get(key)
    if (!pending) {
      pending = this.keys.run(key, () => this.prepare(adapter, asset))
      this.flights.set(key, pending)
      const clear = () => {
        if (this.flights.get(key) === pending) this.flights.delete(key)
      }
      void pending.then(clear, clear)
    }
    return copy(await abortable(pending, signal))
  }

  async prepare(adapter, asset) {
    const state = await this.state()
    const current = state.records.find(
      (item) =>
        item.scopeId === adapter.scopeId &&
        item.assetId === asset.id &&
        item.status === 'ready' &&
        (item.expiresAt === null || item.expiresAt > Date.now() + 30000)
    )
    if (current) return { ...current, reused: true }
    return this.assets.using([asset], async () => {
      const record = {
        id: uuid(),
        scopeId: adapter.scopeId,
        assetId: asset.id,
        provider: adapter.provider,
        protocol: adapter.protocol,
        reference: null,
        deleteId: null,
        expiresAt: null,
        createdAt: Date.now(),
        status: 'uploading',
        cleanup: null
      }
      // 上传前持久登记意图；中途退出的未知结果不能伪装为未发生上传。
      await this.change((index) => {
        index.records.push(record)
      })
      try {
        const info = await this.assets.inspect(asset)
        const data = await this.assets.read(asset)
        const result = await adapter.upload(info, data, AbortSignal.timeout(this.uploadTimeoutMs))
        Object.assign(record, result, { status: 'ready' })
        await this.change((index) => {
          Object.assign(
            index.records.find((item) => item.id === record.id),
            record
          )
        })
        return { ...record, reused: false }
      } catch (error) {
        // 成功响应后的落盘失败不可覆盖已取得的删除标识；再次保存仍失败时向调用方报错。
        if (!record.reference) record.status = 'failed'
        await this.change((index) => {
          Object.assign(
            index.records.find((item) => item.id === record.id),
            record
          )
        }).catch((failure) => this.assets.reportMaintenance(failure))
        throw error
      }
    })
  }

  /** 只使指定旧副本失效，不影响同资源的新上传结果。
   * @param {RemoteFileRef} reference 本模块发出的记录。
   * @returns {Promise<void>}
   */
  async invalidate(reference) {
    await this.change((state) => {
      const record = state.records.find(
        (item) => item.id === reference.id && item.scopeId === reference.scopeId
      )
      if (record?.status === 'ready') record.status = 'invalid'
    })
  }

  /** 显式删除即停止复用；未实现返回 unsupported，网络失败保留删除意图并拒绝。
   * @param {object} adapter 当前作用域适配器。
   * @param {RemoteFileRef} reference 已登记记录。
   * @returns {Promise<{status: string}>} 删除结果。
   */
  async delete(adapter, reference) {
    requireValue(adapter.scopeId === reference.scopeId, 'remote_scope_mismatch')
    return this.keys.run(`${reference.scopeId}:${reference.assetId}`, () =>
      this.remove(adapter, reference.id)
    )
  }

  async remove(adapter, id) {
    const record = await this.change((state) => {
      const item = state.records.find(
        (entry) => entry.id === id && entry.scopeId === adapter.scopeId
      )
      requireValue(item, 'remote_file_unknown')
      if (item.status !== 'deleted') item.status = adapter.delete ? 'pending-delete' : 'unsupported'
      return item
    })
    if (record.status === 'deleted' || record.status === 'unsupported')
      return { status: record.status }
    try {
      await adapter.delete(record)
      await this.change((state) => {
        Object.assign(
          state.records.find((item) => item.id === id),
          { status: 'deleted', cleanup: null }
        )
      })
      return { status: 'deleted' }
    } catch (error) {
      await this.change((state) => {
        state.records.find((item) => item.id === id).cleanup = problem(error)
      })
      throw error
    }
  }

  /** 清理登记且无人使用的副本；不扫描厂商账号，缺失凭据及失败均保留记录。
   * @returns {Promise<object[]>} 本轮逐项结果。
   */
  async collectUnused() {
    if (this.closed) return []
    return this.queue.run('collection', async () => {
      const adapters = new Map((await this.adapters()).map((adapter) => [adapter.scopeId, adapter]))
      const result = []
      for (const record of (await this.state()).records) {
        if (this.closed) break
        if (!record.reference || record.status === 'deleted') continue
        const key = `${record.scopeId}:${record.assetId}`
        if (this.flights.has(key)) continue
        try {
          const outcome = await this.keys.run(key, async () => {
            const adapter = adapters.get(record.scopeId)
            if (record.status === 'unsupported' && adapter && !adapter.delete)
              return { status: 'unsupported' }
            const cleanup = async () => {
              if (!adapter) {
                await this.change((state) => {
                  state.records.find((item) => item.id === record.id).cleanup = {
                    code: 'credential_unavailable',
                    message: '原文件作用域的凭据当前不可用。',
                    retryable: true
                  }
                })
                return { status: 'credential_unavailable' }
              }
              return this.remove(adapter, record.id)
            }
            // 显式删除建立的意图不因本地仍有引用而撤销。
            const latest = (await this.state()).records.find((item) => item.id === record.id)
            if (latest.status === 'pending-delete') return cleanup()
            return this.assets.withUnusedAsset({ id: record.assetId }, cleanup)
          })
          result.push({ id: record.id, ...outcome })
        } catch (error) {
          result.push({ id: record.id, status: 'failed', problem: problem(error) })
        }
      }
      return result
    })
  }

  /** 关闭时等待共享上传落盘，避免调用方取消后丢弃迟到结果。
   * @returns {Promise<void>}
   */
  async close() {
    this.closed = true
    await Promise.allSettled([...this.flights.values()])
    await Promise.allSettled([...this.queue.tails.values(), ...this.keys.tails.values()])
  }
}
