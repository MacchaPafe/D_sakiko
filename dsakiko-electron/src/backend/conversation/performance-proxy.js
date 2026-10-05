import { randomUUID } from 'node:crypto'
import { PERFORMANCE_METHODS, problemError } from '../../shared/contracts/desktop.js'

/** 演出远程代理只处理命令和回执，不在 Node 重新计算进度。 */
export class PerformanceProxy {
  pending = new Map()
  snapshots = new Map()
  ready = false
  constructor() {
    for (const method of PERFORMANCE_METHODS) this[method] = (...args) => this.call(method, args)
  }
  attach(send) {
    this.send = send
    this.ready = true
  }
  call(method, args) {
    if (!this.ready)
      return Promise.reject(problemError('presentation_unavailable', '演出页面尚未就绪。', true))
    const id = randomUUID()
    return new Promise((resolve, reject) => {
      const timeout = setTimeout(() => {
        this.pending.delete(id)
        reject(problemError('presentation_timeout', '演出端未响应，请重新打开程序。', true))
      }, 15000)
      this.pending.set(id, { resolve, reject, timeout })
      this.send({ id, method, args })
    })
  }
  reply(reply) {
    const pending = this.pending.get(reply?.id)
    if (!pending) return
    clearTimeout(pending.timeout)
    this.pending.delete(reply.id)
    if (reply.problem)
      pending.reject(Object.assign(new Error(reply.problem.message), { problem: reply.problem }))
    else pending.resolve(reply.value)
  }
  update(update) {
    const snapshot = update?.snapshot
    if (!snapshot || !Array.isArray(update.finished) || !Number.isInteger(snapshot.revision)) return
    if ((this.snapshots.get(snapshot.sequenceId) || -1) >= snapshot.revision) return
    this.snapshots.set(snapshot.sequenceId, snapshot.revision)
    this.onUpdate?.(update)
  }
  disconnect() {
    this.ready = false
    this.snapshots.clear()
    for (const pending of this.pending.values()) {
      clearTimeout(pending.timeout)
      pending.reject(problemError('presentation_disconnected', '演出端已关闭。'))
    }
    this.pending.clear()
    this.onDisconnect?.()
  }
}
