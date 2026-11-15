import { Live2DPreview } from '../../performance/live2d-preview.js'
import { Performance } from '../../performance/interface.js'
import { createAudioOutput } from '../../performance/audio-output.js'
import { StageLayouts } from '../../performance/stage-layout.js'
import { lastDialogue } from '../../../../shared/chat-history.js'

/** @typedef {{text: string, name: string, revealed: number, translation: string|null, problem: string|null, loading: boolean}} StageState 本地字幕和模型状态，不替代后端历史。 */
/** 正式舞台的资源与输出边界：共享预览加载器，跟随后端计划，实例不随面板开关重建。 */
export class StageController {
  /** @param {import('../../backend/client.js').BackendClient} client 业务连接。 @param {(state: StageState) => void} publish 展示快照。 */
  constructor(client, publish) {
    Object.assign(this, { client, publish })
    this.epoch = 0
    this.modelEpoch = 0
    this.visible = true
    this.layouts = new StageLayouts()
    this.state = {
      text: '',
      name: '',
      translation: null,
      revealed: 0,
      problem: null,
      loading: false
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
  /** @param {string|null} characterId 正式角色身份。 @param {string|null} sequenceId 活跃聊天的演出计划。 @returns {Promise<void>} 切换不影响后台生成。 */
  async select(characterId, sequenceId) {
    const epoch = ++this.epoch
    this.history = null
    this.player.follow(null)
    this.stopReplay()
    this.guideId = null
    this.planKey = null
    this.update({ text: '', name: '', revealed: 0, problem: null })
    if (sequenceId) {
      this.player.follow(sequenceId)
      return
    }
    try {
      if (!characterId) {
        await this.showModel(null)
        return
      }
      const role = await this.client.characters.resolveCapabilities(characterId, {})
      if (epoch !== this.epoch || this.closed) return
      await this.showModel(role.presentation?.model ?? null)
    } catch (error) {
      if (epoch === this.epoch) this.update({ problem: error.message })
    }
  }
  async showModel(asset) {
    const key = JSON.stringify(asset)
    if (key === this.modelKey) return this.loading
    this.modelKey = key
    const epoch = ++this.modelEpoch
    this.abort?.abort()
    this.abort = new AbortController()
    this.surface?.clear()
    this.release(this.lease)
    this.lease = null
    this.update({ loading: Boolean(asset), problem: null })
    if (!asset) return
    const current = () => !this.closed && epoch === this.modelEpoch
    this.loading = (async () => {
      try {
        const access = await this.client.assets.access(asset)
        if (!current()) {
          this.release(access.leaseId)
          return
        }
        this.lease = access.leaseId
        await this.surface.show(access.url, current, this.abort.signal)
        if (current()) this.applyLayout(this.layout())
      } catch (error) {
        if (current()) {
          this.modelKey = null
          this.update({ problem: '角色模型加载失败：' + error.message })
        }
      } finally {
        if (current()) this.update({ loading: false })
      }
    })()
    return this.loading
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
  async replayTurn(chatId, turnId, onStop) {
    this.stopReplay()
    const request = { chatId, finish }
    const controller = this
    function finish(error) {
      if (controller.replayRequest !== request) return
      controller.replayRequest = null
      if (controller.player.mode === 'replay') controller.player.resumeLive()
      onStop(error)
    }
    this.replayRequest = request
    try {
      const materials = await this.client.conversations.getReplayMaterials(chatId, {
        kind: 'turn',
        turnId
      })
      if (this.replayRequest !== request) return
      if (!this.visible) return finish()
      if (!materials.guides.length) throw new Error('本轮暂无可回放的台词。')
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
    const line = lastDialogue(snapshot?.chat)
    this.history = line
      ? {
          ...line,
          name: snapshot.chat.meta.extensions?.characterNames?.[line.speakerId] ?? line.speakerId
        }
      : null
    this.historyAllowed = !snapshot?.activeTurnId && (!snapshot || snapshot.activity === 'idle')
    this.progress(this.player.getSnapshot())
  }
  showHistory(scene) {
    if (!this.historyAllowed) return
    const line = this.history
    const slot = scene?.slots.find((item) => item.id === line?.speakerId)
    this.guideId = null
    this.update({
      text: line?.text ?? '',
      translation: line?.translation ?? null,
      name: slot?.displayName ?? line?.name ?? '',
      revealed: Array.from(line?.text ?? '').length
    })
  }
  progress(snapshot) {
    if (!this.surface || this.closed) return
    const progress = snapshot.progress
    const current = progress?.current ?? progress?.transition
    const slot =
      progress?.scene.slots.find((item) => item.id === current?.guide.slotId) ??
      progress?.scene.slots.find((item) => item.id === this.history?.speakerId) ??
      progress?.scene.slots[0]
    if (!progress) {
      this.showHistory(null)
      return
    }
    const epoch = this.epoch
    const plan =
      current?.guide.resolvedPerformance ??
      progress.defaultPerformances?.find((item) => item.slotId === slot?.id)?.resolvedPerformance
    const planKey = JSON.stringify([current?.guideId, plan, slot?.presentation])
    this.desiredPlanKey = planKey
    void this.showModel(slot?.presentation?.model ?? null).then(() => {
      if (
        epoch !== this.epoch ||
        this.closed ||
        this.desiredPlanKey !== planKey ||
        this.planKey === planKey ||
        !this.visible
      )
        return
      this.planKey = planKey
      void this.applyPlan(plan, epoch, planKey).catch((error) => {
        if (epoch === this.epoch && this.desiredPlanKey === planKey)
          this.update({ problem: error.message })
      })
    })
    if (current?.guide.text) {
      this.guideId = current.guideId
      this.update({
        text: current.guide.text,
        translation: current.guide.translation,
        name: slot?.displayName ?? '',
        revealed: snapshot.revealedCharacters
      })
    } else if (this.historyAllowed) this.showHistory(progress.scene)
    else if (this.state.text) this.update({ revealed: Array.from(this.state.text).length })
    if (snapshot.problem) this.update({ problem: snapshot.problem.message })
  }
  async applyPlan(plan, epoch, planKey) {
    const adapter = this.surface.adapter
    if (!adapter || !plan) return
    const current = () =>
      !this.closed &&
      this.visible &&
      epoch === this.epoch &&
      this.desiredPlanKey === planKey &&
      adapter === this.surface.adapter
    const motion = plan.motion
    if (motion) {
      for (const [group, entries] of Object.entries(this.surface.motionGroups ?? {})) {
        const index = entries.findIndex((entry) => (entry.File ?? entry.file) === motion.member)
        if (index < 0) continue
        await adapter.startMotion(group, index, 3, 150, true, current)
        break
      }
    }
    if (current()) await adapter.setExpression(plan.expression?.id ?? null, current)
  }
  analyse(audio) {
    this.audio = audio
    this.source?.disconnect()
    this.analyser?.disconnect()
    this.source = null
    this.analyser = null
    this.mouth = 0
    this.surface?.adapter?.setSpeechActive(true)
    this.surface?.adapter?.setMouthOpen(0)
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
    this.surface.adapter?.setSpeechActive(true)
    this.surface.adapter?.setMouthOpen(this.mouth)
    const snapshot = this.player.getSnapshot()
    const current = snapshot.progress?.current
    if (current?.guideId === this.guideId && this.state.revealed !== snapshot.revealedCharacters)
      this.update({ revealed: snapshot.revealedCharacters })
  }
  setVisible(visible) {
    this.visible = visible
    this.surface?.setVisible(visible)
    this.player.setVisible(visible)
    if (!visible) {
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
    this.stopReplay()
    this.epoch += 1
    this.modelEpoch += 1
    this.abort?.abort()
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
