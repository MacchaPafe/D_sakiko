import { Live2DPreview } from '../../performance/live2d-preview.js'
import { defaultStageLayout, StageLayouts, stageModelKey } from '../../performance/stage-layout.js'

/** 一个演员独立持有模型、资源租约和演出计划。播放时钟和音频由整个舞台共享。 */
export class StageActor {
  constructor(client, changed, playback) {
    this.client = client
    this.changed = changed
    this.player = playback
    this.layouts = new StageLayouts()
    this.epoch = 0
    this.visible = true
    this.state = {
      shownOutfitKey: null,
      failedOutfitKey: null,
      loading: false,
      hasModel: false,
      modelSettled: false
    }
  }

  mount(element, referenceWidth, sharedApp) {
    this.element = element
    this.surface = new Live2DPreview(element, {
      tracking: false,
      stage: true,
      sharedApp,
      getReferenceWidth: referenceWidth
    })
  }

  update(patch) {
    Object.assign(this.state, patch)
    if (!this.closed) this.changed()
  }

  reset() {
    this.epoch++
    this.clearThinkingMotion()
    this.clearTimedMotion()
    this.cancelModelLoad()
    this.suspendModel()
    this.planKey = null
    this.desiredPlanKey = null
    this.direction = null
    this.failedModel = null
    this.update({
      loading: false,
      modelSettled: false,
      problem: null,
      failedOutfitKey: null,
      canRetryModel: false
    })
  }
  suspendModel() {
    this.awaitingModel = true
    const adapter = this.surface?.adapter
    adapter?.setMouthOpen(0)
    adapter?.setSpeechActive(false)
  }

  cancelModelLoad() {
    this.pendingModel?.abort.abort()
    this.pendingModel = null
  }

  async showModel(presentation) {
    const asset = presentation?.model ?? null
    const key = stageModelKey(presentation)
    if (!key && !this.surface?.model && !this.pendingModel && !this.state.failedOutfitKey) {
      if (!this.state.modelSettled) this.update({ modelSettled: true })
      return false
    }
    if (this.pendingModel?.key === key) return this.pendingModel.promise
    if (key && this.state.failedOutfitKey === key) return false
    this.cancelModelLoad()
    if (key === this.state.shownOutfitKey && this.surface.model) {
      this.awaitingModel = false
      this.failedModel = null
      if (!this.state.modelSettled || this.state.loading || this.state.canRetryModel)
        this.update({
          loading: false,
          modelSettled: true,
          problem: null,
          canRetryModel: false,
          failedOutfitKey: null
        })
      return true
    }
    this.failedModel = null
    if (!asset) {
      if (this.surface.model) this.surface.clear()
      this.release(this.lease)
      this.lease = null
      this.modelKey = null
      this.update({
        loading: false,
        modelSettled: true,
        hasModel: false,
        problem: null,
        canRetryModel: false,
        shownOutfitKey: null,
        failedOutfitKey: null
      })
      return false
    }
    this.suspendModel()
    this.update({
      loading: true,
      modelSettled: false,
      problem: null,
      canRetryModel: false,
      failedOutfitKey: null
    })
    const layoutKey = this.layoutScope ? JSON.stringify([this.layoutScope, key]) : asset.id
    const pending = { key, abort: new AbortController() }
    const current = () => !this.closed && this.pendingModel === pending
    this.pendingModel = pending
    pending.promise = (async () => {
      let lease
      try {
        const access = await this.client.assets.access(asset)
        lease = access.leaseId
        if (!current()) return false
        const replaced = await this.surface.show(access.url, current, pending.abort.signal, {
          cacheKey: JSON.stringify(['asset', asset.id]),
          bounds: presentation.bounds,
          stageDirection: this.layoutScope ? this.direction : null,
          getLayout: (version) =>
            this.layoutScope
              ? (this.savedFrame ?? defaultStageLayout(version))
              : this.layouts.get(layoutKey, version)
        })
        if (!replaced || !current()) return false
        this.release(this.lease)
        this.lease = lease
        lease = null
        this.modelKey = layoutKey
        this.awaitingModel = false
        this.update({ hasModel: true, shownOutfitKey: key })
        return true
      } catch (error) {
        if (current()) {
          this.failedModel = presentation
          this.awaitingModel = !this.surface.model
          this.update({
            problem: '角色模型加载失败：' + error.message,
            canRetryModel: true,
            failedOutfitKey: key
          })
        }
        return false
      } finally {
        this.release(lease)
        if (current()) {
          this.pendingModel = null
          this.update({ loading: false, modelSettled: true })
        }
      }
    })()
    return pending.promise
  }

  retryModel() {
    const failed = this.failedModel
    if (!failed) return
    this.failedModel = null
    this.update({ failedOutfitKey: null })
    this.planKey = null
    void this.showModel(failed).then((shown) => {
      if (shown) this.refresh?.()
    })
  }

  release(id) {
    if (id) void this.client.assets.releaseAccess(id).catch(() => {})
  }

  frame(scope, value) {
    if (scope === this.layoutScope && JSON.stringify(this.savedFrame) === JSON.stringify(value))
      return
    this.layoutScope = scope
    this.savedFrame = value
    if (scope && this.surface?.model) {
      this.surface.stageLayout = value ?? defaultStageLayout(this.surface.adapter.version)
      this.surface.fit()
    }
  }

  present(slot, plan, guideId, restartMotion, idle) {
    const direction = slot?.layout?.direction ?? 'C'
    const directionChanged = this.direction != null && this.direction !== direction
    this.direction = direction
    // 朝向切换或剧场空闲站姿过渡使用更柔和的交叉淡入，常规台词动作保持紧凑响应。
    const fadeInMs = directionChanged || (this.layoutScope && !guideId) ? 800 : 150
    const epoch = this.epoch
    const planKey = JSON.stringify([guideId, plan, slot?.presentation, direction])
    if (this.thinkingMotion && this.thinkingMotion.key !== planKey)
      this.clearThinkingMotion(!plan?.motion)
    if (this.timedMotion?.key !== planKey) this.clearTimedMotion()
    this.desiredPlanKey = planKey
    void this.showModel(slot?.presentation ?? null).then((shown) => {
      if (
        !shown ||
        epoch !== this.epoch ||
        this.closed ||
        this.desiredPlanKey !== planKey ||
        this.planKey === planKey ||
        !this.visible
      )
        return
      this.planKey = planKey
      if (this.layoutScope) this.surface.setStageIdle?.(idle?.motion, direction)
      else if (plan && Object.hasOwn(plan, 'idleMotion'))
        this.surface.setSingleIdle(plan.idleMotion)
      if (plan?.thinkingMotions?.length) {
        this.thinkingMotion = { plan, epoch, key: planKey, ready: true, lastStart: -Infinity }
        this.advanceThinkingMotion()
        return
      }
      if (plan?.motionCues) {
        this.timedMotion = {
          plan,
          epoch,
          key: planKey,
          adapter: this.surface.adapter,
          index: -1,
          cue: null,
          applied: false
        }
        this.advanceTimedMotion(this.player.getSnapshot().elapsedMs)
      } else void this.applyPlan(plan, epoch, planKey, restartMotion, undefined, fadeInMs)
    })
  }
  clearThinkingMotion(stop = true) {
    if (!this.thinkingMotion) return
    this.thinkingMotion.unsubscribe?.()
    if (stop && this.thinkingMotion.playing) this.surface.adapter?.stopMotions()
    this.thinkingMotion = null
    this.planKey = null
  }

  advanceThinkingMotion() {
    const cycle = this.thinkingMotion
    if (!cycle?.ready || performance.now() - cycle.lastStart < 15000) return
    cycle.ready = false
    const pool = cycle.plan.thinkingMotions
    const motion = pool[Math.floor(Math.random() * pool.length)]
    const current = () => this.thinkingMotion === cycle
    const started = (adapter) => {
      cycle.lastStart = performance.now()
      cycle.playing = true
      // SDK 在最后一帧结算后发出 motionFinish；动作时长或窗口暂停都不会提前放行重播。
      cycle.unsubscribe = adapter.onceMotionFinish(() => {
        cycle.playing = false
        cycle.ready = true
      })
    }
    void this.applyPlan(
      { ...cycle.plan, motion },
      cycle.epoch,
      cycle.key,
      false,
      current,
      150,
      started
    )
  }

  clearTimedMotion() {
    const timed = this.timedMotion
    this.timedMotion = null
    if (timed?.cue) timed.adapter?.stopMotions()
  }

  advanceTimedMotion(elapsedMs) {
    const timed = this.timedMotion
    if (!timed) return
    const cues = timed.plan.motionCues
    const index = cues.findLastIndex((cue) => cue.atMs <= elapsedMs)
    const previous = timed.cue
    if (index > timed.index) {
      timed.index = index
      const candidate = cues[index]
      timed.cue = elapsedMs < candidate.atMs + candidate.durationMs ? candidate : null
    } else if (previous && elapsedMs >= previous.atMs + previous.durationMs) {
      timed.cue = null
    } else if (timed.applied) return
    const cue = timed.cue
    if (previous && !cue) timed.adapter?.stopMotions()
    timed.applied = true
    // 恢复窗口时只消费当前有效区间；异步加载完的旧动作也不能越过计划终点启动。
    const current = () =>
      this.timedMotion === timed &&
      timed.cue === cue &&
      (!cue || this.player.getSnapshot().elapsedMs < cue.atMs + cue.durationMs)
    void this.applyPlan(
      { ...timed.plan, motion: cue?.motion ?? null },
      timed.epoch,
      timed.key,
      Boolean(cue),
      current
    )
  }

  async applyPlan(
    plan,
    epoch,
    planKey,
    restartMotion,
    isCueCurrent = () => true,
    fadeInMs = 150,
    onStarted
  ) {
    const adapter = this.surface.adapter
    if (!adapter || !plan) return
    const current = () =>
      !this.closed &&
      this.visible &&
      epoch === this.epoch &&
      this.desiredPlanKey === planKey &&
      !this.awaitingModel &&
      adapter === this.surface.adapter &&
      isCueCurrent()
    if (!current()) return
    const motion = plan.motion
    try {
      if (motion) {
        const file = motion.entry?.File ?? motion.entry?.file ?? motion.member
        for (const [group, entries] of Object.entries(this.surface.motionGroups ?? {})) {
          const index = entries.findIndex((entry) => (entry.File ?? entry.file) === file)
          if (index < 0) continue
          // 同一个动作还在播放时，SDK 会拒绝重新申请该组和编号。
          if (restartMotion || (onStarted && adapter.isPlayingMotion(group, index)))
            adapter.stopMotions()
          const started = await adapter.startMotion(group, index, 3, fadeInMs, true, current)
          if (started && current()) onStarted?.(adapter)
          break
        }
      }
    } catch (error) {
      if (current()) {
        adapter.stopMotions()
        this.update({ problem: error.message })
      }
    }
    if (!current()) return
    try {
      const applied = await adapter.setExpression(plan.expression?.id ?? null, current)
      if (!applied && plan.expression && current()) adapter.resetExpression()
    } catch (error) {
      if (current()) {
        adapter.resetExpression()
        this.update({ problem: error.message })
      }
    }
  }
  layout() {
    return (
      this.surface?.stageLayout ?? this.layouts.get(this.modelKey, this.surface?.adapter?.version)
    )
  }
  applyLayout(value) {
    if (!this.surface?.model || !this.modelKey) return
    this.surface.stageLayout = this.layouts.set(this.modelKey, value)
    this.surface.fit()
  }
  zoom(factor) {
    const layout = this.layout()
    this.applyLayout({ ...layout, scale: layout.scale * factor })
  }
  resetLayout() {
    this.applyLayout(this.layouts.reset(this.modelKey, this.surface?.adapter?.version))
  }

  dispose() {
    this.closed = true
    this.reset()
    this.surface?.destroy()
    this.release(this.lease)
  }
}
