import { performance as clock } from 'node:perf_hooks'
import { uuid, copy, requireValue, SnapshotBus, abortable, fingerprint } from '../core/support.js'

/** @typedef {import('../../shared/contracts/presentation.js').Guide} Guide */
/** @typedef {import('../../shared/contracts/presentation.js').Scene} Scene */
/**
 * @typedef {object} ScheduledGuide 后端计划项，仅含资源引用，不含音频或模型实例。
 * @property {string} id 计划项身份。
 * @property {Guide | null} guide 就绪材料；终态后释放内容，但保留 finish 供等待者查询。
 * @property {string} sequenceId 所属序列。
 * @property {number} readyAt 材料就绪的单调时标；未就绪为 Infinity。
 * @property {number} createdAt 追加时标，补算时不得在追加前开始。
 * @property {number} durationMs 固定计划时长。
 * @property {number | null} startedAt 单调时钟上的计划开始时间。
 * @property {number | null} endsAt 计划截止时间，不随客户端实际播放改变。
 * @property {import('../../shared/contracts/presentation.js').GuideFinish | null} finish 一次固定的终态。
 * @property {Set<(finish: import('../../shared/contracts/presentation.js').GuideFinish) => void>} waiters 等待者，单独取消不删除计划项。
 * @property {boolean} transition 是否为过渡内容，不计入 Turn 普通演出完成条件。
 * @property {boolean} recurring 是否驻留，直到被停止或普通内容抢占。
 * @property {import('../../shared/contracts/presentation.js').ModelStateTarget | null} target 显式状态目标。
 * @property {import('../../shared/contracts/presentation.js').ModelStateEffect[]} [effects] 开始时冻结的状态影响。
 * @property {string} [effectsModel] 开始时的模型材料摘要，防止旧动作改写换装后的状态。
 */

/**
 * @typedef {object} SequenceState
 * @property {string} id 本次后端运行的序列身份。
 * @property {Scene} scene 当前场景快照。
 * @property {string[]} queue 普通计划项顺序，包含当前项。
 * @property {string[]} transitions 过渡项顺序，普通内容就绪时被清除。
 * @property {string | null} current 当前普通项身份。
 * @property {string | null} transition 当前过渡项身份。
 * @property {number} revision 观察版本，不是持久化版本。
 * @property {boolean} closed 是否已关闭。
 * @property {ReturnType<typeof setTimeout> | null} timer 下一计划截止时间的唤醒句柄。
 * @property {Map<string, import('../../shared/contracts/presentation.js').ModelStateSnapshot>} modelStates 计划状态；未知值保持 null。
 * @property {Map<string, object>} mappings 每个演员位置的模型映射材料。
 * @property {import('../../shared/contracts/presentation.js').PerformanceSelection} defaultPresentation 空闲展示选择。
 */

/**
 * 后端演出调度：所有 sequence 独立按计划时间推进，不存在前后台或播放控制者。
 * 完成表示计划区间结束，不宣称任何客户端实际播放成功；没有订阅者也照常推进。
 */
export class Performance {
  /** @type {Map<string, SequenceState>} */
  sequences = new Map()
  /** @type {Map<string, ScheduledGuide>} */
  guides = new Map()
  bus = new SnapshotBus()
  runtimeId = uuid()
  closed = false

  /**
   * 绑定后端计划时钟与模型元数据，不接收客户端播放反馈。
   * @param {object} [options] 宿主依赖与无声阅读策略。
   * @param {() => number} [options.now] 毫秒单调时钟，不能用会被校时的墙上时间。
   * @param {typeof setTimeout} [options.setTimer] 截止时间唤醒能力。
   * @param {typeof clearTimeout} [options.clearTimer] 取消唤醒能力。
   * @param {(presentation: import('../../shared/contracts/presentation.js').ModelPresentation) => Promise<object>} [options.resolveModel] 模型映射解析能力。
   * @param {number} [options.readingMsPerCharacter] 无音频时每字估计阅读时间。
   * @param {number} [options.minimumReadingMs] 非空文本的最短阅读时间。
   */
  constructor({
    now = () => clock.now(),
    setTimer = setTimeout,
    clearTimer = clearTimeout,
    resolveModel = async () => ({}),
    readingMsPerCharacter = 80,
    minimumReadingMs = 800
  } = {}) {
    Object.assign(this, {
      now,
      setTimer,
      clearTimer,
      resolveModel,
      readingMsPerCharacter,
      minimumReadingMs
    })
  }

  /** 运行期身份关闭后失效，不能用旧客户端请求重新创建序列。 */
  sequence(id) {
    const sequence = this.sequences.get(id)
    requireValue(sequence && !sequence.closed, 'sequence_unavailable', '演出序列不存在或已经关闭。')
    return sequence
  }

  /** 演员位置必须唯一，后续 guide 和模型状态都用位置身份寻址。 */
  validateScene(scene) {
    requireValue(scene && Array.isArray(scene.slots))
    requireValue(new Set(scene.slots.map((slot) => slot.id)).size === scene.slots.length)
    for (const slot of scene.slots) requireValue(typeof slot.id === 'string' && slot.id.length > 0)
  }

  /** 音频优先决定发声区间，动作时长可延长计划；无音频才按 Unicode 字符数估算。 */
  duration(guide) {
    const audioMs = guide.audio?.durationMs
    if (audioMs != null)
      requireValue(Number.isFinite(audioMs) && audioMs >= 0 && audioMs <= 86400000)
    const textMs = guide.text
      ? Math.max(this.minimumReadingMs, Array.from(guide.text).length * this.readingMsPerCharacter)
      : 0
    const motionMs = guide.motionDurationMs ?? 0
    requireValue(Number.isFinite(motionMs) && motionMs >= 0 && motionMs <= 86400000)
    return Math.max(audioMs ?? textMs, motionMs)
  }

  /** 建立持续可追加的计划队列；不选择任何客户端的展示位置。
   * @param {Scene} scene 场景快照。
   * @returns {Promise<string>} 本次后端运行中的 sequence 身份。
   */
  async createSequence(scene) {
    requireValue(!this.closed, 'backend_closed', '后端已关闭。')
    this.validateScene(scene)
    const mappings = await this.mappings(scene)
    const id = uuid()
    this.sequences.set(id, {
      id,
      scene: copy(scene),
      queue: [],
      transitions: [],
      current: null,
      transition: null,
      revision: 0,
      closed: false,
      timer: null,
      modelStates: new Map(),
      mappings,
      defaultPresentation: { motion: 'auto', expression: 'auto' }
    })
    return id
  }

  /** 缺失模型材料降级为空能力，文字与音频仍可继续演出。 */
  async mappings(scene) {
    const mappings = new Map()
    for (const slot of scene.slots) {
      try {
        mappings.set(
          slot.id,
          slot.presentation ? await this.resolveModel(copy(slot.presentation)) : {}
        )
      } catch {
        mappings.set(slot.id, {})
      }
    }
    return mappings
  }
  /** 只接收当前模型声明的状态影响，不把任意动作名当作状态切换指令。 */
  effects(sequence, record) {
    const mapping = sequence.mappings.get(record.guide.slotId) ?? {}
    const effects = [
      ...(mapping.stateEffects?.motions?.[record.guide.performance.motion] ?? []),
      ...(mapping.stateEffects?.expressions?.[record.guide.performance.expression] ?? [])
    ]
    if (record.target) effects.push({ state: record.target.state, value: record.target.value })
    return effects.filter((effect) => mapping.states?.[effect.state])
  }
  /** 动作尚未计划完成时状态设为未知，同时固定模型身份供结算核对。 */
  startEffects(sequence, record) {
    record.effects = this.effects(sequence, record)
    record.effectsModel = fingerprint(
      sequence.scene.slots.find((slot) => slot.id === record.guide.slotId)?.presentation
    )
    for (const effect of record.effects) {
      const key = record.guide.slotId + ':' + effect.state
      const state = sequence.modelStates.get(key) ?? {
        slotId: record.guide.slotId,
        state: effect.state,
        value: null,
        pending: null
      }
      state.value = null
      sequence.modelStates.set(key, state)
    }
  }
  /** 先构造并校验材料，再由批量追加统一入队，避免失败后留下半批 guide。 */
  makeGuide(sequence, guide, transition = false) {
    requireValue(
      sequence.scene.slots.some((slot) => slot.id === guide.slotId),
      'invalid_slot',
      '演员位置不存在。'
    )
    requireValue(
      guide.performance &&
        typeof guide.performance.motion === 'string' &&
        typeof guide.performance.expression === 'string'
    )
    if (!transition)
      requireValue(
        typeof guide.ready === 'boolean' && (guide.text === null || typeof guide.text === 'string')
      )
    const material = transition
      ? { text: null, translation: null, emotion: null, audio: null, ready: true, ...guide }
      : guide
    return {
      id: uuid(),
      guide: copy(material),
      sequenceId: sequence.id,
      createdAt: this.now(),
      readyAt: material.ready ? this.now() : Infinity,
      durationMs: this.duration(material),
      startedAt: null,
      endsAt: null,
      finish: null,
      waiters: new Set(),
      transition,
      recurring: Boolean(guide.recurring),
      target: null
    }
  }

  /** 原子追加普通演出材料；队首未就绪时禁止跳过。
   * @param {string} sequenceId 目标序列。
   * @param {Guide[]} guides 有序材料。
   * @returns {Promise<string[]>} 对应的 guide 身份。
   */
  async appendGuides(sequenceId, guides) {
    const sequence = this.sequence(sequenceId)
    requireValue(Array.isArray(guides) && guides.length <= 10000)
    const records = guides.map((guide) => this.makeGuide(sequence, guide))
    for (const record of records) {
      this.guides.set(record.id, record)
      sequence.queue.push(record.id)
    }
    this.advance(sequence)
    this.publish(sequence)
    return records.map((record) => record.id)
  }

  /** 追加等待普通内容时的过渡计划；正在普通演出时忽略。
   * @param {string} sequenceId 目标序列。
   * @param {import('../../shared/contracts/presentation.js').TransitionGuide[]} guides 动作材料。
   * @returns {Promise<string[]>} 接受的 guide 身份。
   */
  async appendTransitionGuides(sequenceId, guides) {
    const sequence = this.sequence(sequenceId)
    this.advance(sequence)
    if (sequence.current) return []
    requireValue(Array.isArray(guides) && guides.length <= 10000)
    const records = guides.map((guide) => this.makeGuide(sequence, guide, true))
    for (const record of records) {
      this.guides.set(record.id, record)
      sequence.transitions.push(record.id)
    }
    this.advance(sequence)
    this.publish(sequence)
    return records.map((record) => record.id)
  }

  /**
   * 清理全部过渡计划，并结算其等待者。
   * @param {string} sequenceId 目标序列。
   * @returns {Promise<void>} 普通计划内容保持不变。
   */
  async stopTransitionGuide(sequenceId) {
    const sequence = this.sequence(sequenceId)
    this.clearTransitions(sequence)
    this.publish(sequence)
  }

  /** 普通演出抢占时统一移除过渡项，不能把被打断的目标动作结算为成功。 */
  clearTransitions(sequence) {
    for (const id of [...sequence.transitions])
      this.finish(sequence, this.guides.get(id), 'removed')
    sequence.transitions = []
    sequence.transition = null
  }

  /** 将模型逻辑状态设为目标；快照表示计划目标，实际实例状态由各客户端持有。
   * @param {string} sequenceId 目标序列。
   * @param {import('../../shared/contracts/presentation.js').ModelStateTarget} target 演员位置、状态键与布尔目标。
   * @returns {Promise<string | null>} 过渡 guide，或无需新增。
   */
  async setModelState(sequenceId, target) {
    const sequence = this.sequence(sequenceId)
    requireValue(typeof target.value === 'boolean')
    const slot = sequence.scene.slots.find((item) => item.id === target.slotId)
    requireValue(slot?.presentation, 'unsupported_state', '此演员没有模型状态能力。')
    const before = fingerprint(slot.presentation)
    const mapping = await this.resolveModel(copy(slot.presentation))
    requireValue(
      !sequence.closed &&
        fingerprint(
          sequence.scene.slots.find((item) => item.id === target.slotId)?.presentation
        ) === before,
      'stale_request',
      '模型已更换。'
    )
    const definition = mapping.states?.[target.state]
    requireValue(
      definition?.on?.motion &&
        definition?.off?.motion &&
        definition.on.motion !== definition.off.motion &&
        mapping.motions?.[definition.on.motion] &&
        mapping.motions?.[definition.off.motion],
      'unsupported_state',
      '模型未声明该状态。'
    )
    this.advance(sequence)
    if (sequence.current) return null
    const key = `${target.slotId}:${target.state}`
    const state = sequence.modelStates.get(key) ?? {
      slotId: target.slotId,
      state: target.state,
      value: definition.initial ?? null,
      pending: null
    }
    if (state.pending?.value === target.value) return state.pending.guideId
    if (!state.pending && state.value === target.value) return null
    const action = target.value ? definition.on : definition.off
    const record = this.makeGuide(
      sequence,
      {
        slotId: target.slotId,
        performance: { motion: action.motion, expression: 'auto' },
        recurring: false,
        motionDurationMs: action.durationMs ?? 1000
      },
      true
    )
    record.target = copy(target)
    this.guides.set(record.id, record)
    sequence.transitions.push(record.id)
    state.pending = { value: target.value, guideId: record.id }
    sequence.modelStates.set(key, state)
    this.advance(sequence)
    this.publish(sequence)
    return record.id
  }

  /**
   * 为尚未就绪的普通 guide 提供最终音频；迟到材料不能覆盖既有内容。
   * @param {string} guideId 目标计划项。
   * @param {import('../../shared/contracts/presentation.js').GuideMaterials} materials 音频或明确的无声结果。
   * @returns {Promise<boolean>} 本次是否使其就绪；已就绪或终态返回 false。
   */
  async resolveGuide(guideId, materials) {
    const record = this.guides.get(guideId)
    requireValue(record, 'guide_unavailable', '演出内容不存在。')
    if (record.finish || record.guide.ready) return false
    const sequence = this.sequence(record.sequenceId)
    const guide = { ...record.guide, audio: copy(materials.audio), ready: true }
    record.durationMs = this.duration(guide)
    record.guide = guide
    record.readyAt = this.now()
    this.advance(sequence)
    this.publish(sequence)
    return true
  }

  /**
   * 删除目标 guide，包括计划当前句；不根据客户端播放位置判断。
   * @param {string[]} guideIds 目标集合，重复或已终态项不会重复结算。
   * @returns {Promise<string[]>} 实际移除的身份。
   */
  async removeGuides(guideIds) {
    return this.remove(guideIds, false)
  }

  /**
   * 原子保留操作生效时的计划当前句，删除目标集合中的后续内容。
   * @param {string[]} guideIds 目标集合。
   * @returns {Promise<string[]>} 实际移除的身份；当前句不在其中。
   */
  async discardPendingGuides(guideIds) {
    return this.remove(guideIds, true)
  }

  /** 先按当前时钟补算，再判断哪句应保留，避免定时器延迟导致保留错误的旧句。 */
  remove(ids, preserveCurrent) {
    const sequences = new Set()
    for (const id of ids) {
      const record = this.guides.get(id)
      if (record && !record.finish) sequences.add(this.sequence(record.sequenceId))
    }
    for (const sequence of sequences) this.advance(sequence)
    const removed = []
    for (const id of new Set(ids)) {
      const record = this.guides.get(id)
      if (!record || record.finish) continue
      const sequence = this.sequence(record.sequenceId)
      if (preserveCurrent && sequence.current === id) continue
      this.finish(sequence, record, 'removed')
      removed.push(id)
    }
    for (const sequence of sequences) {
      this.advance(sequence)
      this.publish(sequence)
    }
    return removed
  }

  /**
   * 更新场景快照；不得删除仍被未完成内容使用的位置。
   * @param {string} sequenceId 目标序列。
   * @param {Scene} scene 新场景，模型更换会清除旧模型的逻辑状态。
   * @returns {Promise<void>} 场景已更新并发布。
   */
  async setScene(sequenceId, scene) {
    const sequence = this.sequence(sequenceId)
    this.validateScene(scene)
    for (const id of [...sequence.queue, ...sequence.transitions]) {
      const record = this.guides.get(id)
      requireValue(
        scene.slots.some((slot) => slot.id === record.guide.slotId),
        'slot_in_use',
        '未完成演出仍使用此演员位置。'
      )
    }
    for (const old of sequence.scene.slots) {
      const next = scene.slots.find((slot) => slot.id === old.id)
      if (fingerprint(old.presentation) === fingerprint(next?.presentation ?? null)) continue
      for (const [key, state] of sequence.modelStates) {
        if (state.slotId !== old.id) continue
        for (const id of [...sequence.transitions]) {
          const record = this.guides.get(id)
          if (record.target?.slotId === old.id) this.finish(sequence, record, 'removed')
        }
        sequence.modelStates.delete(key)
      }
    }
    sequence.mappings = await this.mappings(scene)
    sequence.scene = copy(scene)
    this.publish(sequence)
  }

  /**
   * 设置空闲或等待材料时的逻辑展示选择。
   * @param {string} sequenceId 目标序列。
   * @param {import('../../shared/contracts/presentation.js').PerformanceSelection} selection 动作与表情选择。
   * @returns {Promise<void>} 默认选择已发布，不新增普通演出内容。
   */
  async setDefaultPresentation(sequenceId, selection) {
    const sequence = this.sequence(sequenceId)
    sequence.defaultPresentation = copy(selection)
    this.publish(sequence)
  }

  /** 每项只结算一次；被移除的动作不能确认状态已到目标，模型更换后的状态也不能被旧效果覆盖。 */
  finish(sequence, record, outcome) {
    if (!record || record.finish) return
    record.finish = { guideId: record.id, outcome, problem: null }
    sequence.queue = sequence.queue.filter((id) => id !== record.id)
    sequence.transitions = sequence.transitions.filter((id) => id !== record.id)
    if (sequence.current === record.id) sequence.current = null
    if (sequence.transition === record.id) sequence.transition = null
    for (const effect of record.effects ?? []) {
      const state = sequence.modelStates.get(record.guide.slotId + ':' + effect.state)
      if (
        state &&
        record.effectsModel ===
          fingerprint(
            sequence.scene.slots.find((slot) => slot.id === record.guide.slotId)?.presentation
          )
      )
        state.value = outcome === 'completed' ? effect.value : null
    }
    if (record.target) {
      const state = sequence.modelStates.get(`${record.target.slotId}:${record.target.state}`)
      if (state) {
        if (outcome === 'completed') state.value = record.target.value
        if (state.pending?.guideId === record.id) state.pending = null
      }
    }
    for (const resolve of record.waiters) resolve(copy(record.finish))
    record.waiters.clear()
    record.guide = null
    this.publish(sequence, [record.finish])
  }

  /** 按截止时间补算计划；事件循环晚唤醒不能让所有后续句累计延迟。 */
  advance(sequence) {
    if (sequence.closed) return
    if (sequence.timer) this.clearTimer(sequence.timer)
    sequence.timer = null
    let anchor = this.now()
    while (true) {
      const current = this.guides.get(sequence.current)
      if (current) {
        if (this.now() < current.endsAt) break
        anchor = current.endsAt
        this.finish(sequence, current, 'completed')
      }
      const next = this.guides.get(sequence.queue[0])
      if (!next?.guide.ready) break
      this.clearTransitions(sequence)
      // 可以补算连续已就绪内容，但不能把尚未到达的音频或新追加内容回填到过去播放。
      next.startedAt = Math.max(anchor, next.readyAt, next.createdAt)
      next.endsAt = next.startedAt + next.durationMs
      this.startEffects(sequence, next)
      sequence.current = next.id
      this.publish(sequence)
      if (next.endsAt > this.now()) break
    }
    if (!sequence.current) {
      let transition = this.guides.get(sequence.transition)
      if (transition && !transition.recurring && transition.endsAt <= this.now()) {
        this.finish(sequence, transition, 'completed')
        transition = null
      }
      if (!transition && sequence.transitions.length) {
        transition = this.guides.get(sequence.transitions[0])
        transition.startedAt = this.now()
        transition.endsAt = transition.startedAt + Math.max(transition.durationMs, 1)
        this.startEffects(sequence, transition)
        sequence.transition = transition.id
        this.publish(sequence)
      }
    }
    const timed = this.guides.get(sequence.current) ?? this.guides.get(sequence.transition)
    if (timed && !timed.recurring) {
      sequence.timer = this.setTimer(
        () => {
          this.advance(sequence)
          this.publish(sequence)
        },
        Math.max(1, timed.endsAt - this.now())
      )
      sequence.timer?.unref?.()
    }
  }

  /** 交付一次时间锚点，客户端据此本地推算逐字进度，不需要每帧请求后端。 */
  snapshot(sequence) {
    const now = this.now()
    const progress = (id) => {
      const record = this.guides.get(id)
      if (!record || record.finish) return null
      return {
        guideId: id,
        slotId: record.guide.slotId,
        guide: copy(record.guide),
        startedAtMs: record.startedAt,
        durationMs: record.durationMs,
        elapsedMs: Math.min(record.durationMs, Math.max(0, now - record.startedAt)),
        recurring: record.recurring
      }
    }
    let state = 'empty'
    if (sequence.closed) state = 'closed'
    else if (sequence.current) state = 'playing'
    else if (sequence.queue.length) state = 'waiting'
    return {
      runtimeId: this.runtimeId,
      sequenceId: sequence.id,
      revision: sequence.revision,
      serverTimeMs: now,
      scene: copy(sequence.scene),
      state,
      current: progress(sequence.current),
      transition: progress(sequence.transition),
      waitingFor: state === 'waiting' ? sequence.queue[0] : null,
      defaultPresentation: copy(sequence.defaultPresentation),
      modelStates: copy([...sequence.modelStates.values()])
    }
  }

  /** 版本化完整快照用于重连恢复，finished 只通知本次新结算的项。 */
  publish(sequence, finished = []) {
    sequence.revision += 1
    this.bus.emit(sequence.id, { snapshot: this.snapshot(sequence), finished, problem: null })
  }

  /**
   * 获取权威计划快照，包含客户端本地计算进度所需的时标与当前材料。
   * @param {string} sequenceId 目标序列。
   * @returns {Promise<import('../../shared/contracts/presentation.js').SequenceSnapshot>} 查询时已补算的独立快照。
   */
  async getProgress(sequenceId) {
    const sequence = this.sequence(sequenceId)
    this.advance(sequence)
    return this.snapshot(sequence)
  }

  /**
   * 订阅时立即交付当前快照，之后接收版本化变更；取消监听不改变队列。
   * @param {string} sequenceId 目标序列。
   * @param {(update: import('../../shared/contracts/presentation.js').PerformanceUpdate) => void} listener 完整更新接收者。
   * @returns {() => void} 取消监听。
   */
  observe(sequenceId, listener) {
    const sequence = this.sequence(sequenceId)
    this.advance(sequence)
    return this.bus.subscribe(sequenceId, listener, {
      snapshot: this.snapshot(sequence),
      finished: [],
      problem: null
    })
  }

  /**
   * 等待计划终态；取消此等待不删除 guide。
   * @param {string} guideId 计划项身份。
   * @param {AbortSignal} [signal] 只取消当前等待。
   * @returns {Promise<import('../../shared/contracts/presentation.js').GuideFinish>} 完成、移除或关闭终态。
   */
  async waitForGuideFinish(guideId, signal) {
    const record = this.guides.get(guideId)
    requireValue(record, 'guide_unavailable', '演出内容不存在。')
    this.sequence(record.sequenceId)
    if (record.finish) return copy(record.finish)
    let complete
    const waiting = new Promise((resolve) => {
      complete = resolve
      record.waiters.add(resolve)
    })
    try {
      return await abortable(waiting, signal)
    } finally {
      record.waiters.delete(complete)
    }
  }

  /**
   * 关闭序列并结算全部等待，重复关闭无副作用。
   * @param {string} sequenceId 目标序列。
   * @returns {Promise<void>} 已释放计时器和计划项，旧 guide 身份不再可查询。
   */
  async closeSequence(sequenceId) {
    const sequence = this.sequences.get(sequenceId)
    if (!sequence || sequence.closed) return
    sequence.closed = true
    if (sequence.timer) this.clearTimer(sequence.timer)
    for (const id of [...sequence.queue, ...sequence.transitions])
      this.finish(sequence, this.guides.get(id), 'closed')
    this.publish(sequence)
    for (const [id, record] of this.guides)
      if (record.sequenceId === sequenceId) this.guides.delete(id)
  }

  /**
   * 宿主退出时释放所有定时器与等待，不等待前端。
   * @returns {Promise<void>} 全部序列关闭，不删除 Chat 存档。
   */
  async close() {
    this.closed = true
    for (const id of this.sequences.keys()) await this.closeSequence(id)
    this.bus.clear()
  }
}
