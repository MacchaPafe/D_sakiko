import { problemError } from '../../../shared/contracts/desktop.js'

/**
 * @typedef {object} SequenceRuntime
 * @property {string} id 演出身份。
 * @property {import('../../../shared/contracts/presentation.js').Scene} scene 场景。
 * @property {object[]} queue 尚未完成的 guides。
 * @property {object|null} current 当前计时记录。
 * @property {number} revision 快照版本。
 */

/** 所有 sequence 共用一个常驻调度器，前台才接触声音与模型。 */
export class Performance {
  sequences = new Map()
  guides = new Map()
  foreground = null
  listeners = new Set()
  playback = null
  serial = 0
  constructor({ surface, now = () => performance.now(), autoTick = true } = {}) {
    this.surface = surface
    this.now = now
    this.epoch = crypto.randomUUID()
    if (autoTick) this.timer = setInterval(() => this.tick(), 80)
  }
  require(id) {
    const sequence = this.sequences.get(id)
    if (!sequence) throw problemError('sequence_unavailable', '演出序列已经失效。')
    return sequence
  }
  id() {
    this.serial += 1
    return `${this.epoch}-${this.serial}`
  }
  async createSequence(scene) {
    if (
      !Array.isArray(scene?.slots) ||
      !scene.slots.length ||
      new Set(scene.slots.map((slot) => slot.id)).size !== scene.slots.length
    )
      throw problemError('invalid_scene', '场景演员位置无效。')
    const id = this.id()
    this.sequences.set(id, {
      id,
      scene: structuredClone(scene),
      queue: [],
      current: null,
      revision: 0,
      terminal: new Map(),
      observers: new Set(),
      closed: false
    })
    return id
  }
  async appendGuides(sequenceId, guides) {
    const sequence = this.require(sequenceId)
    if (
      !Array.isArray(guides) ||
      guides.some(
        (guide) =>
          !sequence.scene.slots.some((slot) => slot.id === guide.slotId) ||
          typeof guide.ready !== 'boolean'
      )
    )
      throw problemError('invalid_guide', '演出材料或演员位置无效。')
    const added = guides.map((guide) => ({
      ...structuredClone(guide),
      id: this.id(),
      sequenceId,
      waiters: new Set()
    }))
    for (const guide of added) {
      sequence.queue.push(guide)
      this.guides.set(guide.id, guide)
    }
    this.advance(sequence)
    this.publish(sequence)
    return added.map((guide) => guide.id)
  }
  async resolveGuide(id, { audio }) {
    const guide = this.guides.get(id)
    if (!guide) throw problemError('guide_unavailable', '演出条目不存在。')
    if (guide.ready || guide.finish) return false
    guide.audio = structuredClone(audio)
    guide.ready = true
    const sequence = this.require(guide.sequenceId)
    this.advance(sequence)
    this.publish(sequence)
    return true
  }
  estimate(guide) {
    if (guide.audio) return Math.max(500, guide.audio.durationMs)
    if (guide.text) return Math.max(1200, [...guide.text].length * 110)
    const sequence = this.require(guide.sequenceId)
    return sequence.scene.slots.find((slot) => slot.id === guide.slotId)?.presentation ? 2500 : 0
  }
  advance(sequence, start = this.now()) {
    if (sequence.current || !sequence.queue[0]?.ready) return
    const guide = sequence.queue[0]
    sequence.current = {
      guide,
      startedAt: start,
      duration: this.estimate(guide),
      finishedBySurface: false
    }
    if (sequence.id === this.foreground) this.play(sequence)
  }
  play(sequence) {
    this.stopSurface()
    const current = sequence.current
    if (!current || !this.surface) return
    const token = this.id()
    this.playback = { token, sequenceId: sequence.id }
    Promise.resolve(this.surface.play(current.guide, sequence.scene, current.duration))
      .then(() => {
        if (this.playback?.token !== token || sequence.current !== current) return
        current.finishedBySurface = true
        this.tick()
      })
      .catch((error) => {
        if (this.playback?.token !== token || sequence.current !== current) return
        current.finishedBySurface = true
        this.publish(sequence, [], {
          code: 'playback_failed',
          message: error.message || '演出失败，已跳过本句。',
          retryable: true
        })
        this.tick()
      })
  }
  stopSurface() {
    this.playback = null
    this.surface?.stop()
  }
  tick() {
    const now = this.now()
    for (const sequence of this.sequences.values()) {
      let iterations = 0
      this.advance(sequence)
      while (sequence.current && iterations++ < 1000) {
        const current = sequence.current
        const isForeground = sequence.id === this.foreground
        const elapsed = now - current.startedAt
        const done =
          isForeground && this.surface
            ? current.finishedBySurface || elapsed > current.duration + 30000
            : elapsed >= current.duration
        if (!done) break
        const nextStart = isForeground ? now : current.startedAt + current.duration
        this.finish(sequence, current.guide, 'completed')
        this.advance(sequence, nextStart)
      }
      if (sequence.current) this.publish(sequence)
    }
  }
  finish(sequence, guide, outcome) {
    if (guide.finish) return
    if (sequence.current?.guide === guide) {
      if (sequence.id === this.foreground) this.stopSurface()
      sequence.current = null
    }
    sequence.queue = sequence.queue.filter((item) => item !== guide)
    const finish = { guideId: guide.id, outcome }
    guide.finish = finish
    for (const resolve of guide.waiters) resolve(finish)
    guide.waiters.clear()
    // 完成后只保留编号和终态，释放文本与音频材料。
    this.guides.set(guide.id, { id: guide.id, sequenceId: sequence.id, finish })
    sequence.terminal.set(guide.id, finish)
    this.publish(sequence, [finish])
  }
  async removeGuides(ids) {
    return this.remove(ids, false)
  }
  async discardPendingGuides(ids) {
    return this.remove(ids, true)
  }
  remove(ids, keepCurrent) {
    const removed = []
    const changed = new Set()
    for (const id of ids) {
      const guide = this.guides.get(id)
      if (!guide || guide.finish) continue
      const sequence = this.require(guide.sequenceId)
      if (keepCurrent && sequence.current?.guide === guide) continue
      this.finish(sequence, guide, 'removed')
      changed.add(sequence)
      removed.push(id)
    }
    for (const sequence of changed) {
      this.advance(sequence)
      this.publish(sequence)
    }
    return removed
  }
  async setForeground(id) {
    if (id === this.foreground) return
    if (id !== null) this.require(id)
    this.tick()
    const previous = this.foreground ? this.require(this.foreground) : null
    this.stopSurface()
    this.foreground = id
    if (previous) this.publish(previous)
    const sequence = id ? this.require(id) : null
    this.surface?.showScene(sequence?.scene || null)
    if (sequence) {
      if (sequence.current) {
        sequence.current.startedAt = this.now()
        sequence.current.finishedBySurface = false
        this.play(sequence)
      } else this.advance(sequence)
      this.publish(sequence)
    }
  }
  async setScene(id, scene) {
    const sequence = this.require(id)
    sequence.scene = structuredClone(scene)
    if (id === this.foreground) this.surface?.showScene(scene)
    this.publish(sequence)
  }
  snapshot(sequence) {
    const current = sequence.current
    const elapsed = current ? Math.max(0, this.now() - current.startedAt) : 0
    let state = 'empty'
    if (sequence.queue.length) state = 'waiting'
    if (current) state = 'playing'
    if (sequence.closed) state = 'closed'
    return {
      sequenceId: sequence.id,
      revision: sequence.revision,
      foreground: sequence.id === this.foreground,
      state,
      current: current
        ? {
            guideId: current.guide.id,
            slotId: current.guide.slotId,
            elapsedMs: elapsed,
            estimatedDurationMs: current.duration,
            revealedCharacters: Math.floor(
              [...(current.guide.text || '')].length *
                Math.min(1, elapsed / Math.max(1, current.duration))
            )
          }
        : null,
      waitingFor: !current && sequence.queue.length ? sequence.queue[0].id : null
    }
  }
  async getProgress(id) {
    return this.snapshot(this.require(id))
  }
  publish(sequence, finished = [], problem = null) {
    sequence.revision += 1
    const update = { snapshot: this.snapshot(sequence), finished, problem }
    for (const listener of [...this.listeners, ...sequence.observers]) {
      try {
        listener(update)
      } catch {
        /* 显示错误不能阻塞队列。 */
      }
    }
  }
  observe(id, listener) {
    const sequence = this.require(id)
    listener({ snapshot: this.snapshot(sequence), finished: [], problem: null })
    sequence.observers.add(listener)
    return () => sequence.observers.delete(listener)
  }
  observeAll(listener) {
    this.listeners.add(listener)
    return () => this.listeners.delete(listener)
  }
  waitForGuideFinish(id, signal) {
    const guide = this.guides.get(id)
    if (!guide) return Promise.reject(problemError('guide_unavailable', '演出条目已失效。'))
    if (signal?.aborted) return Promise.reject(new DOMException('等待已取消', 'AbortError'))
    if (guide.finish) return Promise.resolve(guide.finish)
    return new Promise((resolve, reject) => {
      function complete(result) {
        signal?.removeEventListener('abort', abort)
        guide.waiters.delete(complete)
        resolve(result)
      }
      function abort() {
        guide.waiters.delete(complete)
        reject(new DOMException('等待已取消', 'AbortError'))
      }
      guide.waiters.add(complete)
      signal?.addEventListener('abort', abort, { once: true })
    })
  }
  async closeSequence(id) {
    const sequence = this.sequences.get(id)
    if (!sequence) return
    sequence.closed = true
    for (const guide of [...sequence.queue]) this.finish(sequence, guide, 'closed')
    if (id === this.foreground) {
      this.stopSurface()
      this.foreground = null
      this.surface?.showScene(null)
    }
    this.publish(sequence)
    for (const guideId of sequence.terminal.keys()) this.guides.delete(guideId)
    this.sequences.delete(id)
  }
  dispose() {
    clearInterval(this.timer)
    for (const id of this.sequences.keys()) void this.closeSequence(id)
    this.listeners.clear()
  }
}
