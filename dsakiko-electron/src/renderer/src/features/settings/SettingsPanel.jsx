import { useEffect, useState } from 'react'
import { Cable, Download, Monitor, RefreshCw, X } from 'lucide-react'
import { Button } from '../../components/ui/button.jsx'
import { desktopHost } from '../../host/desktop.js'
import { Connections } from './Connections.jsx'
import { Resources } from './Resources.jsx'
import './settings.css'

const sections = [
  { id: 'connections', name: '大模型连接', Icon: Cable },
  { id: 'resources', name: '附加资源下载', Icon: Download },
  { id: 'application', name: '应用', Icon: Monitor }
]

function Application() {
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
          {section === 'application' && <Application />}
        </div>
      </section>
    </div>
  )
}
