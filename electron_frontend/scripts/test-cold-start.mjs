import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const app = fs.readFileSync(path.join(root, 'src/renderer/App.vue'), 'utf8')
const stage = fs.readFileSync(path.join(root, 'src/renderer/components/Live2DStage.vue'), 'utf8')
const stateMachine = fs.readFileSync(path.join(root, 'src/renderer/statemachine/Live2DStateMachine.ts'), 'utf8')
const constants = fs.readFileSync(path.join(root, 'src/renderer/statemachine/constants.ts'), 'utf8')
const qtUi = fs.readFileSync(path.join(root, '..', 'GPT_SoVITS/qtUI.py'), 'utf8')
const mainProcess = fs.readFileSync(path.join(root, 'src/main/index.ts'), 'utf8')
const controlsIsland = fs.readFileSync(path.join(root, 'src/renderer/components/controls-island/index.vue'), 'utf8')
const rendererMain = fs.readFileSync(path.join(root, 'src/renderer/main.ts'), 'utf8')
const viteConfig = fs.readFileSync(path.join(root, 'electron.vite.config.ts'), 'utf8')

assert.match(app, /<Live2DStage[\s\S]*:key=/, 'App must mount Live2DStage during cold start')
assert.match(app, /initial_model/, 'App must wait for the backend initial model business event')
assert.match(app, /desktopLayoutFromBridge\(data\.layout\), event\.type === 'initial_model'/,
  'only the first renderer initialization may request a local cold-start entrance')
assert.match(app, /:initial-entrance="initialEntrance"/,
  'App must pass the model mount reason into the new Live2D stage')
assert.match(app, /v-if="modelPath"/, 'App must not invent a bundled model path')
assert.match(stage, /Live2DStateMachine/, 'Stage must create the Electron-local state machine')
assert.match(stage, /pixi-live2d-display/, 'Stage must use the Pixi Live2D runtime')
assert.match(stage, /config\.sound = false/, 'Pixi manifest Sound must stay disabled for Electron-owned audio')
assert.match(stage, /idleMotionGroup:\s*'__dsakiko_electron_idle__'/,
  'Pixi must not request its own idle motion')
assert.match(stage, /FileReferences/, 'Stage must read Cubism 3\/4 presentation metadata')
assert.match(stage, /parseLive2DPresentationMetadata/, 'Stage must preserve manifest motion/expression capability data')
assert.match(stage, /layout\?\.offset_x/, 'Stage must apply upstream desktop layout offsets when supplied')
assert.match(stage, /displayedWidth[\s\S]*layout\?\.offset_x/,
  'desktop horizontal offsets must use the displayed model bounds')
assert.match(stage, /displayedHeight[\s\S]*layout\?\.offset_y/,
  'desktop layout offsets must map from the displayed model bounds, not the viewport')
assert.match(stage, /const v3ScaleTune = 0\.07[\s\S]*const v3XTune = 0[\s\S]*const v3YTune = -290/,
  'V3 global display calibration must expose scale, x, and y values together')
const resizePaths = stage.match(/if \(runtimeKind === 'v3'\) \{([\s\S]*?)\n      \} else \{([\s\S]*?)\n      \}/)
assert.ok(resizePaths, 'Stage must keep separate V3 and V2 resize paths')
const [, v3ResizePath, v2ResizePath] = resizePaths
assert.match(v3ResizePath, /model\.scale\.set\(layout\.scale\)[\s\S]*model\.scale\.set\(model\.scale\.x \* v3ScaleTune\)/,
  'V3 calibration scale must be applied after its original desktop layout scale')
assert.match(v3ResizePath, /model\.scale\.set\(model\.scale\.x \* v3ScaleTune\)[\s\S]*displayedWidth = model\.width[\s\S]*displayedHeight = model\.height/,
  'V3 offsets must use bounds recomputed after global scale calibration')
assert.match(v3ResizePath, /displayedWidth[\s\S]*v3XTune[\s\S]*displayedHeight[\s\S]*v3YTune/,
  'V3 global x/y calibration must be applied after layout offsets')
assert.match(v3ResizePath, /model\.scale\.set\(2\.3 \* ratio\)/,
  'V3 without layout metadata must use the V2 reference-envelope fallback')
assert.doesNotMatch(v2ResizePath, /v3(?:Scale|X|Y)Tune/,
  'V2 resize behavior must not use V3 global calibration values')
assert.match(v2ResizePath, /model\.scale\.set\(baseScale \* ratio\)[\s\S]*model\.x = width \/ 2 \+ \(layout\?\.offset_x \?\? 0\) \* displayedWidth \/ 2[\s\S]*model\.y = height \/ 2 - \(layout\?\.offset_y \?\? 0\) \* displayedHeight \/ 2/,
  'V2 must retain its existing scale and upstream offset mapping')
assert.match(stage, /destroyLive2DModel/, 'Stage must release a replaced model after unmount/load races')
assert.match(stage, /app\.stage\.addChild\(model\)[\s\S]*await waitForFirstRenderedFrame\(app\)[\s\S]*stateMachine\.start\(\{ initialEntrance: props\.initialEntrance !== false \}\)/,
  'the FSM must start only after the model has completed one Pixi render, with the mount-specific entrance policy')
assert.match(stage, /renderer\.once\('postrender', resolve\)/,
  'cold start must wait for Pixi postrender rather than a pre-render ticker callback or a timed delay')
assert.doesNotMatch(stage, /waitForFirstRenderedFrame[\s\S]*setTimeout/,
  'the render-ready boundary must not be a magic startup timeout')
assert.match(stage, /\} catch \(error\) \{\s*\/\/ The parent may already have mounted B[\s\S]*?if \(disposed\) return\s*console\.error\('\[Live2DStage\] model load failed:'/,
  'a disposed Stage must not emit a late A model error after B has mounted')
assert.match(stateMachine, /pendingSegments/, 'Electron FSM must own assistant segment FIFO')
assert.match(stateMachine, /idleMotionInFlight/, 'idle recovery must not retrigger while a motion is active')
assert.match(stateMachine, /selectExpressionForMotion/, 'Electron FSM must choose presentation before each local motion')
assert.match(stateMachine, /applyBasePresentation/, 'Electron FSM must retain explicit base-presentation changes')
assert.match(stateMachine, /lipSyncN = 1\.9/, 'Electron lip sync gain must use the final calibrated value')
assert.match(stateMachine, /updateEyeOpen\(performance\.now\(\)\)[\s\S]*?_updateMouth\(\)/,
  'mouth and eye parameters must be written together at the final boundary')
assert.match(stateMachine, /internalModel\.on\('beforeModelUpdate'[\s\S]*?_updateMouth\(\)/,
  'final parameter overrides must use the internalModel beforeModelUpdate boundary')
assert.doesNotMatch(stateMachine, /coreModel\.update\s*=|const originalUpdate = coreModel\.update/,
  'Electron must not monkey-patch coreModel.update for final parameters')
assert.doesNotMatch(stateMachine, /stopAllMotions\(\)/,
  'Electron presentation must not unconditionally stop visible motions')
assert.match(stateMachine, /mouthSyncFrameCount % 3 === 0/, 'mouth analyser cadence must match upstream')
assert.match(stateMachine, /manager\.on\('motionStart'/, 'segment audio must have a motion-start boundary')
assert.doesNotMatch(stateMachine, /resetExpression\(\)/, 'V3 must not invent a base-reset lifecycle')
assert.match(stateMachine, /runtimeKind !== 'v2'/, 'V3 cue completion/base recovery must stay separate from V2')
assert.match(stateMachine, /breath\.setParameters[\s\S]*ParamBreath/, 'V3 automatic breath must use ParamBreath only')
assert.doesNotMatch(stateMachine, /checkTalking|preferredMotionGroup/, 'talking and motion selection must not replay/fallback locally')
assert.match(stateMachine, /sakikoMaskOn/, 'Electron FSM must retain local Sakiko mask state across toggle events')
assert.match(stateMachine, /nextSakikoMaskTransition/, 'Electron FSM must alternate mask-off and mask-on motions')
assert.match(stateMachine, /selectBlackSakikoEntry/, 'Electron FSM must locally randomize black Sakiko entry motion')
assert.match(stateMachine, /modelKey\.trim\(\)\.toLowerCase\(\) !== 'sakiko'/,
  'ordinary cold starts must not force an idle expression')
assert.match(stateMachine, /if \(options\.initialEntrance !== false\) this\.startColdStartEntrance\(\)/,
  'a switch-created FSM must be able to suppress only its local cold-start entrance')
assert.match(constants, /LONG_AUDIO_REPEAT_DELAY_SECONDS = 2\.5/, 'long-audio delay must match Pygame')
assert.doesNotMatch(app, /renderer[_-](?:id|fact|snapshot)/i, 'App must not implement renderer routing')
assert.doesNotMatch(stage, /renderer[_-](?:id|fact|snapshot)/i, 'Stage must not implement renderer routing')
assert.doesNotMatch(app, /presentationPolicy/, 'the camelCase duplicate policy must not be imported')
assert.doesNotMatch(stage, /presentationPolicy/, 'the camelCase duplicate policy must not be imported')
assert.match(app, /electron_hello/, 'Electron must announce its business-event capabilities')
assert.doesNotMatch(app, /renderer_instance_id/, 'Electron must not route through renderer identity bookkeeping')
assert.match(app, /renderer_recovery/, 'Electron must explicitly recover local presentation after a refreshed active turn')
assert.doesNotMatch(app, /recover_active_turn/, 'renderer reconnect must use the exact bridge turn, not a renderer-owned replay flag')
assert.match(app, /if \(!bridgeReady\.value\) return/, 'business events must not enter renderer presentation before bridge readiness')
assert.match(app, /event\.type === 'bye'[\s\S]*electronAPI\.closeWindow/, 'bye must close Electron even after model loading failed')
assert.match(app, /modelLoadFailure/, 'a failed model load must clear pending presentation instead of accumulating without an FSM')
assert.match(app, /interface SubtitleSettings[\s\S]*enabled: boolean[\s\S]*fontSize: number[\s\S]*bottomOffset: number[\s\S]*maxWidth: number[\s\S]*textColor: string[\s\S]*backgroundColor: string[\s\S]*backgroundOpacity: number/,
  'subtitle settings must remain Electron-local and complete')
assert.match(app, /const SUBTITLE_SETTINGS_KEY = 'saki-subtitle-settings-v1'/,
  'subtitle settings must persist under a renderer-local storage key')
assert.match(app, /enabled: true,[\s\S]*fontSize: 16,[\s\S]*bottomOffset: 64,[\s\S]*maxWidth: 80,[\s\S]*textColor: '#D4D4D4',[\s\S]*backgroundColor: '#262626',[\s\S]*backgroundOpacity: 0\.8/,
  'subtitle defaults must preserve the former assistant subtitle appearance')
assert.match(app, /fontSize: clamp\(stored\.fontSize, 12, 28[\s\S]*bottomOffset: clamp\(stored\.bottomOffset, 24, 160[\s\S]*maxWidth: clamp\(stored\.maxWidth, 40, 95[\s\S]*backgroundOpacity: clamp\(stored\.backgroundOpacity, 0, 0\.95/,
  'malformed persisted subtitle numeric values must be clamped')
assert.match(app, /v-if="subtitleSettings\.enabled && textBubble"/,
  'disabling subtitles must only hide the assistant bubble, not mutate FSM text')
assert.match(app, /function sendUiIntent\(intent: string\): boolean[\s\S]*if \(!bridgeReady\.value \|\| ws\?\.readyState !== WebSocket\.OPEN\) return false/,
  'UI intents must report bridge readiness before changing renderer state')
assert.match(controlsIsland, /if \(!sendUiIntent\('start_voice_input'\)\) return[\s\S]*isVoiceRecording\.value = true/,
  'the microphone must only enter recording after a UI intent is accepted')
assert.match(controlsIsland, /bridgeReady \? '按住录音，松开后识别' : '后端未连接'/,
  'the microphone must disclose a disconnected backend')
assert.match(controlsIsland, /v-if="isDevelopment"[\s\S]*toggleDevToolsHandler/,
  'DevTools must be development-only')
assert.match(controlsIsland, /@click="hideWindow\(\)"[\s\S]*隐藏到托盘/,
  'the panel must expose the existing hide-to-tray IPC')
assert.match(controlsIsland, /退出 Electron/,
  'the explicit Electron exit action must be named accurately')
assert.match(app, /lastModelSelection[\s\S]*function retryModel\(\)[\s\S]*reloadModel\(/,
  'model failure recovery must retain a local retry selection')
assert.match(app, /modelPath\.value = ''/,
  'a failed Stage must unmount while retaining retry metadata separately')
assert.match(controlsIsland, /subtitle-preview[\s\S]*:style="subtitlePreviewStyle"/,
  'subtitle controls must show a live local preview while values are adjusted')
assert.match(controlsIsland, /subtitleStylesExpanded[\s\S]*字幕样式[\s\S]*v-if="subtitleSettings\.enabled && subtitleStylesExpanded"/,
  'subtitle detail controls must be folded behind a dedicated style section')
assert.match(controlsIsland, /class="toggle-switch"[\s\S]*v-model="subtitleSettings\.enabled"[\s\S]*toggle-track[\s\S]*toggle-thumb/,
  'subtitle visibility must use a toggle switch control')
assert.match(controlsIsland, /settings-panel bg-neutral-100\/95[\s\S]*dark:bg-neutral-900\/95/,
  'settings panel background must stay readable and visually aligned with the controls shell')
assert.match(qtUi, /intent == "open_python_settings"[\s\S]*os\.path\.join\(script_dir, "dsakiko_configuration\.py"\)[\s\S]*"DSakikoConfigArea"/,
  'Electron Python settings intent must launch the Qt settings window through an absolute project path')
assert.match(controlsIsland, /expanded-panel[\s\S]*settings-panel[\s\S]*controls-shell/,
  'the expandable settings panel and compact controls shell must be separate layout regions')
assert.match(controlsIsland, /\.expanded-panel \{ width: max-content[\s\S]*\.settings-panel \{[\s\S]*width: min\(22rem/,
  'only the settings panel may expand to settings width')
assert.match(controlsIsland, /\.settings-content \{[\s\S]*flex: 1 1 auto;[\s\S]*overflow-y: auto/,
  'settings content must scroll within its flex-constrained panel')
assert.doesNotMatch(controlsIsland, /max-height: calc\(100vh - (5\.5|14)rem\)/,
  'settings layout must not depend on magic viewport height offsets')
assert.match(controlsIsland, /@media \(max-width: 260px\)[\s\S]*\.setting-row input\[type='range'\] \{ width: 100%/,
  'narrow windows must stack range inputs without horizontal overflow')
assert.match(controlsIsland, /bottom: `\$\{8 \+ \(subtitleSettings\.bottomOffset - 24\) \* 40 \/ 136\}px`/,
  'subtitle preview must map the full vertical range into its visible preview bounds')
assert.doesNotMatch(controlsIsland, /white-space: nowrap/,
  'subtitle preview must wrap like the real subtitle bubble')
assert.match(mainProcess, /requestSingleInstanceLock/, 'Electron must enforce a single pet process')
assert.match(mainProcess, /second-instance/, 'a second invocation must restore the hidden pet window')
assert.match(mainProcess, /hide-window/, 'close/hide semantics must have an explicit IPC path')
assert.match(mainProcess, /mainWindow\.on\('close',[\s\S]*persistWindowPreferences\(\)/,
  'ordinary Electron close must flush preferences without changing hide semantics')
assert.match(controlsIsland, /function closeWindow\(\) \{ void electronAPI\.closeWindow\(\) \}/,
  'the control-panel close action must use the trusted Electron close IPC')
assert.match(rendererMain, /sdk\/core5\/live2dcubismcore\.min\.js/, 'Electron must load Core 5 from the licensed upstream path')
assert.doesNotMatch(rendererMain, /Live2DFramework\.js/, 'Cubism 2 must rely on live2d.min.js without a duplicate framework file')
assert.match(viteConfig, /const cubism2Source = resolve\(__dirname, '\.\.', 'dsakiko_webui', 'frontend', 'public', 'live2d\.min\.js'\)/,
  'Cubism 2 must reuse the upstream WebUI runtime source')
assert.match(viteConfig, /const target = resolve\(__dirname, 'dist', 'renderer', 'sdk', 'live2d\.min\.js'\)/,
  'Production build must copy the shared Cubism 2 runtime')
assert.equal(fs.existsSync(path.join(root, 'src/renderer/public/sdk/live2d.min.js')), false,
  'Electron source tree must not contain a duplicate Cubism 2 runtime')
assert.match(viteConfig, /cubismCore5Assets/, 'build must stage the upstream Core 5 artifact for file:// runtime')
assert.doesNotMatch(app, /\.\/live2d\//, 'App must not depend on copied formal model assets')
assert.match(qtUi, /def publish_electron_initial_state[\s\S]*selected_model[\s\S]*live2D_model_costume[\s\S]*"sakiko_state"/,
  'black Sakiko cold start must publish the costume model and its business state')
assert.match(qtUi, /def publish_electron_initial_state[\s\S]*current_chat\.get_custom_live2d_model_meta/,
  'cold start must publish the current chat custom/V3 model rather than overwrite it with the character default')
assert.match(qtUi, /get_live2d_layout\(model_path, runtime, "single", "desktop"\)/,
  'Electron must reuse upstream per-model desktop layout semantics')
assert.match(qtUi, /def _electron_model_is_v3[\s\S]*?if not self\._electron_model_is_v3\(selected_model\)/,
  'V3 Sakiko conversion must not enter the V2 costume reload path')
assert.match(qtUi, /intent == "recover_renderer"[\s\S]*_is_active_turn_payload\(recovery\)[\s\S]*cancel_active_turn\(\)/,
  'stale renderer recovery must not cancel a newer Qt turn')
assert.match(qtUi, /def switch_l2d_fps\(self\):\s+if self\.electron_mode:\s+self\.QT_message_queue\.put\("Electron frontend 不支持切换 Live2D 渲染帧率。"\)\s+return/,
  'Electron mode must report that the Pygame-only FPS switch is unavailable')
assert.match(qtUi, /def toggle_live2d_layout_edit\(self\)[\s\S]*?if self\.electron_mode:\s+self\.QT_message_queue\.put\("Electron frontend 不支持 Live2D 布局编辑。"\)\s+return/,
  'Electron mode must report that the Pygame-only layout editor is unavailable')
assert.match(qtUi, /self\.change_char_queue\.put\(\{\s+"type": "switch_l2d_fps"[\s\S]*?已切换 Live2D 渲染帧率/,
  'the Pygame FPS queue behavior must remain available')
assert.match(qtUi, /self\.change_char_queue\.put\(\{\s+"type": "toggle_l2d_layout_edit"[\s\S]*?已切换 Live2D 布局编辑模式/,
  'the Pygame layout editor queue behavior must remain available')

const publicLive2d = path.join(root, 'src/renderer/public/live2d')
assert.equal(fs.existsSync(publicLive2d), false, 'formal character assets must stay in backend live2d_related')
assert.equal(fs.existsSync(path.join(root, 'src/renderer/public/sdk/Live2DFramework.js')), false,
  'the unused Cubism 2 framework file must not be packaged')
const upstreamCore = path.join(root, '..', 'dsakiko_webui', 'frontend', 'public', 'cubism', 'core5')
for (const file of ['live2dcubismcore.min.js', 'LICENSE.md', 'NOTICE.md', 'RedistributableFiles.txt']) {
  assert.equal(fs.existsSync(path.join(upstreamCore, file)), true, `upstream Core 5 provenance asset missing: ${file}`)
}

console.log('Electron cold-start bootstrap checks passed.')
