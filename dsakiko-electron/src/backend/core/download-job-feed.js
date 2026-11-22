import { isDeepStrictEqual } from 'node:util'
import { isPendingDownload } from '../../shared/contracts/download-jobs.js'
import { copy, SnapshotBus } from './support.js'

/** @typedef {import('../../shared/contracts/download-jobs.js').DownloadJob} DownloadJob */
/** @typedef {import('../../shared/contracts/download-jobs.js').DownloadSummary} DownloadSummary */
/** @typedef {import('../../shared/contracts/download-jobs.js').DownloadUpdate} DownloadUpdate */

const progressIntervalMs = 1000

/** 下载中心使用的内存快照，从任务恢复与执行流程更新。 */
export class DownloadJobFeed {
  /** @type {Map<string, DownloadJob>} */
  jobs = new Map()
  /** @type {Map<string, () => Partial<DownloadJob>>} */
  progressReaders = new Map()
  bus = new SnapshotBus()
  pending = 0
  timer = null

  /**
   * @param {'summary'|'details'} scope 首页角标或面板详情。
   * @param {(event: DownloadSummary|DownloadUpdate) => void} listener 当前快照与后续变更。
   * @returns {() => void} 解除观察。
   */
  observe(scope, listener) {
    if (scope === 'summary') return this.bus.subscribe(scope, listener, { count: this.pending })
    // 新订阅直接读到最新进度，即使已有订阅者的下一批进度还在等待发送。
    const items = [...this.jobs.values()]
      .reverse()
      .map((job) => ({ ...job, ...this.progressReaders.get(job.id)?.() }))
    return this.bus.subscribe(scope, listener, { kind: 'snapshot', items })
  }

  /** @param {DownloadJob} job 任务的持久状态，阶段和完成态立即发送。 */
  publish(job) {
    this.progressReaders.delete(job.id)
    if (['planned', 'cancelled'].includes(job.status)) {
      this.remove(job.id)
      return
    }
    const previous = this.jobs.get(job.id)
    if (isDeepStrictEqual(previous, job)) return
    this.jobs.set(job.id, copy(job))
    this.updateCount(previous, job)
    this.bus.emit('details', { kind: 'upsert', items: [job] })
  }

  /** @param {string} id 退出下载中心的任务。 */
  remove(id) {
    const previous = this.jobs.get(id)
    if (!this.jobs.delete(id)) return
    this.progressReaders.delete(id)
    this.updateCount(previous)
    this.bus.emit('details', { kind: 'remove', id })
  }

  updateCount(previous, current) {
    const wasPending = Boolean(previous && isPendingDownload(previous))
    const isPending = Boolean(current && isPendingDownload(current))
    if (wasPending === isPending) return
    this.pending += isPending ? 1 : -1
    this.bus.emit('summary', { count: this.pending })
  }

  /** @param {string} id 执行中的任务。 @param {() => Partial<DownloadJob>} read 发送时读取进度字段，阶段与状态以已持久化的快照为准。 */
  progress(id, read) {
    if (!this.jobs.has(id)) return
    this.progressReaders.set(id, read)
    this.timer ??= setTimeout(() => this.flushProgress(), progressIntervalMs).unref()
  }

  flushProgress() {
    this.timer = null
    const items = []
    for (const [id, read] of this.progressReaders) {
      const previous = this.jobs.get(id)
      const job = { ...previous, ...read() }
      if (isDeepStrictEqual(previous, job)) continue
      this.jobs.set(id, copy(job))
      items.push(job)
    }
    this.progressReaders.clear()
    if (items.length) this.bus.emit('details', { kind: 'upsert', items })
  }

  close() {
    clearTimeout(this.timer)
    this.timer = null
    this.progressReaders.clear()
    this.bus.clear()
  }
}
