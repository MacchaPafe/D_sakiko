import { performance as clock } from 'node:perf_hooks'
import { uuid, copy, requireValue, SnapshotBus, abortable, fingerprint } from '../core/support.js'

/** @typedef {import('../../shared/contracts/presentation.js').Guide} Guide */
/** @typedef {import('../../shared/contracts/presentation.js').Scene} Scene */

/**
 * 后端演出计划项记录。
 * @typedef {object} ScheduledGuide
 * @property {string} id 计划项唯一 ID。
 * @property {Guide | null} guide 演出内容材料（结算完成后置为 null 以释放内存）。
 * @property {string} sequenceId 所属演出序列 ID。
 * @property {number} readyAt 材料准备就绪的单调时钟时间戳（未就绪时为 Infinity）。
 * @property {number} createdAt 计划项创建时的单调时钟时间戳。
 * @property {number} durationMs 计划持续时长（毫秒）。
 * @property {number | null} startedAt 计划开始播放的单调时钟时间戳。
 * @property {number | null} endsAt 计划结束的单调时钟时间戳。
 * @property {import('../../shared/contracts/presentation.js').GuideFinish | null} finish 计划项的结算终态。
 * @property {Set<(finish: import('../../shared/contracts/presentation.js').GuideFinish) => void>} waiters 等待该计划项结束的回调函数集合。
 * @property {boolean} transition 是否为过渡动作计划项。
 * @property {boolean} recurring 是否为持续循环驻留动作（直到被主动停止或被普通台词演出打断）。
 * @property {import('../../shared/contracts/presentation.js').ModelStateTarget | null} target 显式指定的模型状态切换目标。
 * @property {import('../../shared/contracts/presentation.js').ModelStateEffect[]} [effects] 演出开始时锁定的模型状态变更效果。
 * @property {string} [effectsModel] 演出开始时角色模型配置的指纹，用于防止中途换装后旧动作错误修改新模型的状态。
 */

/**
 * 演出序列的运行时状态。
 * @typedef {object} SequenceState
 * @property {string} id 演出序列唯一 ID。
 * @property {Scene} scene 当前场景与演员位置快照。
 * @property {string[]} queue 普通台词演出计划项 ID 队列（包含当前正在演出的项）。
 * @property {string[]} transitions 过渡动作计划项 ID 队列（当普通台词演出就绪时会被自动清空）。
 * @property {string | null} current 当前正在演出的普通计划项 ID。
 * @property {string | null} transition 当前正在演出的过渡计划项 ID。
 * @property {number} revision 状态快照递增版本号。
 * @property {boolean} closed 序列是否已关闭。
 * @property {ReturnType<typeof setTimeout> | null} timer 驱动下一个计划项结束结算的定时器句柄。
 * @property {Map<string, import('../../shared/contracts/presentation.js').ModelStateSnapshot>} modelStates 各演员位置的模型状态记录。
 * @property {Map<string, object>} mappings 各演员位置的模型动作与状态映射配置。
 * @property {import('../../shared/contracts/presentation.js').PerformanceSelection} defaultPresentation 空闲时的默认动作与表情选择。
 */

/**
 * 演出时间线与状态管理模块，负责在后端按计划时长推进台词演出、过渡动作与模型状态切换。
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
   * 创建一个演出管理器。
   * @param {object} [options] 创建选项。
   * @param {() => number} [options.now] 获取当前单调时钟毫秒数的函数（默认使用 perf_hooks.performance.now）。
   * @param {typeof setTimeout} [options.setTimer] 设置定时器的函数（测试时可替换为虚拟时钟）。
   * @param {typeof clearTimeout} [options.clearTimer] 清除定时器的函数。
   * @param {(presentation: import('../../shared/contracts/presentation.js').ModelPresentation) => Promise<object>} [options.resolveModel] 自定义函数，根据模型信息解析动作、表情与状态定义。
   * @param {number} [options.readingMsPerCharacter] 无语音时，按文本字数估算阅读时长的单字毫秒数，默认 80ms。
   * @param {number} [options.minimumReadingMs] 无语音时，单句非空台词的最短持续毫秒数，默认 800ms。
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

  /**
   * 获取指定 ID 的活动演出序列（若不存在或已关闭则抛出异常）。
   */
  sequence(id) {
    const sequence = this.sequences.get(id)
    requireValue(sequence && !sequence.closed, 'sequence_unavailable', '演出序列不存在或已经关闭。')
    return sequence
  }

  /**
   * 校验场景中的演员位置（slot）列表是否合法且 ID 唯一。
   */
  validateScene(scene) {
    requireValue(scene && Array.isArray(scene.slots))
    requireValue(new Set(scene.slots.map((slot) => slot.id)).size === scene.slots.length)
    for (const slot of scene.slots) requireValue(typeof slot.id === 'string' && slot.id.length > 0)
  }

  /**
   * 计算单条演出计划项的持续时长（优先采用音频时长，无音频时按字符数估算，并与动作时长取较大值）。
   */
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

  /**
   * 创建一个新的演出序列。
   * @param {Scene} scene 初始场景与演员位置配置。
   * @returns {Promise<string>} 新创建的演出序列 ID。
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

  /**
   * 解析场景中各演员位置的模型映射配置（若某个模型资源缺失则降级为空对象，不阻断字幕与音频演出）。
   */
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

  /**
   * 提取计划项所触发的模型状态变更效果。
   */
  effects(sequence, record) {
    const mapping = sequence.mappings.get(record.guide.slotId) ?? {}
    const effects = [
      ...(mapping.stateEffects?.motions?.[record.guide.performance.motion] ?? []),
      ...(mapping.stateEffects?.expressions?.[record.guide.performance.expression] ?? [])
    ]
    if (record.target) effects.push({ state: record.target.state, value: record.target.value })
    return effects.filter((effect) => mapping.states?.[effect.state])
  }

  /**
   * 在计划项开始演出时记录其状态变更效果，并将受影响的模型状态暂时设为未确定（null），直到动作完整结束时才写入目标状态。
   */
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

  /**
   * 校验并构造单条计划项记录。
   */
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

  /**
   * 向演出序列末尾批量追加普通台词演出计划项。
   * @param {string} sequenceId 目标演出序列 ID。
   * @param {Guide[]} guides 按顺序排列的演出内容列表。
   * @returns {Promise<string[]>} 新追加的各计划项 ID 列表。
   */
  async appendGuides(sequenceId, guides) {
    const sequence = this.sequence(sequenceId)
    requireValue(Array.isArray(guides) && guides.length <= 10000)
    // 先全部校验并构造完毕再统一入队，防止数组中途某一项校验失败时留下半批已入队的计划项。
    const records = guides.map((guide) => this.makeGuide(sequence, guide))
    for (const record of records) {
      this.guides.set(record.id, record)
      sequence.queue.push(record.id)
    }
    this.advance(sequence)
    this.publish(sequence)
    return records.map((record) => record.id)
  }

  /**
   * 在空闲或等待普通台词就绪期间，向序列追加过渡动作计划项（若当前正在播放普通台词则直接忽略并返回空数组）。
   * @param {string} sequenceId 目标演出序列 ID。
   * @param {import('../../shared/contracts/presentation.js').TransitionGuide[]} guides 过渡动作内容列表。
   * @returns {Promise<string[]>} 已接受的过渡计划项 ID 列表。
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
   * 停止并清空指定序列中的所有过渡动作计划项。
   * @param {string} sequenceId 目标演出序列 ID。
   * @returns {Promise<void>}
   */
  async stopTransitionGuide(sequenceId) {
    const sequence = this.sequence(sequenceId)
    this.clearTransitions(sequence)
    this.publish(sequence)
  }

  /**
   * 将所有未完成的过渡计划项结算为已移除（removed）。
   * 当普通台词就绪并抢占演出时调用此方法，避免把半途被打断的过渡动作误判为正常播放完成（completed）。
   */
  clearTransitions(sequence) {
    for (const id of [...sequence.transitions])
      this.finish(sequence, this.guides.get(id), 'removed')
    sequence.transitions = []
    sequence.transition = null
  }

  /**
   * 切换指定演员位置的模型布尔状态（如戴面具/摘面具），并排入对应的状态切换过渡动作。
   * @param {string} sequenceId 目标演出序列 ID。
   * @param {import('../../shared/contracts/presentation.js').ModelStateTarget} target 目标演员位置、状态名称与目标布尔值。
   * @returns {Promise<string | null>} 排入的过渡计划项 ID；若当前已在目标状态或正在进行普通演出则返回 null。
   */
  async setModelState(sequenceId, target) {
    const sequence = this.sequence(sequenceId)
    requireValue(typeof target.value === 'boolean')
    const slot = sequence.scene.slots.find((item) => item.id === target.slotId)
    requireValue(slot?.presentation, 'unsupported_state', '此演员没有模型状态能力。')
    const before = fingerprint(slot.presentation)
    const mapping = await this.resolveModel(copy(slot.presentation))
    // 异步解析模型配置期间如果演员更换了模型，拒绝本次状态切换，防止用旧模型的动作驱动新模型。
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
   * 为尚未就绪的演出计划项补充合成完毕的音频（或静音降级结果），使其进入可播放就绪状态。
   * @param {string} guideId 目标计划项 ID。
   * @param {import('../../shared/contracts/presentation.js').GuideMaterials} materials 音频资源与时长信息（静音时 audio 为 null）。
   * @returns {Promise<boolean>} 若成功将该计划项更新为就绪则返回 true；若此前已就绪或已结束则返回 false。
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
   * 从演出序列中移除指定的计划项（包括当前正在演出的计划项）。
   * @param {string[]} guideIds 要移除的计划项 ID 列表。
   * @returns {Promise<string[]>} 实际被移除的计划项 ID 列表。
   */
  async removeGuides(guideIds) {
    return this.remove(guideIds, false)
  }

  /**
   * 保留序列中当前正在演出的计划项，移除指定列表中其余尚未开始演出的后续计划项。
   * @param {string[]} guideIds 待清理的计划项 ID 列表。
   * @returns {Promise<string[]>} 实际被移除的计划项 ID 列表（不包含被保留的当前句）。
   */
  async discardPendingGuides(guideIds) {
    return this.remove(guideIds, true)
  }

  /**
   * 移除计划项的内部实现。
   * 在判断哪些项属于“当前正在演出”之前先调用 advance 补算时间线，防止因定时器回调延迟而误保留事实上已经到点结束的旧句。
   */
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
   * 更新演出序列的场景配置与演员位置布局（若某个演员更换了模型，会自动清除其旧模型的状态记录）。
   * @param {string} sequenceId 目标演出序列 ID。
   * @param {Scene} scene 新的场景配置（不能删除仍被未完成计划项占用的演员位置）。
   * @returns {Promise<void>}
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
   * 设置序列在空闲或等待音频就绪时的默认动作与表情选择。
   * @param {string} sequenceId 目标演出序列 ID。
   * @param {import('../../shared/contracts/presentation.js').PerformanceSelection} selection 默认动作与表情配置。
   * @returns {Promise<void>}
   */
  async setDefaultPresentation(sequenceId, selection) {
    const sequence = this.sequence(sequenceId)
    sequence.defaultPresentation = copy(selection)
    this.publish(sequence)
  }

  /**
   * 结算单个计划项并唤醒所有等待该项结束的调用方。
   * 仅当计划项正常完成（completed）且演员中途未更换模型时，才将状态效果正式写入 modelStates。
   */
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

  /**
   * 根据当前单调时钟推进序列时间线，结算已到期的计划项并启动下一条已就绪的计划项。
   */
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
      // 为什么取 anchor、readyAt 和 createdAt 的最大值作为开始时间：
      // 若 Node.js 事件循环定时器略有延迟，已就绪的后续句可紧接上一句结束时间（anchor）补算推进，避免累计延迟；但绝不能早于该句实际创建或音频就绪的时刻开始。
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

  /**
   * 构造指定演出序列当前的进度快照。
   * 快照中附带服务端时间戳（serverTimeMs）与已播放时长（elapsedMs），供前端在本地平滑推算逐字字幕进度，无需后端逐帧推送。
   */
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

  /**
   * 向订阅者广播序列的最新快照与刚结束的计划项列表。
   */
  publish(sequence, finished = []) {
    sequence.revision += 1
    this.bus.emit(sequence.id, { snapshot: this.snapshot(sequence), finished, problem: null })
  }

  /**
   * 获取指定演出序列当前的进度与状态快照。
   * @param {string} sequenceId 目标演出序列 ID。
   * @returns {Promise<import('../../shared/contracts/presentation.js').SequenceSnapshot>} 序列当前的完整状态快照。
   */
  async getProgress(sequenceId) {
    const sequence = this.sequence(sequenceId)
    this.advance(sequence)
    return this.snapshot(sequence)
  }

  /**
   * 订阅指定演出序列的状态更新（订阅时会立即推送一次当前状态快照）。
   * @param {string} sequenceId 目标演出序列 ID。
   * @param {(update: import('../../shared/contracts/presentation.js').PerformanceUpdate) => void} listener 接收演出更新的回调函数。
   * @returns {() => void} 取消订阅的函数。
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
   * 等待指定的演出计划项播放结束（或被移除、关闭）。
   * @param {string} guideId 计划项 ID。
   * @param {AbortSignal} [signal] 控制本次等待的取消信号（取消等待不会移除该计划项本身）。
   * @returns {Promise<import('../../shared/contracts/presentation.js').GuideFinish>} 计划项的结束状态（completed、removed 或 closed）。
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
   * 关闭指定的演出序列，清理其定时器并将所有未完成的计划项结算为已关闭（closed）。
   * @param {string} sequenceId 目标演出序列 ID。
   * @returns {Promise<void>}
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
   * 关闭演出管理器，关闭所有序列并释放全部定时器与订阅。
   * @returns {Promise<void>}
   */
  async close() {
    this.closed = true
    for (const id of this.sequences.keys()) await this.closeSequence(id)
    this.bus.clear()
  }
}
