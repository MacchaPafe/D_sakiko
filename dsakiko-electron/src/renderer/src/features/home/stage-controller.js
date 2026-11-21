import { StageActor } from './stage-actor.js'
import { Performance } from '../../performance/interface.js'
import { createAudioOutput } from '../../performance/audio-output.js'
import { stageModelKey } from '../../performance/stage-layout.js'
import { isReplayableDialogue, lastDialogueEntry } from '../../../../shared/chat-history.js'

/** @typedef {import('../../../../shared/contracts/presentation.js').ReplayMaterials} ReplayMaterials */
/** @typedef {{text: string, name: string, revealed: number, translation: string|null, canReplayLine: boolean, replayingLine: boolean, lineReplayError: string|null, problem: string|null, loading: boolean, hasModel: boolean, canRetryModel: boolean, shownOutfitKey: string|null, failedOutfitKey: string|null, sides: boolean[]|null}} StageState 本地字幕和模型状态，不替代后端历史。 */
/** 正式舞台的资源与输出边界：共享预览加载器，跟随后端计划，实例不随面板开关重建。 */
export class StageController {
  /** @param {import('../../backend/client.js').BackendClient} client 业务连接。 @param {(state: StageState) => void} publish 展示快照。 */
  constructor(client, publish) {
    Object.assign(this, { client, publish })
    this.epoch = 0
    this.visible = true
    this.actors = []
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
      failedOutfitKey: null,
      sides: null
    }
    this.output = createAudioOutput(client, { onSpeechAudio: (audio) => this.analyse(audio) })
    this.player = new Performance({ client, output: this.output })
    this.actors.push(this.createActor())
    this.stopPlayer = this.player.observe((snapshot) => this.progress(snapshot))
  }
  createActor() {
    const actor = new StageActor(this.client, () => this.publishActors(), this.player)
    actor.refresh = () => this.progress(this.player.getSnapshot())
    return actor
  }

  publishActors() {
    const states = this.actors.map((actor) => actor.state)
    const failure = states.find((actor) => actor.problem)
    this.update({
      ...states[0],
      loading: states.some((item) => item.loading),
      hasModel: states.some((item) => item.hasModel),
      problem: failure?.problem ?? null,
      canRetryModel: Boolean(failure?.canRetryModel),
      actors: states.map((item) => ({ ...item }))
    })
  }

  mount(element) {
    this.element = element
    this.mountActor(this.actors[0])
    this.actors[0].surface.app.ticker.add(() => this.tick())
    this.dragStart = (event) => {
      if (event.button !== 0 || this.dual || !this.actors[0].surface.model || this.state.loading)
        return
      this.drag = {
        x: event.clientX,
        y: event.clientY,
        key: this.actors[0].modelKey,
        layout: this.actors[0].layout()
      }
      element.setPointerCapture(event.pointerId)
    }
    this.dragMove = (event) => {
      if (!this.drag || this.drag.key !== this.actors[0].modelKey) return
      const ratio = Math.min(element.clientWidth / 1120, element.clientHeight / 1024)
      this.actors[0].applyLayout({
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
  mountActor(actor, sharedApp) {
    const element = document.createElement('div')
    element.className = 'stage-actor'
    this.element.appendChild(element)
    actor.mount(element, () => 1120, sharedApp)
  }

  configureStage(dual, chatId) {
    if (this.dual === dual && this.chatId === chatId) return
    if (!dual && !this.dual) {
      this.chatId = chatId
      return
    }
    this.dual = dual
    this.chatId = chatId
    this.element?.classList.toggle('two-actors', dual)
    if (dual && this.actors.length === 1) {
      const actor = this.createActor()
      this.actors.push(actor)
      // 两个 Cubism 模型共用 WebGL 上下文，遮罩和纹理会在同一帧按舞台顺序绘制。
      this.mountActor(actor, this.actors[0].surface.app)
      actor.surface.setVisible(this.visible)
    } else if (!dual && this.actors.length === 2) {
      const actor = this.actors.pop()
      actor.dispose()
      actor.element.remove()
    }
    this.actors.forEach((actor, index) => {
      actor.surface.stageSlot = dual ? (index === 0 ? -1 : 1) : 0
      actor.surface.stageCenter = 0.5
      actor.frame(null, null)
      actor.reset()
      void actor.showModel(null)
      actor.surface?.fit?.()
    })
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
   * @param {boolean} [theatre] 是否使用双人舞台。
   * @returns {Promise<void>} 跟随实时演出，序列就绪前可先显示模型。
   */
  async select(characterId, chatId, sequenceId, draftPresentation, theatre = false) {
    this.configureStage(theatre && Boolean(chatId), chatId)
    const epoch = ++this.epoch
    this.actors.forEach((actor) => actor.clearTimedMotion())
    const selectionKey = JSON.stringify([characterId, chatId, stageModelKey(draftPresentation)])
    if (this.selectionKey !== selectionKey) {
      this.selectionKey = selectionKey
      this.actors.forEach((actor) => actor.reset())
      this.update({
        loading: false,
        problem: null,
        canRetryModel: false,
        failedOutfitKey: null,
        sides: null
      })
    }
    this.history = null
    this.lineReplaySource = null
    this.player.follow(null)
    this.stopReplay()
    this.guideId = null
    this.update({ text: '', name: '', revealed: 0, canReplayLine: false, lineReplayError: null })
    if (sequenceId) {
      this.player.follow(sequenceId)
      return
    }
    try {
      let presentation = null
      // 双人首帧等正式场景，避免短暂展示末句说话人的单人构图。
      if (theatre) {
        if (!chatId) await this.actors[0].showModel(null)
        return
      }
      if (chatId) presentation = await this.client.conversations.getStagePresentation(chatId)
      else if (characterId) {
        presentation =
          draftPresentation ??
          (await this.client.characters.getPresentation(characterId)).presentation
      }
      if (epoch !== this.epoch || this.closed) return
      await this.actors[0].showModel(presentation)
    } catch (error) {
      if (epoch === this.epoch) this.update({ problem: error.message })
    }
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
      () =>
        this.client.conversations.getReplayMaterials(
          chatId,
          this.dual ? { kind: 'script' } : { kind: 'turn', turnId }
        ),
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
    const request = { chatId, kind, finish, getMaterials }
    const controller = this
    function finish(error) {
      if (controller.replayRequest !== request) return
      controller.replayRequest = null
      if (controller.player.mode === 'replay') {
        if (kind === 'line') {
          for (const actor of controller.actors) {
            actor.desiredPlanKey = null
            actor.surface?.adapter?.stopMotions()
          }
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
      this.actors.forEach((actor) => actor.clearTimedMotion())
      this.actors.forEach((actor) => {
        actor.planKey = null
        actor.desiredPlanKey = null
      })
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
    const presentation = snapshot?.chat.meta.settings?.presentation
    if (this.replayRequest && this.orientation !== presentation?.orientation)
      this.player.refreshReplay(this.replayRequest.getMaterials)
    this.orientation = presentation?.orientation
    this.frames = presentation?.frames
    const entry = lastDialogueEntry(snapshot?.chat, Date.now())
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
    if (!this.actors[0]?.surface || this.closed) return
    const progress = snapshot.progress
    const current = progress?.current ?? progress?.transition
    const slot =
      progress?.scene.slots.find((item) => item.id === current?.guide.slotId) ??
      progress?.scene.slots.find((item) => item.id === this.history?.line.speakerId) ??
      progress?.scene.slots[0]
    if (!progress) {
      this.actors.forEach((actor) => actor.clearThinkingMotion())
      this.actors.forEach((actor) => actor.clearTimedMotion())
      for (const actor of this.actors) actor.desiredPlanKey = null
      this.showHistory(null)
      return
    }
    this.slots = progress.scene.slots
    for (const [index, actor] of this.actors.entries()) {
      const target = this.dual ? this.slots[index] : slot
      const speaking = Boolean(current && target?.id === current.guide.slotId)
      const idle = progress.defaultPerformances?.find(
        (entry) => entry.slotId === target?.id
      )?.resolvedPerformance
      actor.frame(
        this.dual ? JSON.stringify([this.chatId, target?.id, target?.outfitKey]) : null,
        this.frames?.[target?.id]?.[target?.outfitKey]
      )
      if (!speaking && actor.speaking) {
        actor.surface.adapter?.setMouthOpen(0)
        actor.surface.adapter?.setSpeechActive(false)
      }
      actor.speaking = speaking
      actor.present(
        target,
        speaking ? current.guide.resolvedPerformance : idle,
        speaking ? current.guideId : null,
        snapshot.mode === 'replay' && this.replayRequest?.kind === 'line' && speaking,
        idle
      )
    }
    const sides = this.slots.map((item) => item.supportsInward)
    if (this.dual && JSON.stringify(sides) !== JSON.stringify(this.state.sides))
      this.update({ sides })
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
  analyse(audio) {
    this.audio = audio
    this.source?.disconnect()
    this.analyser?.disconnect()
    this.source = null
    this.analyser = null
    this.mouth = 0
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
    const snapshot = this.player.getSnapshot()
    for (const actor of this.actors) {
      if (!actor.awaitingModel && (actor.speaking || !this.dual)) {
        actor.surface.adapter?.setSpeechActive(true)
        actor.surface.adapter?.setMouthOpen(this.mouth)
      }
      actor.advanceTimedMotion(snapshot.elapsedMs)
      actor.advanceThinkingMotion()
    }
    const current = snapshot.progress?.current
    if (current?.guideId === this.guideId && this.state.revealed !== snapshot.revealedCharacters)
      this.update({ revealed: snapshot.revealedCharacters })
  }
  setVisible(visible) {
    this.visible = visible
    for (const actor of this.actors) {
      actor.visible = visible
      actor.surface?.setVisible(visible)
      actor.planKey = null
    }
    this.player.setVisible(visible)
    if (!visible) {
      this.actors.forEach((actor) => actor.clearThinkingMotion())
      this.actors.forEach((actor) => actor.clearTimedMotion())
      this.stopReplay()
      this.analyse(null)
    } else {
      this.progress(this.player.getSnapshot())
    }
  }
  moveActor(index, dx, dy, original) {
    const actor = this.actors[index]
    if (!actor?.surface?.model) return
    const ratio = actor.surface.layoutScale || 1
    actor.applyLayout({
      ...original,
      offsetX: original.offsetX + dx / ratio,
      offsetY: original.offsetY + dy / ratio
    })
  }
  async saveFrame(index) {
    const actor = this.actors[index]
    const slot = this.slots?.[index]
    if (!this.dual || !actor?.modelKey || !slot || actor.awaitingModel) return
    const key = stageModelKey(slot.presentation)
    if (!slot.outfitKey || key !== actor.state.shownOutfitKey) return
    await this.client.conversations.patchSettings(this.chatId, {
      presentation: {
        frames: {
          [slot.id]: { [slot.outfitKey]: actor.layout() }
        }
      }
    })
  }
  retryModel(index) {
    const actor = index == null ? this.actors.find((item) => item.failedModel) : this.actors[index]
    actor?.retryModel()
  }
  zoom(factor, index = 0) {
    this.actors[index]?.zoom(factor)
  }
  resetLayout(index = 0) {
    this.actors[index]?.resetLayout()
  }
  dispose() {
    this.closed = true
    this.actors.forEach((actor) => actor.clearTimedMotion())
    this.stopReplay()
    this.epoch += 1
    this.actors
      .slice()
      .reverse()
      .forEach((actor) => actor.dispose())
    for (const actor of this.actors) actor.element?.remove()
    this.element?.removeEventListener('pointerdown', this.dragStart)
    this.element?.removeEventListener('pointermove', this.dragMove)
    this.element?.removeEventListener('pointerup', this.dragEnd)
    this.element?.removeEventListener('lostpointercapture', this.dragEnd)
    this.stopPlayer()
    this.player.close()
    void this.context?.close()
  }
}
