import { ref, type Ref } from 'vue'
import type { Live2DModel } from 'pixi-live2d-display'
import type { Ticker } from 'pixi.js'
import type { RendererCommand, RendererFact } from './constants'

export type RendererFactHandler = (fact: RendererFact) => void

/**
 * Electron-side command executor.
 * Behaviour decisions belong to the shared Python owner. This class owns SDK,
 * audio and DOM state and reports lifecycle facts back to that owner.
 */
export class Live2DRendererController {
  readonly textBubble: Ref<string | null> = ref(null)
  readonly userBubble: Ref<string | null> = ref(null)
  readonly isThinking: Ref<boolean> = ref(false)

  private readonly model: Live2DModel
  private readonly ticker: Ticker
  private readonly modelKey: string
  private readonly rendererId: string
  private readonly modelToken: string
  private readonly runtimeVersion: string
  private readonly modelUrl: string
  private readonly report: RendererFactHandler
  private readonly tickerCallback: () => void
  private readonly beforeModelUpdateCallback: () => void
  private activeMotionToken = ''
  private currentAudio: HTMLAudioElement | null = null
  private audioOwner = false
  private audioToken = ''
  private audioTurnId = ''
  private audioSegmentId = ''
  private audioContext: AudioContext | null = null
  private analyser: AnalyserNode | null = null
  private audioSource: MediaElementAudioSourceNode | null = null
  private mouthIndex = -1
  private mouthValue = 0
  private sharedMouthValue = 0
  private sharedMouthActive = false
  private sharedMouthToken = ''
  private lastMouthReportAt = 0
  private eyeLeftIndex = -1
  private eyeRightIndex = -1
  private eyeTransitionStart = 0
  private eyeTransitionFromLeft = 1
  private eyeTransitionFromRight = 1
  private eyeTransitionActive = false
  private started = false
  private readyData: Record<string, unknown> = {}

  constructor(model: Live2DModel, ticker: Ticker, modelKey: string, report: RendererFactHandler, rendererId = modelKey, modelToken = '', runtimeVersion = 'v3', modelUrl = '') {
    this.model = model
    this.ticker = ticker
    this.modelKey = modelKey
    this.rendererId = rendererId
    this.modelToken = modelToken
    this.runtimeVersion = runtimeVersion
    this.modelUrl = modelUrl
    this.report = report
    this.tickerCallback = () => this.updateLipSync()
    this.beforeModelUpdateCallback = () => this.applyModelOverrides()
  }

  start(): void {
    if (this.started) return
    this.started = true
    this.ticker.add(this.tickerCallback, undefined, 30 as any)
    ;(this.model.internalModel as any)?.on?.('beforeModelUpdate', this.beforeModelUpdateCallback)
    this.initParameters()
  }

  destroy(): void {
    if (!this.started) return
    this.started = false
    this.ticker.remove(this.tickerCallback, undefined)
    ;(this.model.internalModel as any)?.off?.('beforeModelUpdate', this.beforeModelUpdateCallback)
    this.stopAudio()
    this.stopMotion()
    try { this.audioContext?.close() } catch (_) { /* best effort */ }
    this.audioContext = null
  }

  reset(): void {
    this.stopAudio()
    this.stopMotion()
    this.textBubble.value = null
    this.userBubble.value = null
    this.isThinking.value = false
  }

  /** Stop transport-local playback after a WS loss; Python owns lifecycle facts. */
  abortTransportPlayback(): void {
    this.stopAudio()
    this.stopMotion()
  }

  reportReady(): void {
    if (!this.started) return
    this.report({ type: 'renderer_ready', data: { ...this.readyData } })
  }

  pushCommand(command: RendererCommand): void {
    const data = command.data || {}
    switch (command.type) {
      case 'motion': this.executeMotion(command); break
      case 'play_motion': this.executeMotion({ ...command, type: 'motion', data: { ...data, motion_token: data.token } }); break
      case 'audio': this.executeAudio(command); break
      case 'play_audio': this.executeAudio({ ...command, type: 'audio', data: { ...data, audio_token: data.token } }); break
      case 'mouth_amplitude': this.applySharedMouth(data); break
      case 'stop_audio': this.stopAudio(); break
      case 'stop_motion': this.stopMotion(); break
      case 'text': this.textBubble.value = String(data.text || ''); break
      case 'segment_started': this.textBubble.value = String(data.text || ''); break
      case 'user_text': this.userBubble.value = String(data.text || ''); break
      case 'thinking': this.isThinking.value = data.active === true; break
      case 'thinking_changed': this.isThinking.value = data.active === true; break
      case 'set_expression': this.executeExpression(data); break
      case 'reset': this.reset(); break
      case 'reset_renderer': this.reset(); break
      case 'close_renderer': try { (window as any).electronAPI?.closeWindow?.() } catch (_) { window.close() }; break
      default: break
    }
  }

  /** Compatibility adapter for old bridge messages; it performs no scheduling. */
  pushEvent(event: { type: string; data?: Record<string, unknown> }): void {
    const data = event.data || {}
    if (event.type === 'user_text') this.userBubble.value = String(data.text || '')
    if (event.type === 'text') this.textBubble.value = String(data.text || '')
  }

  handleClick(_clientX: number, _width: number): void {
    this.report({ type: 'renderer_intent', data: { intent: 'click' } })
  }

  private executeExpression(data: Record<string, any>): void {
    const manager = (this.model.internalModel as any)?.motionManager?.expressionManager
    if (!manager) return
    const expression = String(data.expression || '')
    if (!expression) {
      manager.resetExpression?.()
      return
    }
    Promise.resolve(manager.setExpression?.(expression)).catch((error) => {
      console.warn('[Live2DRendererController] Expression was not applied:', expression, error)
    })
  }

  private executeMotion(command: RendererCommand): void {
    const data = command.data
    const group = String(data.group || '')
    const index = Number(data.index)
    const priority = Number(data.priority ?? 1)
    const token = String(data.motion_token || command.event_id || '')
    if (!group || !Number.isInteger(index) || index < 0 || !token) {
      this.report({ type: 'command_failed', event_id: command.event_id, data: { token, phase: 'motion_start', reason: 'invalid_motion' } })
      return
    }
    const expressionId = String(data.expression_id || '')
    if (expressionId) {
      try { this.model.expression(expressionId) } catch (_) { /* renderer best effort only */ }
    }
    this.eyeTransitionActive = false
    this.stopMotion()
    this.activeMotionToken = token
    const manager = (this.model.internalModel as any)?.motionManager
    const onFinish = () => {
      if (this.activeMotionToken !== token) return
      this.activeMotionToken = ''
      this.queueEyeOpenTransition()
      this.report({ type: 'motion_finished', event_id: command.event_id, data: { token, turn_id: data.turn_id || '', segment_id: data.segment_id || '', group, index, renderer_id: this.rendererId } })
    }
    manager?.once?.('motionFinish', onFinish)
    this.model.motion(group, index, priority as any).then((started) => {
      if (this.activeMotionToken !== token) return
      if (!started) {
        this.activeMotionToken = ''
        this.report({ type: 'command_failed', event_id: command.event_id, data: { token, phase: 'motion_start', reason: 'motion_not_started', group, index } })
        return
      }
        this.report({ type: 'motion_started', event_id: command.event_id, data: { token, turn_id: data.turn_id || '', segment_id: data.segment_id || '', group, index, renderer_id: this.rendererId } })
    }).catch((error) => {
      if (this.activeMotionToken !== token) return
      this.activeMotionToken = ''
      this.report({ type: 'command_failed', event_id: command.event_id, data: { token, phase: 'motion_start', reason: String(error) } })
    })
  }

  private executeAudio(command: RendererCommand): void {
    const data = command.data
    const url = String(data.url || data.path || '')
    const segmentId = String(data.segment_id || command.event_id || '')
    if (!url) {
      this.report({ type: 'audio_ended', event_id: command.event_id, data: { token: String(data.token || data.audio_token || ''), turn_id: data.turn_id || '', segment_id: segmentId, reason: 'no_audio', renderer_id: this.rendererId } })
      return
    }
    this.stopAudio()
    const audioTarget = String(data.target_renderer_id || '')
    if (audioTarget && audioTarget !== this.rendererId) {
      // Motion still fans out to every renderer, but only one runtime owns
      // audible playback and its lifecycle facts.
      this.audioOwner = false
      return
    }
    this.audioOwner = true
    this.audioToken = String(data.token || data.audio_token || '')
    this.audioTurnId = String(data.turn_id || '')
    this.audioSegmentId = segmentId
    // Configure CORS before assigning src.  Constructing with `new Audio(url)`
    // can start the request before MediaElementAudioSourceNode can observe it,
    // leaving the analyser waveform at zero while playback still sounds fine.
    const audio = new Audio()
    audio.crossOrigin = 'anonymous'
    audio.preload = 'auto'
    audio.src = url
    this.currentAudio = audio
    audio.addEventListener('ended', () => {
      if (this.currentAudio !== audio) return
      this.mouthValue = 0
      this.sharedMouthValue = 0
      this.currentAudio = null
      this.reportMouthAmplitude(0, data)
      this.audioOwner = false
      this.report({ type: 'audio_ended', event_id: command.event_id, data: { token: String(data.token || data.audio_token || ''), turn_id: data.turn_id || '', segment_id: segmentId, renderer_id: this.rendererId } })
    })
    audio.addEventListener('error', () => {
      if (this.currentAudio !== audio) return
      this.currentAudio = null
      this.reportMouthAmplitude(0, data)
      this.audioOwner = false
      this.report({ type: 'audio_ended', event_id: command.event_id, data: { token: String(data.token || data.audio_token || ''), turn_id: data.turn_id || '', segment_id: segmentId, reason: 'audio_error', renderer_id: this.rendererId } })
    })
    this.setupLipSync(audio)
    audio.play().then(() => {
      // Chromium may keep an AudioContext suspended until the media element
      // has actually started. Retry the analyser hookup at that point without
      // creating a second audio element or changing the audio owner.
      if (!this.analyser && this.currentAudio === audio) this.setupLipSync(audio)
      void this.audioContext?.resume?.()
      this.report({ type: 'audio_started', event_id: command.event_id, data: { token: String(data.token || data.audio_token || ''), turn_id: data.turn_id || '', segment_id: segmentId, renderer_id: this.rendererId } })
    }).catch((error) => {
      if (this.currentAudio === audio) {
        this.reportMouthAmplitude(0, data)
        this.audioOwner = false
      }
      this.report({ type: 'audio_ended', event_id: command.event_id, data: { token: String(data.token || data.audio_token || ''), turn_id: data.turn_id || '', segment_id: segmentId, reason: String(error), renderer_id: this.rendererId } })
    })
  }

  private stopMotion(): void {
    this.activeMotionToken = ''
    try { (this.model.internalModel as any)?.motionManager?.stopAllMotions?.() } catch (_) { /* best effort */ }
  }

  private stopAudio(): void {
    const stoppedToken = this.audioToken
    const stoppedTurnId = this.audioTurnId
    const stoppedSegmentId = this.audioSegmentId
    const reportStopped = Boolean(this.currentAudio && this.audioOwner && stoppedToken)
    if (this.currentAudio && this.audioOwner) this.reportMouthAmplitude(0, {})
    if (this.currentAudio) {
      this.currentAudio.pause()
      this.currentAudio.src = ''
      this.currentAudio = null
    }
    try { this.audioSource?.disconnect() } catch (_) { /* best effort */ }
    try { this.analyser?.disconnect() } catch (_) { /* best effort */ }
    this.audioSource = null
    this.analyser = null
    this.mouthValue = 0
    this.sharedMouthValue = 0
    this.sharedMouthActive = false
    this.sharedMouthToken = ''
    this.audioOwner = false
    this.audioToken = ''
    this.audioTurnId = ''
    this.audioSegmentId = ''
    if (reportStopped) {
      this.report({
        type: 'audio_ended',
        data: {
          token: stoppedToken,
          turn_id: stoppedTurnId,
          segment_id: stoppedSegmentId,
          renderer_id: this.rendererId,
          reason: 'runtime_stopped',
        },
      })
    }
  }

  private initParameters(): void {
    try {
      const core = (this.model.internalModel as any)?.coreModel
      this.mouthIndex = core?.getParamIndex?.('PARAM_MOUTH_OPEN_Y') ?? -1
      this.eyeLeftIndex = core?.getParamIndex?.('PARAM_EYE_L_OPEN') ?? -1
      this.eyeRightIndex = core?.getParamIndex?.('PARAM_EYE_R_OPEN') ?? -1
      const internal = this.model.internalModel as any
      internal?.setAutoBlinkEnable?.(true)
      internal?.setAutoBreathEnable?.(true)
      const definitions = internal?.motionManager?.definitions || {}
      const motionGroups: Record<string, number> = {}
      const motionFilesByGroup: Record<string, string[]> = {}
      for (const [group, entries] of Object.entries(definitions)) {
        if (Array.isArray(entries)) {
          motionGroups[group] = entries.length
          motionFilesByGroup[group] = entries.map((entry: any) => String(entry?.file || entry?.File || ''))
        }
      }
      const expressionDefinitions = (internal?.motionManager?.expressionManager?.definitions || []) as any[]
      const expressionIds = expressionDefinitions
        .map((definition) => String(definition?.name || definition?.Name || ''))
        .filter(Boolean)
      this.readyData = {
        model_key: this.modelKey,
        renderer_role: 'electron',
        renderer_id: this.rendererId,
        model_token: this.modelToken,
        runtime_version: this.runtimeVersion,
        capabilities: { motion: true, audio: true, lipsync: true },
        motion_groups: motionGroups,
        motion_files_by_group: motionFilesByGroup,
        expression_ids: expressionIds,
        ...(this.modelUrl ? { model_urls: { model_json: this.modelUrl } } : {}),
      }
    } catch (_) { /* optional SDK features */ }
  }

  private setupLipSync(audio: HTMLAudioElement): void {
    try {
      if (!this.audioContext) {
        const AudioContextCtor = window.AudioContext || (window as any).webkitAudioContext
        if (!AudioContextCtor) throw new Error('AudioContext is unavailable')
        this.audioContext = new AudioContextCtor()
      }
      if (this.audioContext.state === 'suspended') void this.audioContext.resume()
      try { this.audioSource?.disconnect() } catch (_) { /* best effort */ }
      try { this.analyser?.disconnect() } catch (_) { /* best effort */ }
      this.analyser = this.audioContext.createAnalyser()
      this.analyser.fftSize = 256
      this.audioSource = this.audioContext.createMediaElementSource(audio)
      this.audioSource.connect(this.analyser)
      this.analyser.connect(this.audioContext.destination)
    } catch (_) {
      this.analyser = null
      this.audioSource = null
    }
  }

  private updateLipSync(): void {
    if (this.mouthIndex < 0) return
    if (this.audioOwner && this.currentAudio && this.analyser) {
      const samples = new Float32Array(this.analyser.fftSize)
      this.analyser.getFloatTimeDomainData(samples)
      let power = 0
      for (const sample of samples) power += sample * sample
      this.mouthValue = Math.min(1, Math.sqrt(power / samples.length) * 2.8)
      this.reportMouthAmplitude(this.mouthValue, {})
    } else if (this.sharedMouthActive) {
      this.mouthValue = this.sharedMouthValue
    } else {
      this.mouthValue *= 0.82
    }
  }

  private reportMouthAmplitude(amplitude: number, data: Record<string, unknown>): void {
    if (!this.audioOwner && amplitude !== 0) return
    const now = performance.now()
    const value = Math.max(0, Math.min(1, amplitude))
    if (value !== 0 && now - this.lastMouthReportAt < 33) return
    this.lastMouthReportAt = now
    this.report({
      type: 'mouth_amplitude',
      data: {
        renderer_id: this.rendererId,
        amplitude: value,
        token: data.token || data.audio_token || this.audioToken,
        turn_id: data.turn_id || this.audioTurnId,
        segment_id: data.segment_id || this.audioSegmentId,
      },
    })
  }

  private applySharedMouth(data: Record<string, unknown>): void {
    const value = Number(data.amplitude)
    if (!Number.isFinite(value)) return
    const token = String(data.token || '')
    if (token && this.sharedMouthToken && token !== this.sharedMouthToken && value <= 0) return
    if (token) this.sharedMouthToken = token
    this.sharedMouthValue = Math.max(0, Math.min(1, value))
    this.mouthValue = this.sharedMouthValue
    this.sharedMouthActive = this.sharedMouthValue > 0.001
    if (!this.sharedMouthActive && token === this.sharedMouthToken) this.sharedMouthToken = ''
  }

  private queueEyeOpenTransition(): void {
    const core = (this.model.internalModel as any)?.coreModel
    if (!core?.getParamFloat || this.eyeLeftIndex < 0 || this.eyeRightIndex < 0) return
    this.eyeTransitionFromLeft = Number(core.getParamFloat(this.eyeLeftIndex)) || 0
    this.eyeTransitionFromRight = Number(core.getParamFloat(this.eyeRightIndex)) || 0
    this.eyeTransitionStart = performance.now()
    this.eyeTransitionActive = true
  }

  /** Runs from pixi-live2d-display's formal final-parameter lifecycle hook. */
  private applyModelOverrides(): void {
    const core = (this.model.internalModel as any)?.coreModel
    if (!core?.setParamFloat) return
    if (this.mouthIndex >= 0) core.setParamFloat(this.mouthIndex, this.mouthValue)
    if (!this.eyeTransitionActive || this.eyeLeftIndex < 0 || this.eyeRightIndex < 0) return
    const progress = Math.min(1, Math.max(0, (performance.now() - this.eyeTransitionStart) / 100))
    const lerp = (from: number) => from + (1 - from) * progress
    core.setParamFloat(this.eyeLeftIndex, lerp(this.eyeTransitionFromLeft))
    core.setParamFloat(this.eyeRightIndex, lerp(this.eyeTransitionFromRight))
    if (progress >= 1) this.eyeTransitionActive = false
  }
}
