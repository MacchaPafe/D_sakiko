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
/** @typedef {{progress: LocalProgress, receivedAt: number, frozenAt?: number}} PlaybackAnchor 记录收到快照时的本地单调时标，不要求两台机器时钟同步。 */
/**
 * @typedef {object} PlaybackSnapshot
 * @property {'live' | 'replay'} mode 本客户端播放模式。
 * @property {LocalProgress | null} progress 实时快照或本地回放材料。
 * @property {boolean} waiting 手动回放已停在句尾。
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
    this.storyCaptions = false
    this.autoReplay = true
    this.readingLog = false
    /** @type {{materials: import('../../../shared/contracts/presentation.js').ReplayMaterials, silent: boolean, index: number, phase: 'selecting'|'loading'|'playing'|'waiting'|'gap', refresh: Promise<object|null>|null, revealed: boolean, offsetMs?: number, deadline?: number, remaining?: number}|null} 句间停顿和句内时钟各自保留剩余时间。 */
    this.replaySession = null
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
    this.output?.setBgmVolume?.(this.readingLog ? 0 : this.options.volume * this.options.bgmVolume)
  }
  /**
   * 应用共享 BGM 曲目，实际音量只取本地偏好。
   * @param {import('../../../shared/contracts/presentation.js').BgmSettings} settings 曲目、开关和循环偏好。
   * @returns {void} 输出失败仅发布本地问题。
   */
  setBgm(settings) {
    this.bgm = structuredClone(settings)
    Promise.resolve(
      this.output?.setBgm?.(settings, {
        volume: this.readingLog ? 0 : this.options.volume * this.options.bgmVolume
      })
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
    if (!this.visible || this.readingLog) {
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
        Math.max(
          0,
          current.elapsedMs + (this.anchor.frozenAt ?? this.now()) - this.anchor.receivedAt
        )
      )
      const total = dialogueMarkdown(current.guide.text).count
      const subtitleDurationMs = current.guide.subtitleDurationMs ?? current.durationMs
      revealedCharacters =
        subtitleDurationMs > 0
          ? Math.min(total, Math.floor((total * elapsedMs) / subtitleDurationMs))
          : total
      // 剧情字幕按阅读速度出现，语音、动作和换句仍使用完整的演出时钟。
      if (this.storyCaptions) {
        const readingProgress = 1 + Math.floor(elapsedMs / 30)
        revealedCharacters = Math.min(total, Math.max(revealedCharacters, readingProgress))
      }
    }
    if (current && this.replaySession?.revealed)
      revealedCharacters = dialogueMarkdown(current.guide.text).count
    return {
      waiting: this.replaySession?.phase === 'waiting',
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
    if (!this.readingLog) this.output?.stop?.()
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
    this.replaySession = {
      materials,
      silent,
      index: -1,
      phase: 'selecting',
      refresh: null,
      revealed: false
    }
    void this.nextReplayGuide()
  }

  /** 保留整场统一的位置；重新取朝向材料时只在下一句采用。 */
  async nextReplayGuide() {
    const session = this.replaySession
    if (!session || this.closed) return
    clearTimeout(this.replayTimer)
    session.phase = 'selecting'
    while (session.refresh) {
      const pending = session.refresh
      const replacement = await pending
      if (session !== this.replaySession || this.closed) return
      if (pending !== session.refresh) continue
      session.refresh = null
      if (replacement) session.materials = replacement
    }
    session.index += 1
    if (!session.materials.guides[session.index]) return this.resumeLive()
    session.revealed = false
    this.startReplayVoice(0)
  }

  /** 先等待声音启动，再从指定偏移恢复字幕、口型与句尾计时。 */
  startReplayVoice(offsetMs) {
    const session = this.replaySession
    const { materials, index } = session
    const guide = materials.guides[index]
    const epoch = ++this.playEpoch
    session.phase = 'loading'
    session.offsetMs = offsetMs
    if (this.readingLog) return
    const show = (audioMs) => {
      if (epoch !== this.playEpoch || this.closed || session !== this.replaySession) return
      const scene = materials.scenes?.findLast((entry) => entry.start <= index) ?? materials
      const readingMs = Math.max(800, dialogueMarkdown(guide.text).visibleCount * 80)
      const durationMs = Math.max(
        guide.subtitleDurationMs ?? audioMs ?? readingMs,
        guide.motionDurationMs ?? 0,
        audioMs ?? 0
      )
      this.playingId = 'replay:' + index
      this.anchor = {
        receivedAt: this.now(),
        progress: {
          scene: scene.scene,
          defaultPerformances: scene.defaultPerformances,
          transition: null,
          current: {
            guideId: this.playingId,
            guide,
            durationMs,
            elapsedMs: offsetMs,
            recurring: false
          }
        }
      }
      session.phase = 'playing'
      this.replayTimer = setTimeout(
        () => this.finishReplayGuide(),
        Math.max(0, durationMs - offsetMs)
      )
      this.publish()
    }
    if (guide.audio && offsetMs < guide.audio.durationMs && !session.silent && this.output?.play) {
      Promise.resolve(
        this.output.play(guide, { offsetMs, volume: this.options.volume, recurring: false })
      ).then(
        () => show(guide.audio.durationMs),
        () => show(null)
      )
    } else show(guide.audio?.durationMs)
  }

  finishReplayGuide() {
    const session = this.replaySession
    this.output?.stop?.()
    const next = session.materials.guides[session.index + 1]
    if (!next) return this.resumeLive()
    session.revealed = true
    if (!this.autoReplay) {
      session.phase = 'waiting'
      this.publish()
    } else this.scheduleReplayGap(next.pauseBeforeMs ?? 0)
  }

  scheduleReplayGap(ms) {
    if (ms <= 0) return void this.nextReplayGuide()
    this.replaySession.phase = 'gap'
    this.replaySession.deadline = this.now() + ms
    this.replayTimer = setTimeout(() => void this.nextReplayGuide(), ms)
  }

  /** @param {boolean} enabled 当前布局是否使用剧情字幕。 @returns {void} 切换时沿用同一句的时间位置。 */
  setStoryCaptions(enabled) {
    if (this.storyCaptions === enabled) return
    this.storyCaptions = enabled
    this.publish()
  }

  /** @param {boolean} automatic 是否自动推进整轮回放。 @returns {void} 已停在句尾时立即响应。 */
  setReplayAuto(automatic) {
    this.autoReplay = automatic
    if (this.readingLog) return
    const phase = this.replaySession?.phase
    if (automatic && phase === 'waiting') void this.nextReplayGuide()
    if (!automatic && phase === 'gap') {
      clearTimeout(this.replayTimer)
      this.replaySession.phase = 'waiting'
      this.publish()
    }
  }

  /** @returns {void} 手动模式先补全字，再按需切断语音并翻句。 */
  advanceReplay() {
    const session = this.replaySession
    if (!session || this.autoReplay || this.readingLog) return
    if (session.phase === 'waiting') return void this.nextReplayGuide()
    if (session.phase !== 'playing') return
    const { progress, revealedCharacters } = this.getSnapshot()
    if (revealedCharacters < dialogueMarkdown(progress.current.guide.text).count) {
      session.revealed = true
      this.publish()
    } else {
      this.output?.stop?.()
      void this.nextReplayGuide()
    }
  }

  /** @param {boolean} open 剧情记录是否覆盖舞台。 @returns {void} 本地回放冻结，实时计划继续接收。 */
  setReadingLog(open) {
    if (open === this.readingLog) return
    this.readingLog = open
    const session = this.replaySession
    this.output?.setBgmVolume?.(open ? 0 : this.options.volume * this.options.bgmVolume)
    this.playEpoch += 1
    this.playingId = null
    this.output?.stop?.()
    if (open) {
      clearTimeout(this.replayTimer)
      if (session?.phase === 'playing') session.offsetMs = this.getSnapshot().elapsedMs
      if (session?.phase === 'gap') session.remaining = Math.max(0, session.deadline - this.now())
      if (session && this.anchor) this.anchor.frozenAt = this.now()
    } else if (!session) {
      if (this.latest) this.adopt(this.latest)
    } else if (session.phase === 'gap') {
      if (this.autoReplay) this.scheduleReplayGap(session.remaining)
      else session.phase = 'waiting'
    } else if (session.phase === 'waiting') {
      if (this.autoReplay) void this.nextReplayGuide()
    } else if (session.phase !== 'selecting') this.startReplayVoice(session.offsetMs)
    this.publish()
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
    if (!this.readingLog) this.output?.stop?.()
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
