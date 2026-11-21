import { useEffect, useRef, useState } from 'react'
import { Cable, Download, Monitor, RefreshCw, RotateCcw, X } from 'lucide-react'
import { Button } from '../../components/ui/button.jsx'
import { Slider } from '../../components/ui/slider.jsx'
import {
  DEFAULT_SPEECH_PAUSE_MS,
  DEFAULT_SPEECH_SPEED
} from '../../../../shared/contracts/speech.js'
import { desktopHost } from '../../host/desktop.js'
import { Connections } from './Connections.jsx'
import { Resources } from './Resources.jsx'
import './settings.css'

const sections = [
  { id: 'connections', name: '大模型连接', Icon: Cable },
  { id: 'resources', name: '附加资源下载', Icon: Download },
  { id: 'application', name: '应用', Icon: Monitor }
]

const SPEECH_SPEED_MIN = 0.6
const SPEECH_SPEED_MAX = 1.4
const SPEECH_PAUSE_MIN_S = 0.1
const SPEECH_PAUSE_MAX_S = 0.8
const SPEECH_SLIDER_STEP = 0.05

function roundTwoDecimals(value) {
  return Math.round(value * 100) / 100
}

function SpeechSliderCard({
  title,
  description,
  value,
  defaultValue,
  min,
  max,
  step,
  formatValue,
  resetLabel,
  defaultHint,
  resetting,
  problem,
  onChange,
  onCommit,
  onReset
}) {
  const isDefault = Math.abs(value - defaultValue) < 0.001
  return (
    <article className="settings-card settings-speech-card">
      <div className="settings-speech-header">
        <div>
          <h4>{title}</h4>
          <p>{description}</p>
        </div>
        <div className="settings-speech-controls">
          <output className="settings-speech-value">{formatValue(value)}</output>
          <Button
            type="button"
            variant="ghost"
            size="icon-xs"
            className="settings-speech-reset"
            disabled={resetting || isDefault}
            aria-label={resetLabel}
            title={defaultHint}
            onClick={onReset}
          >
            <RotateCcw aria-hidden="true" />
          </Button>
        </div>
      </div>
      <div className="settings-speech-slider-row">
        <Slider
          min={min}
          max={max}
          step={step}
          value={[value]}
          aria-label={title}
          aria-valuetext={formatValue(value)}
          onValueChange={([next]) => onChange(next)}
          onValueCommit={([next]) => onCommit(next)}
        />
        <div className="settings-speech-bounds" aria-hidden="true">
          <span>{formatValue(min)}</span>
          <span>{formatValue(max)}</span>
        </div>
      </div>
      {problem && (
        <p className="settings-error" role="alert">
          {problem}
        </p>
      )}
    </article>
  )
}

function SpeechPreferences({ speech, client }) {
  const [speed, setSpeed] = useState(() => speech?.speed ?? DEFAULT_SPEECH_SPEED)
  const [pauseSeconds, setPauseSeconds] = useState(
    () => (speech?.sentencePauseMs ?? DEFAULT_SPEECH_PAUSE_MS) / 1000
  )
  const [resettingField, setResettingField] = useState(null)
  const [problems, setProblems] = useState({ speed: null, pause: null })
  const pendingSpeedRef = useRef(0)
  const pendingPauseRef = useRef(0)
  const speedReqRef = useRef(0)
  const pauseReqRef = useRef(0)

  useEffect(() => {
    if (speech?.speed !== undefined && pendingSpeedRef.current === 0) setSpeed(speech.speed)
  }, [speech?.speed])

  useEffect(() => {
    if (speech?.sentencePauseMs !== undefined && pendingPauseRef.current === 0) {
      setPauseSeconds(speech.sentencePauseMs / 1000)
    }
  }, [speech?.sentencePauseMs])

  if (!speech) return null

  const defaultPauseSeconds = DEFAULT_SPEECH_PAUSE_MS / 1000

  async function saveSpeed(nextValue, isReset = false) {
    const rounded = roundTwoDecimals(nextValue)
    const requestId = ++speedReqRef.current
    pendingSpeedRef.current += 1
    setSpeed(rounded)
    if (isReset) setResettingField('speed')
    setProblems((current) => ({ ...current, speed: null }))
    try {
      await client.settings.update({ speech: { speed: rounded } })
    } catch (error) {
      if (speedReqRef.current === requestId) {
        setSpeed(speech.speed)
        setProblems((current) => ({ ...current, speed: error.message }))
      }
    } finally {
      pendingSpeedRef.current = Math.max(0, pendingSpeedRef.current - 1)
      if (isReset) setResettingField((current) => (current === 'speed' ? null : current))
    }
  }

  async function savePause(nextSeconds, isReset = false) {
    const roundedMs = Math.round(nextSeconds * 1000)
    const roundedSeconds = roundedMs / 1000
    const requestId = ++pauseReqRef.current
    pendingPauseRef.current += 1
    setPauseSeconds(roundedSeconds)
    if (isReset) setResettingField('pause')
    setProblems((current) => ({ ...current, pause: null }))
    try {
      await client.settings.update({ speech: { sentencePauseMs: roundedMs } })
    } catch (error) {
      if (pauseReqRef.current === requestId) {
        setPauseSeconds(speech.sentencePauseMs / 1000)
        setProblems((current) => ({ ...current, pause: error.message }))
      }
    } finally {
      pendingPauseRef.current = Math.max(0, pendingPauseRef.current - 1)
      if (isReset) setResettingField((current) => (current === 'pause' ? null : current))
    }
  }

  return (
    <section aria-label="偏好">
      <h3>偏好</h3>
      <div className="settings-stack">
        <SpeechSliderCard
          title="回复语速"
          description="用于单人对话和小剧场"
          value={speed}
          defaultValue={DEFAULT_SPEECH_SPEED}
          min={SPEECH_SPEED_MIN}
          max={SPEECH_SPEED_MAX}
          step={SPEECH_SLIDER_STEP}
          formatValue={(value) => `${value.toFixed(2)}×`}
          resetLabel="恢复默认语速"
          defaultHint={`恢复默认（${DEFAULT_SPEECH_SPEED.toFixed(2)}×）`}
          resetting={resettingField === 'speed'}
          problem={problems.speed}
          onChange={setSpeed}
          onCommit={(next) => void saveSpeed(next)}
          onReset={() => void saveSpeed(DEFAULT_SPEECH_SPEED, true)}
        />
        <SpeechSliderCard
          title="句间停顿"
          description="调整语音片段之间的停顿时长"
          value={pauseSeconds}
          defaultValue={defaultPauseSeconds}
          min={SPEECH_PAUSE_MIN_S}
          max={SPEECH_PAUSE_MAX_S}
          step={SPEECH_SLIDER_STEP}
          formatValue={(value) => `${value.toFixed(2)} 秒`}
          resetLabel="恢复默认句间停顿"
          defaultHint={`恢复默认（${defaultPauseSeconds.toFixed(2)} 秒）`}
          resetting={resettingField === 'pause'}
          problem={problems.pause}
          onChange={setPauseSeconds}
          onCommit={(next) => void savePause(next)}
          onReset={() => void savePause(defaultPauseSeconds, true)}
        />
      </div>
    </section>
  )
}

function Application({ settings, client }) {
  const [state, setState] = useState(null)
  const [problem, setProblem] = useState(null)
  const [saving, setSaving] = useState(false)
  useEffect(() => {
    let current = true
    const host = desktopHost()
    async function load() {
      try {
        const { platform } = await host.getRuntimeInfo()
        const behavior = platform === 'win32' ? await host.getWindowCloseBehavior() : null
        if (current) setState({ platform, behavior })
      } catch (error) {
        if (current) setProblem(error.message)
      }
    }
    void load()
    return () => {
      current = false
    }
  }, [])
  async function change(event) {
    const value = event.target.value
    setSaving(true)
    setProblem(null)
    try {
      const behavior = await desktopHost().setWindowCloseBehavior(value)
      setState((current) => ({ ...current, behavior }))
    } catch (error) {
      setProblem(error.message)
    } finally {
      setSaving(false)
    }
  }
  return (
    <>
      <section aria-label="窗口行为">
        <h3>窗口行为</h3>
        <div className="settings-card settings-window-card">
          {state?.platform === 'win32' ? (
            <label className="settings-platform-row">
              <span>关闭窗口时</span>
              <select
                className="field-input"
                value={state.behavior ?? ''}
                onChange={change}
                disabled={saving}
              >
                {!state.behavior && (
                  <option value="" disabled>
                    首次关闭时询问
                  </option>
                )}
                <option value="tray">缩小到托盘</option>
                <option value="quit">退出程序</option>
              </select>
            </label>
          ) : (
            state && (
              <div>
                <h4>关闭窗口时</h4>
                {state.platform === 'darwin' ? (
                  <p>按照 macOS 规范，关闭窗口时，应用将继续运行。请按 ⌘Q 退出应用。</p>
                ) : (
                  <p>退出应用。</p>
                )}
              </div>
            )
          )}
          {!state && !problem && (
            <p className="settings-status-delayed" role="status">
              正在读取窗口偏好…
            </p>
          )}
          {problem && (
            <p className="settings-error" role="alert">
              {problem}
            </p>
          )}
        </div>
      </section>
      <SpeechPreferences speech={settings?.speech} client={client} />
    </>
  )
}

/** @param {object} props 设置入口、主页业务和后台任务。 @returns {import('react').ReactElement} 共享浮窗中的设置视图。 */
export function SettingsPanel({
  entry,
  client,
  flow,
  state,
  setup,
  setupState,
  onClose,
  onDownload,
  onShowDownloads
}) {
  const [section, setSection] = useState(entry.section)
  const title = sections.find((item) => item.id === section)?.name
  useEffect(() => {
    if (section === 'resources') void setup.refreshManagedCatalog()
  }, [section, setup])
  function checkUpdates() {
    void setup.refreshManagedCatalog({ force: true })
  }
  return (
    <div className="settings-layout">
      <aside className="settings-sidebar">
        <h1 tabIndex={-1} data-dialog-focus>
          设置
        </h1>
        <nav aria-label="设置分类">
          {sections.map(({ id, name, Icon }) => (
            <button
              type="button"
              key={id}
              aria-current={section === id ? 'page' : undefined}
              onClick={() => setSection(id)}
            >
              <Icon size={17} />
              {name}
            </button>
          ))}
        </nav>
      </aside>
      <section className="settings-main" aria-label={title}>
        <header className="settings-topbar">
          <h2>{title}</h2>
          {section === 'resources' && (
            <Button
              variant="ghost"
              size="sm"
              disabled={setupState.managedCatalogChecking}
              onClick={checkUpdates}
            >
              <RefreshCw size={14} />
              {setupState.managedCatalogChecking ? '正在检查…' : '检查更新'}
            </Button>
          )}
          <Button
            className="settings-dismiss"
            size="icon"
            variant="ghost"
            onClick={onClose}
            aria-label="关闭设置"
          >
            <X />
          </Button>
        </header>
        <div className="settings-content" key={section}>
          {section === 'connections' && (
            <Connections
              client={client}
              connections={state.connections}
              refresh={() => flow.refresh()}
            />
          )}
          {section === 'resources' && (
            <Resources
              client={client}
              target={entry.target}
              onDownload={onDownload}
              setupState={setupState}
              setup={setup}
              onShowDownloads={onShowDownloads}
            />
          )}
          {section === 'application' && <Application settings={state.settings} client={client} />}
        </div>
      </section>
    </div>
  )
}
