import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import vm from 'node:vm'
import { fileURLToPath } from 'node:url'
import ts from 'typescript'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const sakikoV2FixturePath = path.join(root, 'test/fixtures/sakiko-costume-v2.model.json')

function transpile(relativePath) {
  return ts.transpileModule(fs.readFileSync(path.join(root, relativePath), 'utf8'), {
    compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.CommonJS },
  }).outputText
}

function evaluate(code, modules, globals) {
  const module = { exports: {} }
  vm.runInNewContext(code, {
    module,
    exports: module.exports,
    require: (specifier) => {
      if (!(specifier in modules)) throw new Error(`unexpected module import: ${specifier}`)
      return modules[specifier]
    },
    ...globals,
  })
  return module.exports
}

class FakeMotionManager {
  state = {}
  listeners = new Set()
  startListeners = new Set()
  stopCalls = 0

  on(event, listener) {
    if (event === 'motionFinish') this.listeners.add(listener)
    if (event === 'motionStart') this.startListeners.add(listener)
  }

  off(event, listener) {
    if (event === 'motionFinish') this.listeners.delete(listener)
    if (event === 'motionStart') this.startListeners.delete(listener)
  }

  emitFinish() {
    for (const listener of [...this.listeners]) listener()
  }

  emitStart(group, index) {
    this.state = { currentGroup: group, currentIndex: index }
    for (const listener of [...(this.startListeners || [])]) listener(group, index)
  }

  stopAllMotions() { this.stopCalls += 1 }
}

class FakeModel {
  expressions = []
  motions = []
  motionStarts = true
  deferMotionStart = false
  resolveMotion = null
  events = []
  mouthWrites = []
  boundaryListeners = new Set()
  autoBreathCalls = 0
  manager = new FakeMotionManager()
  internalModel = {
    motionManager: this.manager,
    coreModel: {
      update: (...args) => { this.events.push('core-update') },
      loadParameters: () => { this.events.push('load-parameters') },
    },
    breath: { setParameters: parameters => { this.events.push(`breath:${parameters[0]?.parameterId}`) } },
    idParamBreath: 'ParamBreath',
    setAutoBlinkEnable() {},
    setAutoBreathEnable: () => { this.autoBreathCalls += 1 },
    on: (event, listener) => { if (event === 'beforeModelUpdate') this.boundaryListeners.add(listener) },
    off: (event, listener) => { if (event === 'beforeModelUpdate') this.boundaryListeners.delete(listener) },
  }

  emitBeforeModelUpdate() {
    for (const listener of [...this.boundaryListeners]) listener()
  }

  runV3Frame() {
    this.events.push('motion/expression/blink/breath/physics/pose')
    this.emitBeforeModelUpdate()
    this.internalModel.coreModel.update()
    this.internalModel.coreModel.loadParameters()
    this.events.push('draw')
  }

  expression(id) {
    this.expressions.push(id)
    this.events.push(`expression:${id}`)
    return Promise.resolve(true)
  }

  motion(group, index, priority) {
    this.motions.push({ group, index, priority })
    this.events.push(`motion:${group}:${index}:${priority}`)
    if (this.motionStarts) this.manager.state = { currentGroup: group, currentIndex: index }
    if (this.deferMotionStart) {
      return new Promise(resolve => { this.resolveMotion = resolve })
    }
    return Promise.resolve(this.motionStarts)
  }
}

class FakeTicker {
  callbacks = new Set()

  add(callback) { this.callbacks.add(callback) }
  remove(callback) { this.callbacks.delete(callback) }
  tick() { for (const callback of [...this.callbacks]) callback() }
}

async function flushPresentation() {
  for (let index = 0; index < 20; index += 1) await Promise.resolve()
}

class FakeAudio {
  static instances = []
  listeners = new Map()
  duration = 0
  playCalls = 0
  pauseCalls = 0
  constructor() { FakeAudio.instances.push(this) }
  addEventListener(type, listener) {
    const current = this.listeners.get(type) || []
    current.push(listener)
    this.listeners.set(type, current)
  }
  emit(type) { for (const listener of this.listeners.get(type) || []) listener() }
  play() { this.playCalls += 1; return Promise.resolve() }
  pause() { this.pauseCalls += 1 }
}

class FakeAudioContext {
  state = 'running'
  resume() { return Promise.resolve() }
  createMediaElementSource() { return { connect() {} } }
  createAnalyser() {
    return {
      fftSize: 256,
      connect() {},
      disconnect() {},
      getFloatTimeDomainData(values) { values.fill(0) },
    }
  }
  close() {}
}

assert.ok(fs.existsSync(sakikoV2FixturePath), 'the portable Sakiko V2 manifest fixture is required')
const clock = { now: 0 }
const timers = new Map()
let nextTimerId = 0
let closeCount = 0
const globals = {
  performance: { now: () => clock.now },
  console,
  setTimeout: (callback, delay) => {
    const id = ++nextTimerId
    timers.set(id, { callback, delay })
    return id
  },
  clearTimeout: (id) => timers.delete(id),
  Audio: FakeAudio,
  AudioContext: FakeAudioContext,
  window: { electronAPI: { closeWindow: () => { closeCount += 1 } }, close: () => { closeCount += 1 } },
}
const constants = evaluate(transpile('src/renderer/statemachine/constants.ts'), {}, globals)
const policy = evaluate(transpile('src/renderer/statemachine/presentation-policy.ts'), {}, globals)
const stateMachineModule = evaluate(
  transpile('src/renderer/statemachine/Live2DStateMachine.ts'),
  {
    vue: { ref: (value) => ({ value }) },
    './constants': constants,
    './parameter-adapter': {
      createParameterAdapter: (model) => model.parameters || ({ index: () => -1, get: () => undefined, set() {}, setByIndex() {} }),
    },
    './presentation-policy': policy,
  },
  globals,
)
const { Live2DStateMachine } = stateMachineModule
const realSakikoV2 = policy.parseLive2DPresentationMetadata(
  JSON.parse(fs.readFileSync(sakikoV2FixturePath, 'utf8')),
)

// Deterministic V2 black-Sakiko lifecycle: real manifest, no synthetic model
// assets. The state machine must preserve serious during all V2 idle paths.
const ticker = new FakeTicker()
const model = new FakeModel()
const fsm = new Live2DStateMachine(model, ticker, 'sakiko', realSakikoV2, 'black')
// A first renderer initialization owns exactly one local entrance.
fsm.start({ initialEntrance: true })
await flushPresentation()
assert.equal(model.expressions.at(-1), 'serious', 'black Sakiko cold start must be serious')
assert.equal(model.motions.at(-1)?.group, 'change_character', 'cold start must request one entrance motion')
assert.equal(model.motions.at(-1)?.priority, 3, 'cold start entrance must use switch_live2d priority')
fsm.start()
await flushPresentation()
assert.equal(model.motions.filter(motion => motion.group === 'change_character').length, 1,
  'a single runtime must not replay its cold-start entrance')

// A switch creates a new runtime, but its pending business event owns the
// single entrance. The replacement FSM must not create a second cold-start
// transition before that event is drained.
const switchTicker = new FakeTicker()
const switchModel = new FakeModel()
const switchFsm = new Live2DStateMachine(switchModel, switchTicker, 'sakiko', realSakikoV2, 'black')
switchFsm.start({ initialEntrance: false })
await flushPresentation()
assert.equal(switchModel.motions.filter(motion => motion.group === 'change_character').length, 0,
  'a switch-created runtime must suppress the cold-start entrance')
switchFsm.pushEvent({ type: 'switch_live2d', data: {} })
switchTicker.tick()
await flushPresentation()
assert.equal(switchModel.motions.filter(motion => motion.group === 'change_character').length, 1,
  'the pending switch event must request exactly one entrance motion')

model.manager.emitFinish()
clock.now = 2501
ticker.tick()
await flushPresentation()
assert.equal(model.motions.at(-1)?.group, 'idle_motion', 'cold-start finish must reopen 2.5s idle recovery')
assert.equal(model.expressions.at(-1), 'serious', 'idle_motion must not replace black Sakiko serious')

// A completed business motion starts the upstream 2.5s idle-recovery window.
fsm.pushEvent({ type: 'emotion', data: { emotion: 'happiness', text: 'V2 emotion' } })
ticker.tick()
await flushPresentation()
model.manager.emitFinish()
clock.now = 5002
ticker.tick()
await flushPresentation()
assert.equal(model.motions.at(-1)?.group, 'idle_motion', 'recovery starts 2.5s after a motion finish')

clock.now = 10000
ticker.tick()
await flushPresentation()
assert.equal(model.motions.length, 4, 'idle_motion completion must not cause a local replay loop')

// A blocked 25s deadline is consumed rather than played later when unblocked.
fsm.idleRecoverTimer = clock.now
fsm.lastSavedTime = 0
fsm.turnMotionComplete = false
fsm.motionIsOver = false
fsm.idleMotionInFlight = true
clock.now = 25001
ticker.tick()
await flushPresentation()
assert.equal(model.motions.length, 4, 'blocked 25s deadline must not play IDLE')
assert.equal(fsm.lastSavedTime, 25001, 'blocked deadline must advance instead of becoming overdue')
fsm.turnMotionComplete = true
clock.now = 25002
ticker.tick()
await flushPresentation()
assert.equal(model.motions.length, 4, 'unblocking must not immediately backfill a skipped IDLE')
clock.now = 50002
ticker.tick()
await flushPresentation()
assert.equal(model.motions.at(-1)?.group, 'IDLE', 'the next eligible 25s deadline plays IDLE')
assert.equal(model.expressions.at(-1), 'serious', '25s IDLE must retain black Sakiko serious')
model.manager.emitFinish()
fsm.pushEvent({ type: 'emotion', data: { emotion: 'happiness', text: 'V2 emotion 2' } })
ticker.tick()
await flushPresentation()
assert.equal(model.motions.at(-1)?.group, 'happiness', 'the V2 regression must include an emotion motion')
assert.equal(model.expressions.at(-1), 'serious', 'V2 emotion must retain black Sakiko serious')
model.manager.emitFinish()
await flushPresentation()
assert.equal(model.expressions.at(-1), 'serious', 'V2 emotion completion must recover black Sakiko serious')
clock.now = 52503
ticker.tick()
await flushPresentation()
assert.equal(model.motions.at(-1)?.group, 'idle_motion', 'V2 emotion must return through normal idle recovery')
assert.equal(model.expressions.at(-1), 'serious', 'V2 idle recovery must still retain black Sakiko serious')

// A model without an entrance group must immediately release the normal
// local recovery gate rather than leaving its first frame permanently static.
const missingEntranceMetadata = policy.parseLive2DPresentationMetadata({
  FileReferences: { Motions: { idle_motion: [{ File: 'motions/idle.motion3.json' }] }, Expressions: [] },
})
const missingEntranceTicker = new FakeTicker()
const missingEntranceModel = new FakeModel()
const missingEntranceFsm = new Live2DStateMachine(
  missingEntranceModel, missingEntranceTicker, 'anon', missingEntranceMetadata,
)
clock.now = 0
missingEntranceFsm.start()
await flushPresentation()
assert.equal(missingEntranceModel.motions.length, 0, 'missing entrance group must not invent a fallback motion')
assert.equal(missingEntranceFsm.motionIsOver, true, 'missing entrance group must complete the local motion gate')
clock.now = 2501
missingEntranceTicker.tick()
await flushPresentation()
assert.equal(missingEntranceModel.motions.at(-1)?.group, 'idle_motion',
  'missing entrance group must still reach normal idle recovery')

// Non-Sakiko clicks are deliberately ignored: no extra IDLE or gaze cue.
const nonSakikoTicker = new FakeTicker()
const nonSakikoModel = new FakeModel()
const nonSakikoFsm = new Live2DStateMachine(nonSakikoModel, nonSakikoTicker, 'anon', realSakikoV2)
nonSakikoFsm.start()
await flushPresentation()
const nonSakikoMotionCount = nonSakikoModel.motions.length
assert.equal(nonSakikoModel.expressions.length, 0, 'ordinary cold start must not force an idle expression')
nonSakikoFsm.handleClick(50, 100)
nonSakikoTicker.tick()
await flushPresentation()
assert.equal(nonSakikoModel.motions.length, nonSakikoMotionCount, 'cold-start click adds no cue for non-Sakiko')

// Talking uses the Pygame priority and must preempt local presentation.
fsm.pushEvent({ type: 'talking', data: { active: true } })
ticker.tick()
await flushPresentation()
assert.equal(model.motions.at(-1)?.priority, 4, 'talking_motion priority must match upstream')

// A no-audio FIFO may advance immediately, but its current V3 emotion motion
// retains the selected expression after finish; upstream has no base-recovery
// lifecycle at this boundary.
const v3Metadata = policy.parseLive2DPresentationMetadata({
  FileReferences: {
    Motions: {
      happiness: [{ File: 'motions/motion_smile01.motion3.json' }],
      happiness_C: [{ File: 'motions/motion_smile02_C.motion3.json' }],
      text_generating_C: [{ File: 'motions/mtn_thinking01_C.motion3.json' }],
    },
    Expressions: [
      { Name: 'exp_smile01', File: 'expressions/smile.exp3.json' },
      { Name: 'exp_idle01', File: 'expressions/idle.exp3.json' },
      { Name: 'exp_thinking01', File: 'expressions/thinking.exp3.json' },
    ],
  },
})
const noAudioTicker = new FakeTicker()
const noAudioModel = new FakeModel()
const noAudioFsm = new Live2DStateMachine(noAudioModel, noAudioTicker, 'anon', v3Metadata)
noAudioFsm.start()
await flushPresentation()
assert.deepEqual(noAudioModel.events.filter(event => event.startsWith('breath:')), ['breath:ParamBreath'],
  'V3 automatic breath must be restricted to ParamBreath')
assert.equal(noAudioModel.autoBreathCalls, 0, 'V3 must not enable the full default breath preset')
noAudioFsm.pushEvent({ type: 'assistant_segment', data: { emotion: 'happiness', text: 'no audio', audio_url: '' } })
noAudioTicker.tick()
await flushPresentation()
assert.equal(noAudioModel.motions.at(-1)?.group, 'happiness_C', 'V3 must prefer the center-position motion group')
assert.equal(noAudioModel.expressions.at(-1), 'exp_smile01', 'unfinished no-audio emotion must keep its selected face')
noAudioModel.manager.emitFinish()
await flushPresentation()
assert.equal(noAudioModel.expressions.at(-1), 'exp_smile01', 'V3 emotion finish must retain its auto-expression')

// Thinking cadence mirrors Pygame: the first cue is eligible after one
// second, then subsequent cues are spaced by fifteen seconds from the prior
// request.  A finish callback only unlocks the next cue; it does not reset the
// cadence origin.
clock.now = 0
const thinkingTicker = new FakeTicker()
const thinkingModel = new FakeModel()
const thinkingFsm = new Live2DStateMachine(thinkingModel, thinkingTicker, 'anon', v3Metadata)
thinkingFsm.start()
await flushPresentation()
thinkingFsm.pushEvent({ type: 'text_generating', data: { active: true } })
thinkingTicker.tick()
await flushPresentation()
clock.now = 1000
thinkingTicker.tick()
await flushPresentation()
assert.equal(thinkingModel.motions.length, 0, 'first thinking cue must wait one second')
clock.now = 1001
thinkingTicker.tick()
await flushPresentation()
assert.equal(thinkingModel.motions.at(-1)?.group, 'text_generating_C', 'first thinking cue uses the center motion group')
thinkingModel.manager.emitFinish()
assert.equal(thinkingModel.expressions.at(-1), 'exp_thinking01', 'thinking finish must retain its auto-expression')
clock.now = 16000
thinkingTicker.tick()
await flushPresentation()
assert.equal(thinkingModel.motions.length, 1, 'subsequent thinking cue must wait fifteen seconds')
clock.now = 16002
thinkingTicker.tick()
await flushPresentation()
assert.equal(thinkingModel.motions.length, 2, 'subsequent thinking cue starts at the fifteen-second boundary')
thinkingModel.manager.emitFinish()
thinkingFsm.pushEvent({ type: 'text_generating', data: { active: false } })
thinkingTicker.tick()
thinkingFsm.pushEvent({ type: 'text_generating', data: { active: true } })
thinkingTicker.tick()
clock.now = 17103
thinkingTicker.tick()
await flushPresentation()
assert.equal(thinkingModel.motions.length, 2, 'a later conversation must not restore the one-second thinking interval')
clock.now = 31003
thinkingTicker.tick()
await flushPresentation()
assert.equal(thinkingModel.motions.length, 3, 'later conversations retain the fifteen-second thinking cadence')
thinkingFsm.reset()
thinkingFsm.pushEvent({ type: 'text_generating', data: { active: true } })
thinkingTicker.tick()
clock.now = 32003
thinkingTicker.tick()
await flushPresentation()
assert.equal(thinkingModel.motions.length, 3, 'an FSM reset still waits through the first one-second boundary')
clock.now = 32004
thinkingTicker.tick()
await flushPresentation()
assert.equal(thinkingModel.motions.length, 4, 'only an FSM reset restores the one-second thinking interval')

// Sakiko conversion follows Pygame's NORMAL priority and ordering: the
// transition request is issued before the new role base expression.
const conversionTicker = new FakeTicker()
const conversionModel = new FakeModel()
const conversionFsm = new Live2DStateMachine(conversionModel, conversionTicker, 'sakiko', realSakikoV2, 'black')
conversionFsm.start()
await flushPresentation()
conversionModel.events.length = 0
conversionFsm.pushEvent({ type: 'sakiko_state', data: { value: 'white' } })
conversionTicker.tick()
await flushPresentation()
assert.equal(conversionModel.motions.at(-1)?.priority, 2, 'Sakiko conversion must use priority 2')
const conversionMotionEvent = conversionModel.events.findIndex(event => event.startsWith('motion:change_character:') && event.endsWith(':2'))
assert.ok(conversionMotionEvent >= 0)
assert.ok(conversionModel.events.indexOf('expression:idle') > conversionMotionEvent,
  'conversion expression must follow the motion request')

// Sakiko costume/mask conversion is V2-only. A V3 Sakiko business fact may
// select a new model in App.vue, but must not run the V2 special FSM locally.
const v3SakikoTicker = new FakeTicker()
const v3SakikoModel = new FakeModel()
const v3SakikoFsm = new Live2DStateMachine(v3SakikoModel, v3SakikoTicker, 'sakiko', v3Metadata, 'black')
v3SakikoFsm.start()
await flushPresentation()
v3SakikoModel.motions.length = 0
v3SakikoModel.expressions.length = 0
v3SakikoFsm.pushEvent({ type: 'sakiko_state', data: { value: 'white' } })
v3SakikoTicker.tick()
await flushPresentation()
assert.equal(v3SakikoModel.motions.length, 0, 'V3 Sakiko must not run V2 conversion motions')
assert.equal(v3SakikoModel.expressions.length, 0, 'V3 Sakiko conversion must not invent a base expression')

// No unsupported V3 base expression may reset a live face as a lifecycle side
// effect.  The upstream adapter simply leaves the current expression intact.
const noBaseMetadata = policy.parseLive2DPresentationMetadata({
  FileReferences: {
    Motions: { happiness_C: [{ File: 'motions/mtn_smile01_C.motion3.json' }] },
    Expressions: [],
  },
})
const noBaseTicker = new FakeTicker()
const noBaseModel = new FakeModel()
let resetCount = 0
noBaseModel.internalModel.motionManager.expressionManager = { resetExpression: () => { resetCount += 1 } }
const noBaseFsm = new Live2DStateMachine(noBaseModel, noBaseTicker, 'anon', noBaseMetadata)
noBaseFsm.start()
await flushPresentation()
noBaseFsm.pushEvent({ type: 'assistant_segment', data: { emotion: 'happiness', text: 'none' } })
noBaseTicker.tick()
await flushPresentation()
assert.equal(resetCount, 0, 'V3 without a supported expression must not invoke base reset')

// Lip sync is written at the V3 beforeModelUpdate final boundary, uses the
// visible Electron gain, and samples frames 0, 3, ... while holding the value.
const lipTicker = new FakeTicker()
const lipModel = new FakeModel()
lipModel.parameters = {
  index: id => id === 'PARAM_MOUTH_OPEN_Y' ? 0 : -1,
  get: () => undefined,
  set() {},
  setByIndex: (_index, value) => { lipModel.events.push(`mouth:${value}`) },
}
const lipMetadata = policy.parseLive2DPresentationMetadata({ FileReferences: { Motions: {}, Expressions: [] } })
const lipFsm = new Live2DStateMachine(lipModel, lipTicker, 'anon', lipMetadata)
lipFsm.start()
await flushPresentation()
let analyserReads = 0
lipFsm.audioPlaying = true
lipFsm.analyserNode = {
  fftSize: 4,
  getFloatTimeDomainData: values => { analyserReads += 1; values.fill(0.5) },
}
for (let frame = 0; frame < 4; frame += 1) {
  lipModel.runV3Frame()
}
assert.equal(analyserReads, 2, 'mouth analyser must be sampled every third frame')
assert.deepEqual(lipModel.events.slice(-20), [
  'motion/expression/blink/breath/physics/pose', 'mouth:0.95', 'core-update', 'load-parameters', 'draw',
  'motion/expression/blink/breath/physics/pose', 'mouth:0.95', 'core-update', 'load-parameters', 'draw',
  'motion/expression/blink/breath/physics/pose', 'mouth:0.95', 'core-update', 'load-parameters', 'draw',
  'motion/expression/blink/breath/physics/pose', 'mouth:0.95', 'core-update', 'load-parameters', 'draw',
].slice(-20), 'mouth must be written before Core.update and loadParameters')

// Electron subtitles prefer the backend's Chinese translation and never
// append the source-language line when a translation is available.
const subtitleTicker = new FakeTicker()
const subtitleModel = new FakeModel()
const subtitleFsm = new Live2DStateMachine(subtitleModel, subtitleTicker, 'anon')
subtitleFsm.start()
await flushPresentation()
subtitleFsm.pushEvent({ type: 'assistant_segment', data: {
  emotion: 'happiness', text: 'こんにちは', translation: '你好', audio_url: '',
} })
subtitleTicker.tick()
await flushPresentation()
assert.equal(subtitleFsm.textBubble.value, '你好', 'Electron subtitle must show Chinese translation only')
subtitleFsm.pushEvent({ type: 'assistant_segment', data: {
  emotion: 'happiness', text: 'こんにちは', translation: '', audio_url: '',
} })
subtitleTicker.tick()
await flushPresentation()
assert.equal(subtitleFsm.textBubble.value, null, 'Electron subtitle must hide untranslated Japanese text')
subtitleFsm.pushEvent({ type: 'assistant_segment', data: {
  emotion: 'happiness', text: 'hello', translation: '', audio_url: '',
} })
subtitleTicker.tick()
await flushPresentation()
assert.equal(subtitleFsm.textBubble.value, null, 'Electron subtitle must hide untranslated English text')
subtitleFsm.pushEvent({ type: 'assistant_segment', data: {
  emotion: 'happiness', text: '你好', translation: '', audio_url: '',
} })
subtitleTicker.tick()
await flushPresentation()
assert.equal(subtitleFsm.textBubble.value, '你好', 'Electron subtitle must show Chinese source text')
subtitleFsm.pushEvent({ type: 'assistant_segment', data: {
  emotion: 'happiness', text: 'hello', translation: 'hello', audio_url: '',
} })
subtitleTicker.tick()
await flushPresentation()
assert.equal(subtitleFsm.textBubble.value, null, 'Electron subtitle must reject non-Chinese translations')

// Cancel stops the current audio and clears the pending FIFO without cutting
// off the motion already visible in the model.
const cancelTicker = new FakeTicker()
const cancelModel = new FakeModel()
const cancelFsm = new Live2DStateMachine(cancelModel, cancelTicker, 'anon')
cancelFsm.start()
await flushPresentation()
cancelFsm.pushEvent({ type: 'assistant_segment', data: { emotion: 'happiness', text: 'cancel', audio_url: 'file:///cancel.wav' } })
cancelTicker.tick()
await flushPresentation()
cancelModel.manager.emitStart('happiness', cancelModel.motions.at(-1).index)
await flushPresentation()
cancelFsm.pushEvent({ type: 'cancel', data: {} })
cancelTicker.tick()
await flushPresentation()
assert.equal(cancelModel.manager.stopCalls, 0, 'cancel must not StopAllMotions')
assert.equal(FakeAudio.instances.at(-1).pauseCalls, 1, 'cancel must stop active TTS audio')

// Talking is a one-shot presentation. A natural finish does not replay it or
// mark the motion as normally completed; only stop_talking closes the state.
const talkingTicker = new FakeTicker()
const talkingModel = new FakeModel()
const talkingFsm = new Live2DStateMachine(talkingModel, talkingTicker, 'anon')
talkingFsm.start()
await flushPresentation()
talkingFsm.pushEvent({ type: 'talking', data: { active: true } })
talkingTicker.tick()
await flushPresentation()
assert.equal(talkingModel.motions.length, 2, 'talking must follow the one cold-start entrance')
assert.equal(talkingModel.motions.at(-1)?.priority, 4)
talkingModel.manager.emitFinish()
talkingTicker.tick()
await flushPresentation()
assert.equal(talkingModel.motions.length, 2, 'talking motion must not replay after natural finish')
talkingFsm.pushEvent({ type: 'talking', data: { active: false } })
talkingTicker.tick()
await flushPresentation()
assert.equal(talkingModel.manager.stopCalls, 0, 'stop_talking must not StopAllMotions')
assert.equal(talkingModel.expressions.length, 0, 'stop_talking must not reset expression')

// Missing idle_motion must fail instead of falling back to IDLE, and clicks
// request IDLE only instead of falling back to idle_motion.
const noIdleMetadata = policy.parseLive2DPresentationMetadata({
  FileReferences: { Motions: { IDLE: [{ File: 'motions/idle_C.motion3.json' }] }, Expressions: [] },
})
const noIdleTicker = new FakeTicker()
const noIdleModel = new FakeModel()
const noIdleFsm = new Live2DStateMachine(noIdleModel, noIdleTicker, 'sakiko', noIdleMetadata, 'black')
noIdleFsm.start()
await flushPresentation()
noIdleFsm.motionIsOver = true
noIdleFsm.idleRecoverTimer = 0
clock.now = 3000
noIdleTicker.tick()
await flushPresentation()
assert.equal(noIdleModel.motions.length, 0, 'idle recovery must not fall back to IDLE')
noIdleFsm.handleClick(50, 100)
noIdleTicker.tick()
await flushPresentation()
assert.equal(noIdleModel.motions.length, 1, 'Sakiko click must request IDLE directly')

// Audio begins at motionStart, not when the motion request is still loading;
// long-audio repeat timing starts at motion finish.
FakeAudio.instances.length = 0
clock.now = 100
const boundaryTicker = new FakeTicker()
const boundaryModel = new FakeModel()
boundaryModel.deferMotionStart = true
const boundaryFsm = new Live2DStateMachine(boundaryModel, boundaryTicker, 'anon', v3Metadata)
boundaryFsm.start()
await flushPresentation()
boundaryFsm.pushEvent({ type: 'assistant_segment', data: { emotion: 'happiness', text: 'audio boundary', audio_url: 'file:///same.wav' } })
boundaryTicker.tick()
await flushPresentation()
assert.equal(boundaryModel.motions.at(-1)?.group, 'happiness_C')
assert.equal(FakeAudio.instances.length, 0, 'audio must wait for motionStart')
boundaryModel.manager.emitStart('happiness_C', boundaryModel.motions.at(-1).index)
await flushPresentation()
assert.equal(FakeAudio.instances.length, 1, 'motionStart must begin the segment audio')
const boundaryAudio = FakeAudio.instances[0]
boundaryAudio.duration = 8
boundaryAudio.emit('loadedmetadata')
boundaryModel.resolveMotion(true)
await flushPresentation()
boundaryModel.manager.emitFinish()
assert.equal(boundaryFsm.longAudioNextMotionAt, 2600, 'long-audio delay must begin at motion finish')
assert.equal(boundaryFsm.idleRecoverTimer, 100, 'idle recovery origin must be the motion finish')
boundaryAudio.emit('ended')
assert.equal(boundaryFsm.idleRecoverTimer, 100, 'audio completion must not move the motion-based idle origin')

// Bye must remain visible until the runtime reports motion completion.
const byeTicker = new FakeTicker()
const byeModel = new FakeModel()
const byeFsm = new Live2DStateMachine(byeModel, byeTicker, 'sakiko', realSakikoV2, 'black')
byeFsm.start()
await flushPresentation()
byeFsm.pushEvent({ type: 'bye', data: {} })
byeTicker.tick()
await flushPresentation()
assert.equal(byeModel.motions.at(-1)?.group, 'bye')
assert.equal(closeCount, 0, 'bye must not close immediately or after a fixed startup delay')
assert.equal([...timers.values()].at(-1)?.delay, constants.BYE_TIMEOUT_MS, 'timeout is only a watchdog')
byeModel.manager.emitFinish()
await flushPresentation()
assert.equal(closeCount, 1, 'bye closes when its motion completion callback fires')
assert.equal(timers.size, 0, 'normal bye completion must cancel the watchdog')

// A start promise is not a bye completion when the adapter has no finish event.
const noCallbackTicker = new FakeTicker()
const noCallbackModel = new FakeModel()
noCallbackModel.manager.on = undefined
const noCallbackFsm = new Live2DStateMachine(noCallbackModel, noCallbackTicker, 'sakiko', realSakikoV2, 'black')
noCallbackFsm.start()
await flushPresentation()
noCallbackFsm.pushEvent({ type: 'bye', data: {} })
noCallbackTicker.tick()
await flushPresentation()
assert.equal(closeCount, 1, 'bye start without completion event must wait for watchdog fallback')

console.log('Live2D FSM V2/V3 idle cadence, presentation, audio, and bye completion checks passed.')
