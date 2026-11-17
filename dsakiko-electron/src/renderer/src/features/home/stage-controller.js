import { Live2DPreview } from '../../performance/live2d-preview.js'
import { Performance } from '../../performance/interface.js'
import { createAudioOutput } from '../../performance/audio-output.js'
import { StageLayouts, stageModelKey } from '../../performance/stage-layout.js'
import { isReplayableDialogue, lastDialogueEntry } from '../../../../shared/chat-history.js'

/** @typedef {import('../../../../shared/contracts/presentation.js').ReplayMaterials} ReplayMaterials */
/** @typedef {{text: string, name: string, revealed: number, translation: string|null, canReplayLine: boolean, replayingLine: boolean, lineReplayError: string|null, problem: string|null, loading: boolean, hasModel: boolean, canRetryModel: boolean, shownOutfitKey: string|null, failedOutfitKey: string|null}} StageState 本地字幕和模型状态，不替代后端历史。 */
/** 正式舞台的资源与输出边界：共享预览加载器，跟随后端计划，实例不随面板开关重建。 */
export class StageController {
  /** @param {import('../../backend/client.js').BackendClient} client 业务连接。 @param {(state: StageState) => void} publish 展示快照。 */
  constructor(client, publish) {
    Object.assign(this, { client, publish })
    this.epoch = 0
    this.visible = true
    this.layouts = new StageLayouts()
    this.state = {
      text: '',
      name: '',
      translation: null,
      revealed: 0,
      canReplayLine: false,
      replayingLine: false,
      lineReplayError: null,
      problem: null,
      loading: false,
      hasModel: false,
      canRetryModel: false,
      shownOutfitKey: null,
      failedOutfitKey: null
    }
    this.output = createAudioOutput(client, { onSpeechAudio: (audio) => this.analyse(audio) })
    this.player = new Performance({ client, output: this.output })
    this.stopPlayer = this.player.observe((snapshot) => this.progress(snapshot))
  }
  mount(element) {
    this.surface = new Live2DPreview(element, {
      tracking: false,
      referenceWidth: 1120,
      stage: true
    })
    this.surface.app.ticker.add(() => this.tick())
    this.dragStart = (event) => {
      if (event.button !== 0 || !this.surface.model || this.state.loading) return
      this.drag = { x: event.clientX, y: event.clientY, key: this.modelKey, layout: this.layout() }
      element.setPointerCapture(event.pointerId)
    }
    this.dragMove = (event) => {
      if (!this.drag || this.drag.key !== this.modelKey) return
      const ratio = Math.min(element.clientWidth / 1120, element.clientHeight / 1024)
      this.applyLayout({
        ...this.drag.layout,
        offsetX: this.drag.layout.offsetX + (event.clientX - this.drag.x) / ratio,
        offsetY: this.drag.layout.offsetY + (event.clientY - this.drag.y) / ratio
      })
    }
    this.dragEnd = () => {
      this.drag = null
    }
    element.addEventListener('pointerdown', this.dragStart)
    element.addEventListener('pointermove', this.dragMove)
    element.addEventListener('pointerup', this.dragEnd)
    element.addEventListener('lostpointercapture', this.dragEnd)
    this.element = element
  }
  update(patch) {
    this.state = { ...this.state, ...patch }
    if (!this.closed) this.publish(this.state)
  }
  /**
   * @param {string|null} characterId 当前角色。
   * @param {string|null} chatId 已保存的对话，草稿时为空。
   * @param {string|null} sequenceId 活跃对话的演出计划。
   * @param {import('../../../../shared/contracts/presentation.js').ModelPresentation} [draftPresentation] 空白对话临时选中的服装。
   * @returns {Promise<void>} 跟随实时演出，序列就绪前可先显示模型。
   */
  async select(characterId, chatId, sequenceId, draftPresentation) {
    const epoch = ++this.epoch
    this.clearTimedMotion()
    const selectionKey = JSON.stringify([characterId, chatId, stageModelKey(draftPresentation)])
    if (this.selectionKey !== selectionKey) {
      this.selectionKey = selectionKey
      this.cancelModelLoad()
      this.failedModel = null
      this.suspendModel()
      this.update({ loading: false, problem: null, canRetryModel: false, failedOutfitKey: null })
    }
    this.history = null
    this.lineReplaySource = null
    this.player.follow(null)
    this.stopReplay()
    this.guideId = null
    this.planKey = null
    this.update({ text: '', name: '', revealed: 0, canReplayLine: false, lineReplayError: null })
    if (sequenceId) {
      this.player.follow(sequenceId)
      return
    }
    try {
      let presentation = null
      if (chatId) presentation = await this.client.conversations.getStagePresentation(chatId)
      else if (characterId) {
        presentation =
          draftPresentation ??
          (await this.client.characters.getPresentation(characterId)).presentation
      }
      if (epoch !== this.epoch || this.closed) return
      await this.showModel(presentation)
    } catch (error) {
      if (epoch === this.epoch) this.update({ problem: error.message })
    }
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
    if (this.pendingModel?.key === key) return this.pendingModel.promise
    if (key && this.state.failedOutfitKey === key) return false
    this.cancelModelLoad()
    if (key === this.state.shownOutfitKey && this.surface.model) {
      this.awaitingModel = false
      this.failedModel = null
      if (this.state.loading || this.state.canRetryModel)
        this.update({ loading: false, problem: null, canRetryModel: false, failedOutfitKey: null })
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
        hasModel: false,
        problem: null,
        canRetryModel: false,
        shownOutfitKey: null,
        failedOutfitKey: null
      })
      return false
    }
    this.suspendModel()
    this.update({ loading: true, problem: null, canRetryModel: false, failedOutfitKey: null })
    const layoutKey = asset.id
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
          getLayout: (version) => this.layouts.get(layoutKey, version)
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
          this.update({ loading: false })
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
      if (shown) this.progress(this.player.getSnapshot())
    })
  }

  release(id) {
    if (id) void this.client.assets.releaseAccess(id).catch(() => {})
  }
  /**
   * @param {import('../../../../shared/contracts/presentation.js').AudioMaterial} audio 已保存的单句音频。
   * @param {AbortSignal} signal 记录面板取消回放时撤销音频输出。
   * @param {(error?: Error) => void} onStop 播放结束或失败时恢复按钮。
   * @returns {void} 只使用本地语音音轨，不切换演出进度。
   */
  playHistoryAudio(audio, signal, onStop) {
    const { volume } = this.player.getPlaybackOptions()
    void this.output.playPreview(audio, { volume, signal, onStop })
  }
  /**
   * @param {string} chatId 来源对话。 @param {string} turnId 要播放的轮次。
   * @param {(error?: Error) => void} onStop 结束、取消或加载失败。
   * @returns {Promise<void>} 仅请求已保存的演出材料，计时由当前窗口负责。
   */
  replayTurn(chatId, turnId, onStop) {
    return this.replay(
      chatId,
      'turn',
      () => this.client.conversations.getReplayMaterials(chatId, { kind: 'turn', turnId }),
      onStop
    )
  }
  /**
   * @param {string|null} chatId 当前对话。
   * @returns {Promise<void>} 从头重播舞台上的这一句，或停止当前单句／整轮回放。
   */
  async replayLine(chatId) {
    if (!chatId) return
    if (this.replayRequest) {
      this.stopReplay(chatId)
      return
    }
    const source = this.lineReplaySource
    if (!source) return
    // 正在播放的 Guide 已有解析结果；静态字幕只有历史定位，需要读取保存的材料。
    await this.replay(
      chatId,
      'line',
      () =>
        source.materials ??
        this.client.conversations.getReplayMaterials(chatId, { kind: 'line', line: source.line }),
      (error) => this.update({ replayingLine: false, lineReplayError: error?.message ?? null })
    )
  }
  /**
   * @param {string} chatId 来源对话。
   * @param {'line'|'turn'} kind 回放范围。
   * @param {() => ReplayMaterials | Promise<ReplayMaterials>} getMaterials 可直接使用或异步读取的演出材料。
   * @param {(error?: Error) => void} onStop 结束、取消或加载失败。
   * @returns {Promise<void>} 当前窗口独占回放。
   */
  async replay(chatId, kind, getMaterials, onStop) {
    this.stopReplay()
    const request = { chatId, kind, finish }
    const controller = this
    function finish(error) {
      if (controller.replayRequest !== request) return
      controller.replayRequest = null
      if (controller.player.mode === 'replay') {
        if (kind === 'line') {
          controller.desiredPlanKey = null
          controller.surface?.adapter?.stopMotions()
        }
        controller.player.resumeLive()
      }
      onStop(error)
    }
    this.replayRequest = request
    if (kind === 'line') this.update({ replayingLine: true, lineReplayError: null })
    try {
      const materials = await getMaterials()
      if (this.replayRequest !== request) return
      if (!this.visible) return finish()
      if (!materials.guides.length) throw new Error('没有可回放的台词。')
      this.clearTimedMotion()
      this.planKey = null
      this.desiredPlanKey = null
      this.player.replay(materials, { onEnd: finish })
    } catch (error) {
      finish(error)
    }
  }
  /** @param {string} [chatId] 指定对话时，迟到的发送确认只停止该对话的回放。 @returns {void} 停止加载或播放，恢复实时舞台。 */
  stopReplay(chatId) {
    const request = this.replayRequest
    if (request && (!chatId || chatId === request.chatId)) request.finish()
  }
  /** @param {object|null} snapshot 当前聊天的正式快照。 @returns {void} 恢复静态末句，不创建回放、不触发历史音频。 */
  setHistory(snapshot) {
    const entry = lastDialogueEntry(snapshot?.chat)
    this.history = entry
      ? {
          ...entry,
          name:
            snapshot.chat.meta.extensions?.characterNames?.[entry.line.speakerId] ??
            entry.line.speakerId
        }
      : null
    this.historyAllowed = !snapshot?.activeTurnId && (!snapshot || snapshot.activity === 'idle')
    this.progress(this.player.getSnapshot())
  }
  showHistory(scene) {
    if (!this.historyAllowed) return
    const line = this.history?.line
    const slot = scene?.slots.find((item) => item.id === line?.speakerId)
    this.guideId = null
    this.lineReplaySource = line && isReplayableDialogue(line) ? { line: this.history.ref } : null
    this.update({
      text: line?.text ?? '',
      translation: line?.translation ?? null,
      name: slot?.displayName ?? this.history?.name ?? '',
      revealed: Array.from(line?.text ?? '').length,
      canReplayLine: Boolean(this.lineReplaySource)
    })
  }
  progress(snapshot) {
    if (!this.surface || this.closed) return
    const progress = snapshot.progress
    const current = progress?.current ?? progress?.transition
    const slot =
      progress?.scene.slots.find((item) => item.id === current?.guide.slotId) ??
      progress?.scene.slots.find((item) => item.id === this.history?.line.speakerId) ??
      progress?.scene.slots[0]
    if (!progress) {
      this.clearTimedMotion()
      this.desiredPlanKey = null
      this.showHistory(null)
      return
    }
    const epoch = this.epoch
    const plan =
      current?.guide.resolvedPerformance ??
      progress.defaultPerformances?.find((item) => item.slotId === slot?.id)?.resolvedPerformance
    const planKey = JSON.stringify([current?.guideId, plan, slot?.presentation])
    if (this.timedMotion?.key !== planKey) this.clearTimedMotion()
    const restartMotion =
      snapshot.mode === 'replay' && this.replayRequest?.kind === 'line' && Boolean(progress.current)
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
      } else void this.applyPlan(plan, epoch, planKey, restartMotion)
    })
    if (current?.guide.text) {
      const lineReplayError = this.guideId === current.guideId ? this.state.lineReplayError : null
      this.guideId = current.guideId
      this.lineReplaySource = progress.current
        ? { materials: { scene: progress.scene, guides: [current.guide] } }
        : null
      this.update({
        text: current.guide.text,
        translation: current.guide.translation,
        name: slot?.displayName ?? '',
        revealed: snapshot.revealedCharacters,
        canReplayLine: Boolean(this.lineReplaySource),
        lineReplayError
      })
    } else if (this.historyAllowed) this.showHistory(progress.scene)
    else if (this.state.text) this.update({ revealed: Array.from(this.state.text).length })
    if (snapshot.problem) this.update({ problem: snapshot.problem.message })
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

  async applyPlan(plan, epoch, planKey, restartMotion, isCueCurrent = () => true) {
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
          if (restartMotion) adapter.stopMotions()
          await adapter.startMotion(group, index, 3, 150, true, current)
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
  analyse(audio) {
    this.audio = audio
    this.source?.disconnect()
    this.analyser?.disconnect()
    this.source = null
    this.analyser = null
    this.mouth = 0
    if (!this.awaitingModel) {
      this.surface?.adapter?.setSpeechActive(true)
      this.surface?.adapter?.setMouthOpen(0)
    }
    if (!audio) return
    try {
      this.context ??= new AudioContext()
      void this.context.resume().catch(() => {})
      this.source = this.context.createMediaElementSource(audio)
      this.analyser = this.context.createAnalyser()
      this.analyser.fftSize = 1024
      this.samples = new Float32Array(this.analyser.fftSize)
      this.source.connect(this.analyser)
      this.analyser.connect(this.context.destination)
    } catch {
      /* 口型分析不可用时仍保留普通音频播放。 */
    }
  }
  tick() {
    if (!this.visible) return
    let target = 0
    if (this.analyser && this.audio && !this.audio.paused && !this.audio.ended) {
      this.analyser.getFloatTimeDomainData(this.samples)
      const sum = this.samples.reduce((value, sample) => value + sample * sample, 0)
      const rms = Math.sqrt(sum / this.samples.length)
      if (rms > 0.008) target = Math.min(1, rms * 1.4)
    }
    this.mouth = (this.mouth ?? 0) + (target - (this.mouth ?? 0)) * 0.35
    if (!this.awaitingModel) {
      this.surface.adapter?.setSpeechActive(true)
      this.surface.adapter?.setMouthOpen(this.mouth)
    }
    const snapshot = this.player.getSnapshot()
    this.advanceTimedMotion(snapshot.elapsedMs)
    const current = snapshot.progress?.current
    if (current?.guideId === this.guideId && this.state.revealed !== snapshot.revealedCharacters)
      this.update({ revealed: snapshot.revealedCharacters })
  }
  setVisible(visible) {
    this.visible = visible
    this.surface?.setVisible(visible)
    this.player.setVisible(visible)
    if (!visible) {
      this.clearTimedMotion()
      this.stopReplay()
      this.analyse(null)
    } else {
      this.planKey = null
      this.progress(this.player.getSnapshot())
    }
  }
  layout() {
    return this.layouts.get(this.modelKey, this.surface?.adapter?.version)
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
    this.clearTimedMotion()
    this.stopReplay()
    this.epoch += 1
    this.cancelModelLoad()
    this.element?.removeEventListener('pointerdown', this.dragStart)
    this.element?.removeEventListener('pointermove', this.dragMove)
    this.element?.removeEventListener('pointerup', this.dragEnd)
    this.element?.removeEventListener('lostpointercapture', this.dragEnd)
    this.stopPlayer()
    this.player.close()
    this.surface?.destroy()
    this.release(this.lease)
    void this.context?.close()
  }
}
