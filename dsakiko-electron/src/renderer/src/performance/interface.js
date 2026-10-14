/** @typedef {import('./playback-contracts.js').PlaybackOptions} PlaybackOptions */
/** @typedef {import('../../../shared/contracts/presentation.js').SequenceSnapshot} SequenceSnapshot */
/** @typedef {import('../../../shared/contracts/presentation.js').GuideProgress} GuideProgress */
/** @typedef {{offsetMs: number, volume: number, recurring: boolean, silent?: boolean}} PlayOptions 本次播放偏移与本地音量。 */
/**
 * @typedef {object} PlaybackOutput 音频/模型输出由宿主注入，可只实现支持的能力。
 * @property {(guide: import('../../../shared/contracts/presentation.js').Guide, options: PlayOptions) => void | Promise<void>} [play] 从给定偏移播放，不回报后端进度。
 * @property {() => void} [stop] 停止当前普通播放。
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
    this.closed = false
    this.playingId = null
    this.playEpoch = 0
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
    this.stopObserving = this.client.performance.observe(sequenceId, (update) => {
      const progress = update.snapshot ?? update
      this.latest = { progress, receivedAt: this.now() }
      if (this.mode === 'live') this.adopt(this.latest)
      else this.publish()
    })
  }
  /** 相同 guide 的新锚点只校准字幕，不重复启动音频；其他客户端的播放状态不参与判断。 */
  adopt(latest) {
    this.anchor = latest
    const current = latest.progress.current ?? latest.progress.transition
    const id = current?.guideId ?? null
    if (this.playingId !== id) {
      this.playingId = id
      this.playEpoch += 1
      const epoch = this.playEpoch
      this.output?.stop?.()
      if (current)
        Promise.resolve(
          this.output?.play?.(current.guide, {
            offsetMs: Math.min(
              current.durationMs,
              current.elapsedMs + this.now() - latest.receivedAt
            ),
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
      const total = Array.from(current.guide.text ?? '').length
      revealedCharacters =
        current.durationMs > 0
          ? Math.min(total, Math.floor((total * elapsedMs) / current.durationMs))
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
   * @param {{silent?: boolean}} [options] 是否静音，仅影响本地。
   * @returns {void} 回放计时与后端 Turn 生命周期独立。
   */
  replay(materials, { silent = false } = {}) {
    this.mode = 'replay'
    clearTimeout(this.replayTimer)
    this.output?.stop?.()
    this.playingId = null
    const guides = structuredClone(materials.guides)
    let index = 0
    const next = () => {
      if (this.closed || this.mode !== 'replay') return
      const guide = guides[index]
      if (!guide) {
        this.playingId = null
        this.output?.stop?.()
        this.anchor = null
        this.publish()
        return
      }
      const durationMs =
        guide.audio?.durationMs ?? Math.max(800, Array.from(guide.text ?? '').length * 80)
      const guideId = 'replay:' + index
      index += 1
      this.anchor = {
        receivedAt: this.now(),
        progress: {
          scene: materials.scene,
          current: { guideId, guide, durationMs, elapsedMs: 0, recurring: false },
          transition: null
        }
      }
      this.playingId = guideId
      this.playEpoch += 1
      const epoch = this.playEpoch
      Promise.resolve(
        this.output?.play?.(guide, {
          offsetMs: 0,
          volume: this.options.volume,
          silent,
          recurring: false
        })
      ).catch((error) => {
        if (epoch !== this.playEpoch) return
        this.localProblem = { code: 'playback_failed', message: error.message }
        this.publish()
      })
      this.publish()
      this.replayTimer = setTimeout(next, durationMs)
    }
    next()
  }
  /**
   * 回到最新后端计划；不重启或修改后端 sequence。
   * @returns {void} 丢弃本地回放锚点。
   */
  resumeLive() {
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
