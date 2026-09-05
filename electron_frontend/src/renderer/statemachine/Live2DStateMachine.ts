import { ref, type Ref } from 'vue'
import type { Live2DModel } from 'pixi-live2d-display'
import type { Ticker } from 'pixi.js'
import {
  EMOTION_MAP, MOTION_GROUP_SIZES, MODEL_SPECIFIC_SIZES,
  LONG_AUDIO_THRESHOLD_SECONDS, LONG_AUDIO_REPEAT_DELAY_SECONDS, LONG_AUDIO_MAX_REPEATS,
  IDLE_RECOVER_DELAY_MS, TIMED_IDLE_INTERVAL_MS,
  THINK_INTERVAL_FIRST, THINK_INTERVAL_SUBSEQUENT,
  EYE_OPEN_DURATION_MS, BYE_TIMEOUT_MS, CLICK_THROTTLE_MS,
} from './constants'
import type { StateMachineEvent } from './constants'
import { createParameterAdapter, type ParameterAdapter } from './parameter-adapter'
import {
  baseSemanticForModel,
  nextSakikoMaskTransition,
  selectBlackSakikoEntry,
  selectBaseExpression,
  selectExpressionForMotion,
  type BasePresentation,
  type Live2DPresentationMetadata,
  type SakikoPresentationState,
} from './presentation-policy'

interface PendingAssistantSegment {
  text: string
  translation: string
  audioUrl: string
  group: string
}

type MotionFinishOutcome = 'completed' | 'unavailable'

export class Live2DStateMachine {
  // ── 模型 ──
  private model: Live2DModel
  private ticker: Ticker
  private tickerCallback: () => void
  private modelKey = 'sakiko'

  // ── 输出 ──
  readonly textBubble: Ref<string | null> = ref(null)
  readonly userBubble: Ref<string | null> = ref(null)
  readonly isThinking: Ref<boolean> = ref(false)

  // ── local presentation state ──
  // Whether the current local motion has completed.
  private motionIsOver = true
  // Whether the current thinking motion has completed.
  private thinkMotionIsOver = true
  // Whether the current audio segment has completed.
  private turnMotionComplete = true
  // Idle recovery is intentionally local to this runtime.
  private idleRecoverTimer = 0
  private idleMotionInFlight = false
  // Timed idle timer.
  private lastSavedTime = 0
  // Thinking interval timer.
  private lastThinkTime = 0
  // Thinking interval (first 1s, then 15s).
  private thinkInterval = THINK_INTERVAL_FIRST
  // Close animation has already been requested.
  private ifBye = false
  // Click request waiting for the next ticker frame.
  private mouseClicked = false
  private talkingActive = false

  // ── 音频 ──
  private audioPlaying = false
  private currentAudio: HTMLAudioElement | null = null
  private currentAudioId = 0

  // ── 长音频 ──
  private longAudioActive = false
  private longAudioGroup = ''
  private longAudioNextMotionAt = 0
  private longAudioTriggeredCount = 0

  // ── 睁眼过渡 ──
  private eyeOpenPending = false
  private eyeOpenTransitionActive = false
  private eyeOpenStartTime = 0
  private eyeOpenStartL = 1.0
  private eyeOpenStartR = 1.0

  // ── 口型同步 ──
  private mouthSyncFrameCount = 0
  private mouthOpenValue = 0
  // Electron's Web Audio RMS is lower than the Pygame WavHandler envelope.
  private lipSyncN = 1.9
  private _mouthParamIndex = -1
  private audioContext: AudioContext | null = null
  private analyserNode: AnalyserNode | null = null

  // ── Stale Promise ──
  private currentMotionId = 0
  /** Invalidates async expression/motion work when this model is released. */
  private runtimeGeneration = 0
  private motionFinishCleanup: (() => void) | null = null
  private lastClickTime = 0
  private eventQueue: StateMachineEvent[] = []
  private modelLoaded = false
  private parameters: ParameterAdapter
  private sakikoState: SakikoPresentationState
  private bridgeBasePresentation: BasePresentation
  private finalParameterBoundaryCleanup: (() => void) | null = null
  private initialPresentationPrepared = false
  // A renderer instance gets one local entrance. Recreated runtimes receive
  // a new state machine, while ordinary turn/reset traffic does not replay it.
  private coldStartEntranceRequested = false
  // Pygame starts Sakiko with the mask on.  Electron owns this transient
  // presentation state locally so repeated mask intents alternate correctly.
  private sakikoMaskOn = true
  private presentationMetadata: Live2DPresentationMetadata
  private presentationRequestId = 0
  private presentationQueue: Promise<void> = Promise.resolve()

  // Electron owns segment ordering.  Python only appends business facts;
  // normal completion never interrupts the active segment.
  private pendingSegments: PendingAssistantSegment[] = []
  private activeSegment: PendingAssistantSegment | null = null
  private activeSegmentAudioDone = true
  private activeSegmentMotionDone = true
  private byeTimer: ReturnType<typeof setTimeout> | null = null
  private byeComplete = false

  constructor(
    model: Live2DModel,
    ticker: Ticker,
    modelKey?: string,
    presentationMetadata: Live2DPresentationMetadata = { runtimeKind: 'v2', motionFilesByGroup: {}, expressionIds: [] },
    sakikoState?: SakikoPresentationState,
    bridgeBasePresentation: BasePresentation = 'idle',
  ) {
    this.model = model
    this.ticker = ticker
    if (modelKey) this.modelKey = modelKey
    this.presentationMetadata = presentationMetadata
    this.sakikoState = sakikoState
    this.bridgeBasePresentation = bridgeBasePresentation
    this.parameters = createParameterAdapter(model)
    this.tickerCallback = () => this.onTickerUpdate()
  }

  private get manifestHasMotions(): boolean {
    return Object.keys(this.presentationMetadata.motionFilesByGroup).length > 0
  }

  private resolveMotionGroup(group: string, position: 'C' | 'L' | 'R' | null = 'C'): string {
    const manifestGroups = Object.keys(this.presentationMetadata.motionFilesByGroup)
    const exact = manifestGroups.find(candidate => candidate.toLowerCase() === group.toLowerCase())
    if (!position) return exact || group
    const positioned = manifestGroups.find(candidate => candidate.toLowerCase() === `${group}_${position}`.toLowerCase())
    return positioned || exact || group
  }

  private manifestMotionFiles(group: string, position: 'C' | 'L' | 'R' | null = 'C'): Array<string | null> | undefined {
    return this.presentationMetadata.motionFilesByGroup[this.resolveMotionGroup(group, position)]
  }

  private getMotionSize(group: string): number {
    // The loaded manifest is authoritative for this local runtime.  Static
    // tables remain only as a compatibility fallback when metadata is absent.
    const manifestFiles = this.manifestMotionFiles(group)
    if (manifestFiles) return manifestFiles.length
    if (this.manifestHasMotions) return 0
    return MODEL_SPECIFIC_SIZES[this.modelKey]?.[group] ?? MOTION_GROUP_SIZES[group] ?? 1
  }

  start(options: { initialEntrance?: boolean } = {}): void {
    this.ticker.add(this.tickerCallback, undefined, 30 as any)
    const now = performance.now()
    // Pygame starts with no completed motion, so idle recovery does not fire
    // immediately on the first cold-start frame. The 2.5s timer begins only
    // after a motion completion callback (or an explicit talking stop).
    this.idleRecoverTimer = now
    this.motionIsOver = false
    this.lastSavedTime = now
    this.lastThinkTime = now
    this.modelLoaded = true
    if (!this.initialPresentationPrepared) this.applyColdStartPresentation()
    this._initBlinkBreath()
    this._initFinalParameterBoundary()
    if (options.initialEntrance !== false) this.startColdStartEntrance()
    console.log('[StateMachine] Started')
  }

  /**
   * Apply the manifest-selected base face while the stage is still empty.
   * Cubism expression files are asynchronous; preparing this before the
   * first Pixi render keeps cold start visually identical to a later model
   * switch without a delayed reload or synthetic transition.
   */
  async prepareInitialPresentation(): Promise<void> {
    this.modelLoaded = true
    this.applyColdStartPresentation()
    await this.presentationQueue
    this.initialPresentationPrepared = true
  }

  private applyColdStartPresentation(): void {
    // Upstream only assigns an initial expression for Sakiko. Ordinary
    // characters keep the model's authored/default expression at cold start.
    if (this.modelKey.trim().toLowerCase() !== 'sakiko') return
    const selected = selectBaseExpression(this.presentationMetadata, 'serious')
    void this.applyExpression(selected.expression)
  }

  destroy(): void {
    if (this.byeTimer) {
      clearTimeout(this.byeTimer)
      this.byeTimer = null
    }
    this.runtimeGeneration++
    this.presentationRequestId++
    this.finalParameterBoundaryCleanup?.()
    this.finalParameterBoundaryCleanup = null
    this.ticker.remove(this.tickerCallback, undefined)
    this.currentMotionId++
    this.cancelMotionFinishListener()
    this._resetEyeOpenTransition()
    this.stopAudio()
    if (this.audioContext) { try { this.audioContext.close() } catch (_) {}; this.audioContext = null }
    this.modelLoaded = false
    console.log('[StateMachine] Destroyed')
  }

  pushEvent(event: StateMachineEvent): void {
    if (!this.modelLoaded) { this.eventQueue.push(event); return }
    this.eventQueue.push(event)
  }

  // ── 主循环：每帧由 Ticker 调用 ──
  private onTickerUpdate(): void {
    const now = performance.now()
    this.processEvents(now)
    if (this.ifBye) return
    this.checkThinking(now)
    this.checkIdleRecover(now)
    this.checkTimedIdle(now)
    this.checkClick(now)
    this.turnMotionComplete = !this.audioPlaying
    this.checkLongAudioLoop(now)
  }

  // ── processEvents: consume backend business facts ──
  private processEvents(now: number): void {
    while (this.eventQueue.length > 0) {
      const event = this.eventQueue.shift()!
      switch (event.type) {
        case 'assistant_segment':
        case 'emotion': {
          const data = event.data || {}
          const label = String(data.label || data.emotion || 'LABEL_0')
          if (label === 'bye') {
            this.beginBye()
            break
          }
          const group = EMOTION_MAP[label] || ''
          this.enqueueSegment({
            text: String(data.text || ''),
            translation: String(data.translation || ''),
            audioUrl: String(data.audio_url || data.audio || ''),
            group,
          })
          break
        }

        case 'text_generating': {
          const active = event.data.active === true
          this.isThinking.value = active
          if (!active) {
            this.thinkMotionIsOver = true
            this.restoreBasePresentationIfIdle()
          }
          break
        }

        case 'cancel':
        case 'cancel_turn': {
          this.interruptSegments()
          this.motionIsOver = true
          this.thinkMotionIsOver = true
          this.turnMotionComplete = true
          this.idleMotionInFlight = false
          this.idleRecoverTimer = performance.now()
          this.isThinking.value = false
          this.textBubble.value = '...'
          this._resetLongAudio()
          this._resetEyeOpenTransition()
          break
        }

        case 'user_text': {
          if (event.data.text) this.userBubble.value = event.data.text
          break
        }

        case 'bye': {
          this.beginBye()
          break
        }

        case 'switch_character':
        case 'switch_live2d': {
          // App reloads the model when model_url is present; the local FSM
          // only owns the transition animation.
          this.interruptSegments()
          this._resetLongAudio()
          this.motionIsOver = false
          this.thinkMotionIsOver = true
          this.applyBasePresentation()
          this._playMotion('change_character', 3)
          break
        }

        case 'sakiko_state':
        case 'char_converted': {
          // The black/white costume and mask choreography is an upstream V2
          // path. V3 may still use this business fact to reload its selected
          // model in App.vue, but the local state machine must not invent V2
          // transition motions or base-expression changes.
          if (this.presentationMetadata.runtimeKind !== 'v2') break
          const { value, mask_on: maskOn } = event.data || {}
          this.interruptSegments()
          let blackEntryGroup: 'change_character' | 'change_character_maskoff' | null = null
          if (value === 'black' || value === 'white') {
            this.sakikoState = value
            if (value === 'black') {
              // Upstream randomizes the entry animation and remembers the
              // result for the next mask toggle.  Keep that choice local.
              const entry = selectBlackSakikoEntry(
                typeof maskOn === 'boolean' ? maskOn : undefined,
              )
              this.sakikoMaskOn = entry.maskOn
              blackEntryGroup = entry.requestedGroup
            } else {
              this.sakikoMaskOn = typeof maskOn === 'boolean' ? maskOn : true
            }
          }
          if (value === 'maskoff') {
            this.motionIsOver = false
            if (this.sakikoState === 'black') {
              const transition = nextSakikoMaskTransition(this.sakikoMaskOn)
              this.sakikoMaskOn = transition.maskOn
              this._playMotion(transition.requestedGroup, 3)
            } else {
              // Upstream uses the first thinking motion as white Sakiko's
              // mask interaction; it is not a black-mask transition.
              this._playMotion('text_generating', 3, false, 0)
            }
          } else if (value === 'black' || value === 'white') {
            this.motionIsOver = false
            const group = blackEntryGroup || 'change_character'
            // Upstream Sakiko conversion uses NORMAL (2), starts the
            // transition first, then applies the role base expression. This
            // ordering is intentionally separate from V3 auto expressions.
            this._playMotion(group, 2, false, undefined, undefined, () => {
              this.applyBasePresentation()
            })
          }
          break
        }

        case 'talking': {
          this.talkingActive = event.data?.active === true
          if (this.talkingActive) {
            this._resetLongAudio()
            this.motionIsOver = false
            this._playMotion('talking_motion', 4)
          } else {
            // Pygame's stop_talking invokes its normal finish callback; it
            // does not stop the SDK motion or reset the active expression.
            this._resetLongAudio()
            this._queueEyeOpen()
            this.motionIsOver = true
            this.idleRecoverTimer = performance.now()
          }
          break
        }

        case 'expression': {
          const expression = String(event.data?.name || event.data?.expression || '')
          if (expression && this.presentationMetadata.expressionIds.includes(expression)) {
            this.applyExpression(expression)
          }
          break
        }

        case 'theme':
          break
      }
    }
  }

  private enqueueSegment(segment: PendingAssistantSegment): void {
    this.isThinking.value = false
    this.thinkMotionIsOver = true
    this.pendingSegments.push(segment)
    if (!this.activeSegment) this.startNextSegment()
  }

  private startNextSegment(): void {
    if (this.ifBye || this.activeSegment || this.pendingSegments.length === 0) return
    const segment = this.pendingSegments.shift()!
    this.activeSegment = segment
    this.activeSegmentAudioDone = !segment.audioUrl
    this.activeSegmentMotionDone = !segment.group
    this.motionIsOver = !segment.group
    this.turnMotionComplete = !segment.audioUrl
    this._resetLongAudio()
    // Electron subtitles are Chinese-only when the backend supplies the
    // localized translation; fall back to the source text for Chinese-mode
    // responses that intentionally have no translation.
    const translatedSubtitle = segment.translation.trim()
    const sourceSubtitle = segment.text.trim()
    const isChinese = (value: string): boolean =>
      /[\u3400-\u9fff]/.test(value) && !/[ぁ-ゖァ-ヺ]/.test(value)
    const subtitle = isChinese(translatedSubtitle)
      ? translatedSubtitle
      : (isChinese(sourceSubtitle) ? sourceSubtitle : '')
    this.textBubble.value = subtitle || null
    console.log('[StateMachine] segment start', segment.text.slice(0, 40))

    if (segment.group) {
      this._playMotion(segment.group, 3, false, undefined, () => {
        this.activeSegmentMotionDone = true
        this.maybeFinishSegment()
      }, () => {
        this.startSegmentAudioIfNeeded(segment.audioUrl)
      })
    } else if (segment.audioUrl) {
      // With no motion to own the boundary, match upstream's fallback and
      // start the audio immediately.
      this.startSegmentAudioIfNeeded(segment.audioUrl)
    }
    this.maybeFinishSegment()
  }

  private startSegmentAudioIfNeeded(audioUrl: string): void {
    if (!audioUrl || !this.activeSegment || this.currentAudio || this.activeSegmentAudioDone) return
    if (this.activeSegmentMotionDone) this.idleRecoverTimer = performance.now()
    this.startSegmentAudio(audioUrl)
  }

  private startSegmentAudio(audioUrl: string): void {
    const aid = ++this.currentAudioId
    const el = new Audio()
    el.crossOrigin = 'anonymous'
    el.src = audioUrl
    this.currentAudio = el
    this.audioPlaying = true
    this.activeSegmentAudioDone = false
    el.addEventListener('loadedmetadata', () => {
      if (aid !== this.currentAudioId || !this.activeSegment) return
      if (el.duration > LONG_AUDIO_THRESHOLD_SECONDS) {
        this.longAudioActive = true
        this.longAudioGroup = this.activeSegment.group
        // The delay is measured from the initial motion's finish, not from
        // metadata/audio load. If the motion already finished while the
        // browser was loading metadata, use that boundary now.
        if (this.activeSegmentMotionDone) {
          this.longAudioNextMotionAt = performance.now() + LONG_AUDIO_REPEAT_DELAY_SECONDS * 1000
        }
      }
    })
    const complete = () => {
      if (aid !== this.currentAudioId) return
      this.audioPlaying = false
      this.currentAudio = null
      this.activeSegmentAudioDone = true
      this._resetLongAudio()
      this._rawSetMouth(0)
      this.maybeFinishSegment()
    }
    el.addEventListener('ended', complete)
    el.addEventListener('error', complete)
    this._setupLipSync(el, aid)
    void el.play().catch(complete)
  }

  private maybeFinishSegment(): void {
    if (!this.activeSegment || !this.activeSegmentAudioDone) return
    // A no-audio backend reply is emitted in a short burst upstream. Do not
    // turn every segment into a motion-completion barrier: the next selected
    // motion naturally supersedes the prior one, while audio keeps its own
    // observable FIFO boundary below.
    const finished = this.activeSegment
    const motionDone = this.activeSegmentMotionDone
    this.activeSegment = null
    this.audioPlaying = false
    this.turnMotionComplete = true
    // Audio determines FIFO advancement, while motion keeps its own local
    // lifecycle.  Do not let idle recovery interrupt a still-running emotion
    // motion merely because its audio ended first.
    if (motionDone) {
      this.motionIsOver = true
    } else {
      this.motionIsOver = false
    }
    console.log('[StateMachine] segment end', finished.text.slice(0, 40))
    this.startNextSegment()
    // A no-audio reply advances the FIFO immediately, but its selected motion
    // still owns the face until it actually finishes. V2 may restore its role
    // base after that finish; V3 keeps the upstream auto-expression visible.
    if (motionDone) this.restoreBasePresentationIfIdle()
  }

  private interruptSegments(): void {
    this.pendingSegments.length = 0
    this.activeSegment = null
    this.currentAudioId++
    this.stopAudio()
    // Cancel is an audio/FIFO operation. Keep the visible motion running and
    // only invalidate its callbacks so a user action is not cut off abruptly.
    this.currentMotionId++
    this.cancelMotionFinishListener()
    this._resetEyeOpenTransition()
    this._resetLongAudio()
  }

  private beginBye(): void {
    if (this.ifBye) return
    this.interruptSegments()
    this.ifBye = true
    this.byeComplete = false
    this.eventQueue.length = 0
    this.isThinking.value = false
    this.thinkMotionIsOver = true
    this.turnMotionComplete = true
    this.textBubble.value = null
    this.motionIsOver = false
    // A healthy runtime exits on the actual bye motion completion.  Keep a
    // watchdog only for a missing SDK completion callback or load failure.
    this.byeTimer = setTimeout(() => {
      this.finishBye('unavailable')
    }, BYE_TIMEOUT_MS)
    this._playMotion('bye', 3, false, undefined, outcome => this.finishBye(outcome))
  }

  private finishBye(outcome: MotionFinishOutcome): void {
    if (this.byeComplete) return
    this.byeComplete = true
    if (this.byeTimer) {
      clearTimeout(this.byeTimer)
      this.byeTimer = null
    }
    if (outcome === 'unavailable') {
      console.warn('[StateMachine] bye motion did not complete; using close fallback')
    }
    this.destroy()
    try { (window as any).electronAPI?.closeWindow() } catch (_) { try { window.close() } catch (_) {} }
  }

  // ── checkThinking: local thinking cadence ──
  private checkThinking(now: number): void {
    if (!this.isThinking.value || !this.thinkMotionIsOver) return
    if (now - this.lastThinkTime <= this.thinkInterval * 1000) return

    this.lastThinkTime = now
    this.thinkInterval = THINK_INTERVAL_SUBSEQUENT

    this.thinkMotionIsOver = false

    this._playMotion('text_generating', 3)
  }

  // ── checkIdleRecover: local idle recovery ──
  private checkIdleRecover(now: number): void {
    if (!this.motionIsOver || this.audioPlaying) return
    if (this.isThinking.value) return
    if (this.idleMotionInFlight) return
    if (now - this.idleRecoverTimer <= IDLE_RECOVER_DELAY_MS) return

    this.motionIsOver = false
    this.idleMotionInFlight = true
    // Pygame starts idle_motion without a finish callback. It is a recovery
    // pose, not a 2.5-second repeating cue; a later 25s IDLE or business
    // motion owns the next transition.
    this._playMotion('idle_motion', 1, true)
  }

  // ── checkTimedIdle: periodic local idle ──
  private checkTimedIdle(now: number): void {
    if (now - this.lastSavedTime <= TIMED_IDLE_INTERVAL_MS) return
    // Advance before checking the gate. Upstream skips this period when a turn
    // is speaking/thinking; it does not play an overdue IDLE after unblocking.
    this.lastSavedTime = now
    this.idleMotionInFlight = false
    // Match the upstream visible gate: no active turn audio and no thinking.
    if (!this.turnMotionComplete || this.isThinking.value) {
      return
    }

    this.motionIsOver = false
    this._playMotion('IDLE', 1)
  }

  // ── checkClick ──
  private checkClick(now: number): void {
    if (!this.mouseClicked) return
    this.mouseClicked = false
    if (this.modelKey.toLowerCase() !== 'sakiko') return
    this.thinkMotionIsOver = true
    this.motionIsOver = false
    this.idleMotionInFlight = false
    const idleSize = this.getMotionSize('IDLE')
    if (idleSize <= 0) { this.motionIsOver = true; return }
    const idx = Math.floor(Math.random() * idleSize)
    this._playMotion('IDLE', 1, false, idx)
  }

  handleClick(clientX: number, width: number): void {
    if (this.modelKey.toLowerCase() !== 'sakiko') return
    if (performance.now() - this.lastClickTime < CLICK_THROTTLE_MS) return
    this.lastClickTime = performance.now()
    this.mouseClicked = true
  }

  // ── checkLongAudio: long-segment local motion loop ──
  private checkLongAudioLoop(now: number): void {
    if (!this.longAudioActive) return
    if (!this.audioPlaying) { this._resetLongAudio(); return }
    if (!this.motionIsOver) return
    if (!this.longAudioGroup) { this._resetLongAudio(); return }
    if (this.longAudioTriggeredCount >= LONG_AUDIO_MAX_REPEATS) return

    // A positive deadline is installed by the motion-finish boundary (or by
    // late audio metadata when that finish already happened). Do not create
    // a new timer from an arbitrary ticker frame.
    if (this.longAudioNextMotionAt <= 0) return
    if (now < this.longAudioNextMotionAt) return

    this.motionIsOver = false
    this._playMotion(this.longAudioGroup, 3)
    this.longAudioTriggeredCount++
    this.longAudioNextMotionAt = 0  // 重置，下次 onFinish 后重新计时
  }

  private currentBaseSemantic(): 'idle' | 'serious' {
    return baseSemanticForModel(this.modelKey, this.sakikoState, this.bridgeBasePresentation)
  }

  /** Serialize expression updates so an older async SDK load cannot win later. */
  private applyExpression(expression: string | null): Promise<void> {
    const requestId = ++this.presentationRequestId
    const generation = this.runtimeGeneration
    const apply = async () => {
      if (!this.modelLoaded || generation !== this.runtimeGeneration || requestId !== this.presentationRequestId) return
      try {
        if (expression) {
          // pixi-live2d-display returns false both for an unsupported id and
          // when the requested expression is already active.  Capability
          // filtering above establishes support, so false must not erase the
          // current face during repeated thinking/emotion cues.
          await this.model.expression(expression)
        } // A missing policy result means "leave the current expression".
          // This is the upstream behavior for V3 models without a supported
          // semantic/base expression; do not invent a reset lifecycle.
      } catch (_) {
        // A transient SDK/load exception must not turn a known presentation
        // into a reset or advance a motion with a stale expression request.
      }
    }
    const scheduled = this.presentationQueue.then(apply, apply)
    this.presentationQueue = scheduled.catch(() => {})
    return scheduled
  }

  private applyBasePresentation(): void {
    const selected = selectBaseExpression(this.presentationMetadata, this.currentBaseSemantic())
    void this.applyExpression(selected.expression)
  }

  private restoreBasePresentationIfIdle(): void {
    if (this.presentationMetadata.runtimeKind !== 'v2') return
    if (this.ifBye || this.isThinking.value || this.talkingActive || this.audioPlaying) return
    if (this.activeSegment || this.pendingSegments.length > 0) return
    this.applyBasePresentation()
  }

  private startColdStartEntrance(): void {
    if (this.coldStartEntranceRequested) return
    this.coldStartEntranceRequested = true
    // Match ordinary local model switches. A missing group completes through
    // _playMotion and therefore cannot hold idle recovery in a static pose.
    this._playMotion('change_character', 3)
  }

  // ── 动作播放 + 回调 ──
  private _playMotion(
    requestedGroup: string,
    priority: number,
    noFinishReset?: boolean,
    fixedIdx?: number,
    onFinish?: (outcome: MotionFinishOutcome) => void,
    onStart?: () => void,
  ): void {
    const generation = this.runtimeGeneration
    const group = this.resolveMotionGroup(requestedGroup)
    const size = this.getMotionSize(group)
    if (size <= 0) {
      // The upstream adapter returns False for a missing group and does not
      // apply an expression.  The caller may use this boundary to start its
      // audio fallback, but no synthetic V3 face/reset is introduced.
      onStart?.()
      this.idleMotionInFlight = false
      this.motionIsOver = true
      if (requestedGroup === 'text_generating') this.thinkMotionIsOver = true
      this.idleRecoverTimer = performance.now()
      onFinish?.('unavailable')
      return
    }
    if (!noFinishReset) this.idleMotionInFlight = false
    const idx = fixedIdx !== undefined ? Math.min(size - 1, fixedIdx) : Math.floor(Math.random() * size)
    this.currentMotionId++
    const motionId = this.currentMotionId
    const selectedExpression = selectExpressionForMotion(
      this.presentationMetadata,
      group,
      idx,
      this.currentBaseSemantic(),
    )
    const isIdleLike = noFinishReset === true
    const isThink = requestedGroup === 'text_generating'
    const manager = (this.model.internalModel as any)?.motionManager
    let settled = false
    let motionStarted = false
    const notifyStart = (startedGroup?: unknown, startedIndex?: unknown) => {
      if (motionStarted || motionId !== this.currentMotionId) return
      if (typeof startedGroup === 'string' && startedGroup.toLowerCase() !== group.toLowerCase()) return
      if (typeof startedIndex === 'number' && startedIndex !== idx) return
      motionStarted = true
      onStart?.()
    }
    const finish = (outcome: MotionFinishOutcome = 'completed') => {
      if (settled || motionId !== this.currentMotionId) return
      const state = manager?.state
      // Ignore a stale success notification from an older SDK motion, but do
      // not let a failed start inherit that stale manager state forever.
      if (outcome === 'completed' && state?.currentGroup
        && (state.currentGroup !== group || state.currentIndex !== idx)) return
      settled = true
      this.motionFinishCleanup?.()
      this.motionFinishCleanup = null

      // Long-audio repeats are scheduled from the initial/previous motion's
      // actual finish boundary, exactly as in Pygame.  Audio metadata may
      // arrive earlier or later and must not move this 2.5s origin.
      if (this.longAudioActive && this.audioPlaying && this.activeSegment
        && this.activeSegment.group === requestedGroup) {
        this.longAudioNextMotionAt = performance.now() + LONG_AUDIO_REPEAT_DELAY_SECONDS * 1000
      }

      if (this.talkingActive && requestedGroup === 'talking_motion') {
        // Upstream starts talking_motion once and supplies no finish callback.
        // A natural SDK finish therefore leaves the talking presentation
        // active until an explicit stop_talking event.
        this.motionIsOver = false
        this.idleMotionInFlight = false
      } else if (isIdleLike) {
        // Keep the recovery pose held after its actual completion.  Upstream's
        // idle_motion has no finish callback and therefore never self-replays.
        this.motionIsOver = false
        this.idleMotionInFlight = false
      } else if (isThink) {
        this.thinkMotionIsOver = true
      } else {
        this.motionIsOver = true
        this.idleRecoverTimer = performance.now()
      }

      if (!isIdleLike && !(this.talkingActive && requestedGroup === 'talking_motion')) this._queueEyeOpen()
      onFinish?.(outcome)
      this.restoreBasePresentationIfIdle()
    }

    // V3 automatic expressions follow the upstream adapter's concrete-motion
    // policy. V2 keeps its already-selected base face while motions play.
    const expressionReady = this.presentationMetadata.runtimeKind === 'v3'
      ? this.applyExpression(selectedExpression.expression)
      : Promise.resolve()

    // The request id also prevents a delayed expression load from starting a
    // superseded motion.
    void expressionReady.then(() => {
      if (!this.modelLoaded || generation !== this.runtimeGeneration || motionId !== this.currentMotionId || this.ifBye && requestedGroup !== 'bye') return
      this.cancelMotionFinishListener()
      if (typeof manager?.on === 'function') {
        manager.on('motionStart', notifyStart)
        manager.on('motionFinish', finish)
        this.motionFinishCleanup = () => {
          try { manager.off?.('motionStart', notifyStart) } catch (_) { /* best effort */ }
          try { manager.off?.('motionFinish', finish) } catch (_) { /* best effort */ }
        }
      }
      this.model.motion(group, idx, priority).then((started: boolean) => {
        if (!this.modelLoaded || generation !== this.runtimeGeneration || motionId !== this.currentMotionId) return
        if (started === false) {
          notifyStart()
          finish('unavailable')
        }
        else {
          // pixi-live2d-display emits motionStart before its start promise
          // resolves. A test/runtime adapter without that event still gets
          // the same audio boundary once the motion request succeeds.
          notifyStart()
          this._resetEyeOpenTransition()
          // A successful start is not completion. This distinction is
          // essential for bye: only a real finish event may close the window.
          if (typeof manager?.on !== 'function' && requestedGroup !== 'bye') finish()
        }
      }).catch(() => finish('unavailable'))
    }).catch(() => finish('unavailable'))
  }

  private cancelMotionFinishListener(): void {
    this.motionFinishCleanup?.()
    this.motionFinishCleanup = null
  }

  private _queueEyeOpen(): void {
    this.eyeOpenPending = true
    this.eyeOpenTransitionActive = false
    this.eyeOpenStartTime = 0
  }

  private _resetEyeOpenTransition(): void {
    this.eyeOpenPending = false
    this.eyeOpenTransitionActive = false
    this.eyeOpenStartTime = 0
    this.eyeOpenStartL = 1
    this.eyeOpenStartR = 1
  }

  private updateEyeOpen(now: number): void {
    if (this.eyeOpenPending) {
      // Read on the first frame after finish. The motion's final parameter
      // values are authoritative; skip a reopen transition if the eyes are
      // already open, as the Pygame runtime does.
      this.eyeOpenStartL = this.parameters.get('PARAM_EYE_L_OPEN') ?? 1
      this.eyeOpenStartR = this.parameters.get('PARAM_EYE_R_OPEN') ?? 1
      this.eyeOpenPending = false
      if (this.eyeOpenStartL > 0.5) {
        this._resetEyeOpenTransition()
        return
      }
      this.eyeOpenStartTime = now
      this.eyeOpenTransitionActive = true
    }
    if (!this.eyeOpenTransitionActive) return
    const elapsed = now - this.eyeOpenStartTime
    if (elapsed >= EYE_OPEN_DURATION_MS) {
      this._resetEyeOpenTransition()
      this.parameters.set('PARAM_EYE_L_OPEN', 1)
      this.parameters.set('PARAM_EYE_R_OPEN', 1)
      return
    }
    const t = elapsed / EYE_OPEN_DURATION_MS
    const lerp = (s: number, e: number, f: number) => s + (e - s) * f
    this.parameters.set('PARAM_EYE_L_OPEN', lerp(this.eyeOpenStartL, 1, t))
    this.parameters.set('PARAM_EYE_R_OPEN', lerp(this.eyeOpenStartR, 1, t))
  }

  reset(): void {
    this.runtimeGeneration++
    this.presentationRequestId++
    this.pendingSegments.length = 0
    this.activeSegment = null
    this.currentAudioId++
    this.stopAudio()
    this.idleMotionInFlight = false
    this.motionIsOver = true
    this.thinkMotionIsOver = true
    this.turnMotionComplete = true
    const now = performance.now()
    this.idleRecoverTimer = now
    this.lastSavedTime = now
    this.lastThinkTime = now
    this.thinkInterval = THINK_INTERVAL_FIRST
    this._resetLongAudio()
    this._resetEyeOpenTransition()
    this.isThinking.value = false
    this.talkingActive = false
    this.textBubble.value = null
    this.userBubble.value = null
    this.eventQueue.length = 0
    this.ifBye = false
    this.byeComplete = false
    this.currentMotionId++
    if (this.modelKey.trim().toLowerCase() === 'sakiko' && this.presentationMetadata.runtimeKind === 'v2') {
      this.applyBasePresentation()
    }
  }

  private _resetLongAudio(): void {
    this.longAudioActive = false
    this.longAudioGroup = ''
    this.longAudioNextMotionAt = 0
    this.longAudioTriggeredCount = 0
  }

  private stopAudio(): void {
    if (this.currentAudio) { this.currentAudio.pause(); this.currentAudio = null; this.audioPlaying = false }
    this.mouthOpenValue = 0
    if (this._mouthParamIndex >= 0) this._rawSetMouth(0)
    if (this.analyserNode) { try { this.analyserNode.disconnect() } catch (_) {}; this.analyserNode = null }
  }

  // ── 最终参数边界（口型/眼睛） ──
  private _initFinalParameterBoundary(): void {
    this.finalParameterBoundaryCleanup?.()
    this.finalParameterBoundaryCleanup = null
    try {
      const internalModel = this.model.internalModel as any
      this._mouthParamIndex = this.parameters.index('PARAM_MOUTH_OPEN_Y')
      if (typeof internalModel?.on !== 'function') return
      const onBeforeModelUpdate = () => {
        // pixi-live2d-display has applied motion, expression, blink, breath,
        // physics, and pose before this event. Write the overrides here so
        // coreModel.update() and the following loadParameters()/Draw observe
        // the final values in the same frame.
        this.updateEyeOpen(performance.now())
        this._updateMouth()
      }
      internalModel.on('beforeModelUpdate', onBeforeModelUpdate)
      this.finalParameterBoundaryCleanup = () => {
        try { internalModel.off?.('beforeModelUpdate', onBeforeModelUpdate) } catch (_) { /* best effort */ }
      }
    } catch (e) { console.error('[StateMachine] Final parameter boundary init failed:', e) }
  }

  _updateMouth(): void {
    if (!this.audioPlaying || !this.analyserNode || this._mouthParamIndex < 0) {
      if (this.mouthOpenValue > 0.005) this.mouthOpenValue *= 0.85; else this.mouthOpenValue = 0
      this._rawSetMouth(this.mouthOpenValue)
      this.mouthSyncFrameCount += 1
      return
    }
    // Match upstream's `is_update_mouth_sync % 3 == 0`: sample the analyser
    // on the first frame, then every third frame, holding the envelope on the
    // two intervening frames.
    if (this.mouthSyncFrameCount % 3 === 0) {
      try {
        const bl = this.analyserNode.fftSize
        const d = new Float32Array(bl)
        this.analyserNode.getFloatTimeDomainData(d)
        let s = 0; for (let i = 0; i < bl; i++) s += d[i] * d[i]
        this.mouthOpenValue = Math.min(1, Math.sqrt(s / bl) * this.lipSyncN)
      } catch (_) {}
    }
    this._rawSetMouth(this.mouthOpenValue)
    this.mouthSyncFrameCount += 1
  }

  private _rawSetMouth(v: number): void {
    this.parameters.setByIndex(this._mouthParamIndex, v)
  }

  private _setupLipSync(audioEl: HTMLAudioElement, audioId: number): void {
    try {
      if (!this.audioContext) this.audioContext = new AudioContext()
      if (this.audioContext.state === 'suspended') this.audioContext.resume()
      if (this.analyserNode) { try { this.analyserNode.disconnect() } catch (_) {} }
      const src = this.audioContext.createMediaElementSource(audioEl)
      this.analyserNode = this.audioContext.createAnalyser()
      this.analyserNode.fftSize = 256
      src.connect(this.analyserNode)
      this.analyserNode.connect(this.audioContext.destination)
      this.mouthSyncFrameCount = 0
    } catch (e) { console.warn('[StateMachine] Lip sync setup failed:', e); this.analyserNode = null }
  }

  private _initBlinkBreath(): void {
    try {
      const im = this.model.internalModel as any
      if (im?.setAutoBlinkEnable) im.setAutoBlinkEnable(true)
      if (this.presentationMetadata.runtimeKind === 'v3') {
        // pixi-live2d-display's Cubism 4 default breath preset also drives
        // AngleX/Y/Z and BodyAngleX. WebUI/upstream only enables ParamBreath
        // for V3, so replace that preset before the next internal update.
        if (typeof im?.breath?.setParameters === 'function') {
          im.breath.setParameters([{
            parameterId: im.idParamBreath || 'ParamBreath',
            offset: 0.5,
            peak: 0.5,
            cycle: 3.2345,
            weight: 0.5,
          }])
        } else {
          console.warn('[StateMachine] V3 runtime has no ParamBreath-only API; automatic breath disabled')
        }
      } else if (im?.setAutoBreathEnable) {
        im.setAutoBreathEnable(true)
      }
    } catch (_) {}
  }
}
