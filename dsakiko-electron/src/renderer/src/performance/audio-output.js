/**
 * 创建无页面依赖的浏览器音频输出；模型绘制可由未来舞台组合适配器完成。
 * @param {Pick<import('../backend/client.js').BackendClient, 'assets'>} client 仅使用 assets.access 获取短期音频地址。
 * @param {{ Audio?: typeof Audio, onSpeechAudio?: (audio: HTMLAudioElement|null) => void }} [options] 浏览器构造器及口型分析入口，null 表示停止。
 * @returns {import('./interface.js').PlaybackOutput & {playPreview: (material: import('../../../shared/contracts/presentation.js').AudioMaterial, options: {volume: number, signal: AbortSignal, onStop: (error?: Error) => void}) => Promise<void>}} 舞台和历史共用的音频输出。
 */
export function createAudioOutput(
  client,
  { Audio: AudioConstructor = globalThis.Audio, onSpeechAudio = () => {} } = {}
) {
  let speech = null
  let bgm = null
  let speechLease = null
  let bgmLease = null
  let stopPreview = null
  function releaseAccess(id) {
    if (id) void client.assets.releaseAccess(id).catch(() => {})
  }
  // 资源地址查询可能晚于停止或换曲返回，用代次拒绝旧结果，避免重新启动已取消的播放。
  let speechEpoch = 0
  let bgmEpoch = 0
  let speechMuted = false
  let volume = 1
  let bgmVolume = 1
  let bgmKey = null
  let visible = true
  function stop(error) {
    speechEpoch += 1
    speech?.pause()
    onSpeechAudio(null)
    if (speech) speech.src = ''
    speech = null
    releaseAccess(speechLease)
    speechLease = null
    const notify = stopPreview
    stopPreview = null
    notify?.(error)
  }
  /**
   * 读取短期地址并从指定偏移播放；浏览器拒绝自动播放时交给调用方处理。
   * @param {import('../../../shared/contracts/presentation.js').Guide} guide 已就绪材料。
   * @param {import('./interface.js').PlayOptions} options 本地播放选项。
   * @returns {Promise<void>} 音频已尝试启动，不代表整句播放完成。
   */
  async function play(guide, options) {
    await playAudio(guide.audio, options)
  }
  async function playPreview(material, { volume, signal, onStop }) {
    await playAudio(material, { volume }, { signal, onStop })
  }
  async function playAudio(material, options, preview = null) {
    if (preview?.signal.aborted) return
    stop()
    if (!material || (preview && !visible)) {
      preview?.onStop()
      return
    }
    const epoch = speechEpoch
    function abortPreview() {
      stop()
    }
    if (preview) {
      preview.signal.addEventListener('abort', abortPreview, { once: true })
      stopPreview = (error) => {
        preview.signal.removeEventListener('abort', abortPreview)
        preview.onStop(error)
      }
    }
    speechMuted = options.silent === true
    try {
      const access = await client.assets.access(material.asset)
      if (epoch !== speechEpoch || options.getOffsetMs?.() >= material.durationMs) {
        releaseAccess(access.leaseId)
        return
      }
      speechLease = access.leaseId
      const audio = new AudioConstructor(access.url)
      audio.crossOrigin = 'anonymous'
      speech = audio
      // 历史语音沿用同一条音轨，只有实时台词需要驱动舞台口型。
      if (!preview) onSpeechAudio(audio)
      audio.addEventListener(
        'ended',
        () => {
          if (speech === audio) stop()
        },
        { once: true }
      )
      audio.addEventListener(
        'error',
        () => {
          if (speech === audio) stop(new Error('音频无法解码或读取。'))
        },
        { once: true }
      )
      audio.volume = speechMuted ? 0 : (options.volume ?? volume)
      audio.addEventListener(
        'loadedmetadata',
        () => {
          if (speech !== audio) return
          const offsetMs = options.getOffsetMs?.() ?? options.offsetMs ?? 0
          if (options.getOffsetMs && offsetMs >= material.durationMs) {
            stop()
            return
          }
          audio.currentTime = Math.min(offsetMs / 1000, Math.max(0, audio.duration - 0.01))
        },
        { once: true }
      )
      if (visible) await audio.play()
      if (epoch !== speechEpoch) audio.pause()
    } catch (error) {
      if (epoch === speechEpoch) {
        stop(error)
        if (!preview) throw error
      }
    }
  }
  /**
   * 曲目不变时只调整音量，避免每次设置快照到达都从头播放。
   * @param {import('../../../shared/contracts/presentation.js').BgmSettings} settings 共享曲目偏好。
   * @param {{volume: number}} options 已叠乘总音量的输出值。
   * @returns {Promise<void>} 新曲目已尝试启动或当前音量已更新。
   */
  async function setBgm(settings, options) {
    const key = JSON.stringify(settings)
    bgmVolume = options.volume
    if (key === bgmKey) {
      if (bgm) bgm.volume = bgmVolume
      return
    }
    bgmKey = key
    bgmEpoch += 1
    const epoch = bgmEpoch
    bgm?.pause()
    if (bgm) bgm.src = ''
    bgm = null
    releaseAccess(bgmLease)
    bgmLease = null
    if (!settings?.enabled || !settings.audio) return
    const access = await client.assets.access(settings.audio)
    if (epoch !== bgmEpoch) {
      releaseAccess(access.leaseId)
      return
    }
    bgmLease = access.leaseId
    const audio = new AudioConstructor(access.url)
    bgm = audio
    function finishBgm() {
      if (bgm !== audio) return
      releaseAccess(bgmLease)
      bgmLease = null
      bgm = null
      bgmKey = null
    }
    audio.addEventListener('ended', finishBgm, { once: true })
    audio.addEventListener('error', finishBgm, { once: true })
    audio.volume = bgmVolume
    audio.loop = settings.loop
    try {
      if (visible) await audio.play()
    } catch (error) {
      finishBgm()
      throw error
    }
    if (epoch !== bgmEpoch) audio.pause()
  }
  return {
    play,
    playPreview,
    stop,
    setBgm,
    setVisible(value) {
      visible = value
      if (!visible) {
        stop()
        bgm?.pause()
      } else if (bgm) void bgm.play().catch(() => {})
    },
    setVolume(options) {
      volume = options.volume
      if (speech) speech.volume = speechMuted ? 0 : volume
    },
    setBgmVolume(value) {
      bgmVolume = value
      if (bgm) bgm.volume = value
    },
    close() {
      stop()
      bgmEpoch += 1
      bgm?.pause()
      if (bgm) bgm.src = ''
      bgm = null
      releaseAccess(bgmLease)
      bgmLease = null
    }
  }
}
