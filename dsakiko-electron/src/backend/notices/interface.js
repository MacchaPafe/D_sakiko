import path from 'node:path'
import {
  copy,
  fingerprint,
  readJson,
  requireValue,
  SerialQueue,
  SnapshotBus,
  uuid,
  writeJson
} from '../core/support.js'

/**
 * @typedef {object} NoticeSource
 * @property {string} key 后端持久业务结果的唯一来源键。
 * @property {import('../../shared/contracts/notices.js').Notice['kind']} kind 受支持的通知类型。
 * @property {number} createdAt 来源发生时间。
 * @property {import('../../shared/contracts/notices.js').Notice['data']} data 已脱敏的业务载荷。
 */
/** @typedef {import('../../shared/contracts/notices.js').Notice & {key: string, active: boolean, dismissed: boolean}} NoticeRecord 后端私有记录，关闭后仍保留去重证据。 */
/** @typedef {{version: 1, revision: number, nextOrder: number, records: NoticeRecord[]}} NoticeState 持久通知状态。 */

/** 统一持久通知与确认；业务来源负责事实，本模块负责顺序、去重和订阅。 */
export class Notices {
  /**
   * 注入完整来源查询，便于在无窗口和重启时补齐业务提交后的通知。
   * @param {{appDataRoot: string, readSources: () => Promise<NoticeSource[]>, write?: typeof writeJson}} options 来源读取与原子持久化能力。
   */
  constructor({ appDataRoot, readSources, write = writeJson }) {
    this.file = path.join(appDataRoot, 'notices.json')
    this.readSources = readSources
    this.write = write
    this.runtimeId = uuid()
    this.queue = new SerialQueue()
    this.bus = new SnapshotBus()
    this.closed = false
  }

  /** @returns {Promise<NoticeState>} 文件损坏时拒绝覆盖，不把已关闭记录当作丢失。 */
  async state() {
    const state = await readJson(this.file, { version: 1, revision: 0, nextOrder: 0, records: [] })
    requireValue(
      state.version === 1 &&
        Array.isArray(state.records) &&
        Number.isSafeInteger(state.revision) &&
        state.revision >= 0 &&
        Number.isSafeInteger(state.nextOrder) &&
        state.nextOrder >= 0,
      'notices_invalid'
    )
    return state
  }

  /** @param {NoticeState} state 持久状态。 @returns {import('../../shared/contracts/notices.js').NoticeSnapshot} 独立的公开快照。 */
  snapshot(state) {
    const notices = state.records
      .filter((item) => item.active && !item.dismissed)
      .sort((a, b) => a.order - b.order)
      .map(({ id, kind, createdAt, order, data }) => ({ id, kind, createdAt, order, data }))
    return copy({ runtimeId: this.runtimeId, revision: state.revision, notices })
  }

  /** @param {NoticeState} state 待提交状态。 @returns {Promise<void>} 写入成功后才发布，失败不产生虚假确认。 */
  async save(state) {
    state.revision += 1
    await this.write(this.file, state)
    this.bus.emit('notices', this.snapshot(state))
  }

  /** @returns {Promise<NoticeState>} 在通知队列内核对全部来源，查询失败时保留原记录。 */
  async reconcile() {
    requireValue(!this.closed, 'notices_closed')
    const sources = await this.readSources()
    requireValue(Array.isArray(sources), 'invalid_notice_sources')
    const keys = new Set()
    for (const source of sources) {
      requireValue(
        typeof source.key === 'string' &&
          source.key.length > 0 &&
          !keys.has(source.key) &&
          ['connection', 'voice-ready'].includes(source.kind) &&
          Number.isFinite(source.createdAt) &&
          source.createdAt >= 0,
        'invalid_notice_sources'
      )
      keys.add(source.key)
    }
    const state = await this.state()
    const previous = fingerprint(state.records)
    for (const record of state.records) record.active = false
    // 首次补录按来源时间稳定排列；已有顺序永不因来源更新或迟到事件重排。
    for (const source of sources.sort(
      (a, b) => a.createdAt - b.createdAt || a.key.localeCompare(b.key)
    )) {
      let record = state.records.find((item) => item.key === source.key)
      if (!record) {
        record = {
          id: fingerprint(source.key),
          key: source.key,
          kind: source.kind,
          createdAt: source.createdAt,
          order: state.nextOrder++,
          dismissed: false
        }
        state.records.push(record)
      }
      record.active = true
      record.data = copy(source.data)
    }
    if (previous !== fingerprint(state.records)) await this.save(state)
    return state
  }

  /** @returns {Promise<import('../../shared/contracts/notices.js').NoticeSnapshot>} 核对来源后返回未关闭列表；内部恢复入口同样使用此方法。 */
  async list() {
    return this.queue.run('state', async () => this.snapshot(await this.reconcile()))
  }

  /**
   * 关闭已登记通知，保留来源证据以抵御重启和重复任务结果。
   * @param {string} id 通知身份。
   * @returns {Promise<import('../../shared/contracts/notices.js').NoticeSnapshot>} 已持久确认后的列表。
   */
  async dismiss(id) {
    requireValue(typeof id === 'string' && /^[a-f0-9]{64}$/.test(id), 'invalid_notice_id')
    return this.queue.run('state', async () => {
      requireValue(!this.closed, 'notices_closed')
      // 关闭不依赖资源扫描成功；即使来源暂时不可读，也能确认已经看过的通知。
      const state = await this.state()
      const record = state.records.find((item) => item.id === id)
      requireValue(record, 'notice_not_found')
      if (!record.dismissed) {
        record.dismissed = true
        await this.save(state)
      }
      return this.snapshot(state)
    })
  }

  /** @param {(snapshot: import('../../shared/contracts/notices.js').NoticeSnapshot) => void} listener 观察者。 @returns {Promise<() => void>} 首次完整快照与取消订阅能力。 */
  async observe(listener) {
    return this.queue.run('state', async () => {
      const state = await this.reconcile()
      return this.bus.subscribe('notices', listener, this.snapshot(state))
    })
  }

  /** @returns {Promise<void>} 等待已开始的更新，拒绝新操作并释放观察者。 */
  async close() {
    await this.queue.run('state', () => {
      this.closed = true
      this.bus.clear()
    })
  }
}
