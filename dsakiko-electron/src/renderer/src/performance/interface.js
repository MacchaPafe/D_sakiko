import { dialogueMarkdown } from '../../../shared/dialogue-markdown.js'
/** @typedef {import('./playback-contracts.js').PlaybackOptions} PlaybackOptions */
/** @typedef {import('../../../shared/contracts/presentation.js').SequenceSnapshot} SequenceSnapshot */
/** @typedef {import('../../../shared/contracts/presentation.js').GuideProgress} GuideProgress */
/** @typedef {{offsetMs: number, volume: number, recurring: boolean, silent?: boolean, getOffsetMs?: () => number}} PlayOptions 本次播放偏移与本地音量。 */
/**
 * @typedef {object} PlaybackOutput 音频/模型输出由宿主注入，可只实现支持的能力。
 * @property {(guide: import('../../../shared/contracts/presentation.js').Guide, options: PlayOptions) => void | Promise<void>} [play] 从给定偏移播放，不回报后端进度。
 * @property {() => void} [stop] 停止当前普通播放。
 * @property {(visible: boolean) => void} [setVisible] 隐藏时暂停本客户端全部音轨。
 * @property {(options: PlaybackOptions) => void} [setVolume] 更新本地总音量。
 * @property {(settings: import('../../../shared/contracts/presentation.js').BgmSettings, options: {volume: number}) => void | Promise<void>} [setBgm] 独立背景音轨。
 * @property {(volume: number) => void} [setBgmVolume] 已乘总音量的背景音量。
 * @property {() => void} [close] 释放全部输出资源。
 */
/** @typedef {Pick<GuideProgress, 'guideId' | 'guide' | 'durationMs' | 'elapsedMs' | 'recurring'>} LocalGuideProgress 本地回放无需伪造后端时标和运行身份。 */
/** @typedef {{scene: SequenceSnapshot['scene'], current: LocalGuideProgress | null, transition: LocalGuideProgress | null}} LocalProgress 实时或回放的共同展示材料。 */
/** @typedef {{progress: LocalProgress, receivedAt: number}} PlaybackAnchor 记录收到快照时的本地单调时标，不要求两台机器时钟同步。 */
/**
 * @typedef {object} PlaybackSnapshot
 * @property {'live' | 'replay'} mode 本客户端播放模式。
 * @property {LocalProgress | null} progress 实时快照或本地回放材料。
 * @property {number} elapsedMs 按本地单调时钟推算的进度，受计划时长限制。
 * @property {number} revealedCharacters 字幕应显示的 Unicode 字符数。
 * @property {PlaybackOptions} playback 本地音量偏好。
 * @property {{code: string, message: string} | null} problem 仅影响本客户端的播放问题。
 * @property {boolean} liveAvailable 回放期间是否已有可恢复的实时快照。
 */
const PREFERENCES_KEY = 'dsakiko.playback.v1'

/** 缺省、损坏或越界偏好统一恢复 100%，不继承其他客户端的音量。 */
function volume(value) {
  return Number.isFinite(value) && value >= 0 && value <= 1 ? value : 1
}
/** 本地存储损坏不阻止播放器初始化，也不向后端请求音量。 */
function preferences(storage) {
  try {
    const saved = JSON.parse(storage?.getItem(PREFERENCES_KEY) ?? 'null')
    return { volume: volume(saved?.volume), bgmVolume: volume(saved?.bgmVolume) }
  } catch {
    return { volume: 1, bgmVolume: 1 }
  }
}

/**
 * 客户端从动播放控制器：根据后端锚点在本地计算字幕，独立回放不会写后端进度。
 * 输出适配器可由未来 React 舞台、桌宠或纯音频宿主注入；本文件不包含页面设计。
 */
export class Performance {
  /**
   * 绑定客户端订阅和本地输出，实例不拥有后端 sequence。
   * @param {object} [options] 本客户端能力。
   * @param {import('../backend/client.js').BackendClient} options.client 业务传输封装。
   * @param {Storage} [options.storage] 本地偏好存储，默认 localStorage。
   * @param {EventTarget} [options.events] 接收同源页面偏好变更。
   * @param {() => number} [options.now] 本地毫秒单调时钟。
   * @param {PlaybackOutput | null} [options.output] 无 UI 的音频或模型适配器。
   */
  constructor({
    client,
    storage = globalThis.localStorage,
    events = globalThis,
    now = () => performance.now(),
    output = null
  } = {}) {
    Object.assign(this, { client, storage, events, now, output })
    this.options = preferences(storage)
    this.listeners = new Set()
    this.mode = 'live'
    /** @type {PlaybackAnchor | null} 回放中也持续更新的实时位置。 */
    this.latest = null
    /** @type {PlaybackAnchor | null} 当前展示所用锚点，可与 latest 独立。 */
    this.anchor = null
    this.stopObserving = null
    this.replayTimer = null
    this.replayEnded = null
    this.closed = false
    this.playingId = null
    this.playEpoch = 0
    this.visible = true
    this.storageListener = (event) => {
      if (event.key === PREFERENCES_KEY || event.key === null) {
        this.options = preferences(this.storage)
        this.applyVolume()
        this.publish()
      }
    }
    events?.addEventListener?.('storage', this.storageListener)
    this.stopSettings = client?.settings?.observe?.((settings) => this.setBgm(settings.bgm))
  }
  /** BGM 相对音量叠乘总音量，不改共享曲目设置。 */
  applyVolume() {
    this.output?.setVolume?.(this.options)
    this.output?.setBgmVolume?.(this.options.volume * this.options.bgmVolume)
  }
  /**
   * 应用共享 BGM 曲目，实际音量只取本地偏好。
   * @param {import('../../../shared/contracts/presentation.js').BgmSettings} settings 曲目、开关和循环偏好。
   * @returns {void} 输出失败仅发布本地问题。
   */
  setBgm(settings) {
    this.bgm = structuredClone(settings)
    Promise.resolve(
      this.output?.setBgm?.(settings, { volume: this.options.volume * this.options.bgmVolume })
    ).catch((error) => {
      this.localProblem = { code: 'bgm_failed', message: error.message }
      this.publish()
    })
  }
  /**
   * 读取当前客户端的播放偏好。
   * @returns {PlaybackOptions} 总音量与 BGM 相对音量，缺省均为 100%。
   */
  getPlaybackOptions() {
    return { ...this.options }
  }
  /**
   * 持久保存本地音量并更新当前输出。
   * @param {PlaybackOptions} options 当前设备的完整音量选择。
   * @returns {void} 不发送后端请求。
   */
  setPlaybackOptions(options) {
    this.options = { volume: volume(options.volume), bgmVolume: volume(options.bgmVolume) }
    this.storage?.setItem(PREFERENCES_KEY, JSON.stringify(this.options))
    this.applyVolume()
    this.publish()
  }
  /**
   * 订阅当前客户端要展示的后端计划。
   * @param {string} sequenceId 要跟随的后端计划。
   * @returns {void} 切换订阅不影响其他客户端。
   */
  follow(sequenceId) {
    this.stopObserving?.()
    this.sequenceId = sequenceId
    this.latest = null
    this.resumeLive()
    if (!sequenceId) return
    this.stopObserving = this.client.performance.observe(sequenceId, (update) => {
      if (this.closed || this.sequenceId !== sequenceId) return
      let progress = update.snapshot ?? update
      if (
        this.suppressScript &&
        progress.scene?.slots?.some((slot) => slot.supportsInward !== undefined)
      )
        progress = { ...progress, current: null, transition: null }
      this.latest = { progress, receivedAt: this.now() }
      if (this.mode === 'live') this.adopt(this.latest)
      else this.publish()
    })
  }
  /** 相同 guide 的新锚点只校准字幕，不重复启动音频；其他客户端的播放状态不参与判断。 */
  adopt(latest) {
    this.anchor = latest
    if (!this.visible) {
      this.publish()
      return
    }
    const current = latest.progress.current ?? latest.progress.transition
    const id = current?.guideId ?? null
    if (this.playingId !== id) {
      this.playingId = id
      this.playEpoch += 1
      const epoch = this.playEpoch
      this.output?.stop?.()
      const offsetMs = this.getSnapshot().elapsedMs
      if (current && (!current.guide.audio || offsetMs < current.guide.audio.durationMs))
        Promise.resolve(
          this.output?.play?.(current.guide, {
            offsetMs,
            // 资源加载本身可能跨过语音终点，播放前按同一个句内时钟重新取偏移。
            getOffsetMs: () => this.getSnapshot().elapsedMs,
            volume: this.options.volume,
            recurring: current.recurring
          })
        ).catch((error) => {
          if (epoch !== this.playEpoch) return
          this.localProblem = { code: 'playback_failed', message: error.message }
          this.publish()
        })
    }
    this.publish()
  }
  /** @param {boolean} visible 窗口可见状态。 @returns {void} 隐藏时保持最新锚点，恢复从当前进度播放。 */
  setVisible(visible) {
    if (this.visible === visible) return
    this.visible = visible
    this.playEpoch += 1
    this.playingId = null
    this.output?.stop?.()
    this.output?.setVisible?.(visible)
    if (!visible && this.mode === 'replay') this.resumeLive()
    else if (visible && this.latest) this.adopt(this.latest)
  }
  /**
   * 按时间锚点推算当前本地播放与字幕位置。
   * @returns {PlaybackSnapshot} 调用方可在动画帧读取，不产生网络通信。
   */
  getSnapshot() {
    const progress = this.anchor?.progress ?? null
    const current = progress?.current ?? progress?.transition
    let elapsedMs = 0
    let revealedCharacters = 0
    if (current) {
      elapsedMs = Math.min(
        current.durationMs,
        Math.max(0, current.elapsedMs + this.now() - this.anchor.receivedAt)
      )
      const total = dialogueMarkdown(current.guide.text).count
      const subtitleDurationMs = current.guide.subtitleDurationMs ?? current.durationMs
      revealedCharacters =
        subtitleDurationMs > 0
          ? Math.min(total, Math.floor((total * elapsedMs) / subtitleDurationMs))
          : total
    }
    return {
      mode: this.mode,
      progress,
      elapsedMs,
      revealedCharacters,
      playback: { ...this.options },
      problem: this.localProblem ?? null,
      liveAvailable: this.mode === 'replay' && Boolean(this.latest)
    }
  }
  /**
   * 观察本地播放状态变化；连续字幕位置由 getSnapshot 本地计算。
   * @param {(snapshot: PlaybackSnapshot) => void} listener 接收本地状态。
   * @returns {() => void} 取消观察，不停止播放。
   */
  observe(listener) {
    this.listeners.add(listener)
    listener(this.getSnapshot())
    return () => this.listeners.delete(listener)
  }
  /** 仅通知当前客户端；输出或监听异常不反馈到后端演出时钟。 */
  publish() {
    for (const listener of this.listeners) {
      try {
        listener(this.getSnapshot())
      } catch {
        /* 播放不受单个展示监听影响。 */
      }
    }
  }
  /**
   * 用历史材料启动本地回放；继续接收实时快照但不自动打断回放。
   * @param {import('../../../shared/contracts/presentation.js').ReplayMaterials} materials 已保存的材料，不隐式重合成。
   * @param {{silent?: boolean, onEnd?: () => void}} [options] 本地静音与结束通知。
   * @returns {void} 回放计时与后端 Turn 生命周期独立。
   */
  replay(materials, { silent = false, onEnd = null } = {}) {
    if (this.mode === 'replay') this.resumeLive()
    this.mode = 'replay'
    this.replayEnded = onEnd
    this.output?.stop?.()
    this.playingId = null
    this.anchor = {
      receivedAt: this.now(),
      progress: {
        scene: materials.scene,
        defaultPerformances: materials.defaultPerformances,
        current: null,
        transition: null
      }
    }
    let guides = structuredClone(materials.guides)
    let index = 0
    const session = { refresh: null }
    this.replaySession = session
    const next = async () => {
      while (session.refresh) {
        const pending = session.refresh
        const replacement = await pending
        if (this.replaySession !== session) return
        if (pending !== session.refresh) continue
        session.refresh = null
        if (replacement) {
          materials = replacement
          guides = replacement.guides
        }
      }
      if (this.closed || this.mode !== 'replay') return
      const playback = materials.scenes?.findLast((entry) => entry.start <= index) ?? materials
      const guide = guides[index]
      if (!guide) {
        this.resumeLive()
        return
      }
      const readingMs = Math.max(800, dialogueMarkdown(guide.text).visibleCount * 80)
      const guideId = 'replay:' + index
      index += 1
      this.playingId = guideId
      const epoch = ++this.playEpoch
      const show = (audioMs) => {
        if (this.closed || epoch !== this.playEpoch) return
        const durationMs = Math.max(
          guide.subtitleDurationMs ?? audioMs ?? readingMs,
          guide.motionDurationMs ?? 0
        )
        this.anchor = {
          receivedAt: this.now(),
          progress: {
            scene: playback.scene,
            defaultPerformances: playback.defaultPerformances,
            current: { guideId, guide, durationMs, elapsedMs: 0, recurring: false },
            transition: null
          }
        }
        this.replayTimer = setTimeout(() => {
          if (this.closed || epoch !== this.playEpoch) return
          this.output?.stop?.()
          const pause = guides[index]?.pauseBeforeMs ?? 0
          if (pause > 0) this.replayTimer = setTimeout(next, pause)
          else next()
        }, durationMs)
        this.publish()
      }
      // 等音频开始再启动本句计时，加载资源的时间不能挤占台词播放。
      if (guide.audio && !silent && this.output?.play)
        Promise.resolve(
          this.output.play(guide, {
            offsetMs: 0,
            volume: this.options.volume,
            recurring: false
          })
        ).then(
          () => show(guide.audio.durationMs),
          () => show(null)
        )
      else show(guide.audio?.durationMs)
    }
    next()
  }
  /** 当前句沿用已开播材料，下一句开始前采用新朝向。 */
  refreshReplay(load) {
    if (this.replaySession)
      this.replaySession.refresh = Promise.resolve()
        .then(load)
        .catch((error) => {
          this.localProblem = { code: 'replay_refresh_failed', message: error.message }
          this.publish()
          return null
        })
  }

  /**
   * 回到最新后端计划；不重启或修改后端 sequence。
   * @returns {void} 丢弃本地回放锚点。
   */
  resumeLive() {
    const onEnd = this.replayEnded
    this.replayEnded = null
    this.replaySession = null
    this.mode = 'live'
    clearTimeout(this.replayTimer)
    this.playEpoch += 1
    this.playingId = null
    this.output?.stop?.()
    if (this.latest) this.adopt(this.latest)
    else {
      this.anchor = null
      this.publish()
    }
    onEnd?.()
  }
  /**
   * 手动发送仅在后端确认受理后退出本地回放；失败保留当前回放。
   * @param {string} chatId 目标对话。
   * @param {import('../../../shared/contracts/conversation.js').UserSubmission} submission 用户输入。
   * @param {import('../../../shared/contracts/settings.js').TurnOptions} options 本轮覆盖值与稳定业务请求身份。
   * @returns {Promise<string>} 后端已受理的 Turn 身份。
   */
  async createTurn(chatId, submission, options) {
    const turnId = await this.client.conversations.createTurn(chatId, submission, options)
    this.resumeLive()
    return turnId
  }
  /**
   * 释放本地播放与订阅，不停止后端对话。
   * @returns {void} 本地输出、计时器和监听已释放。
   */
  close() {
    this.closed = true
    this.playEpoch += 1
    clearTimeout(this.replayTimer)
    this.stopObserving?.()
    this.stopSettings?.()
    this.output?.close?.()
    this.output?.stop?.()
    this.events?.removeEventListener?.('storage', this.storageListener)
    this.listeners.clear()
  }
}
