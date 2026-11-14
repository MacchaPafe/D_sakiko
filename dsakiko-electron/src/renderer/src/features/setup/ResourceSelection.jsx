import { RefreshCw } from 'lucide-react'
import { Button } from '../../components/ui/button.jsx'
import { Checkbox } from '../../components/ui/checkbox.jsx'
import { previewCharacters } from '../onboarding/characters.js'
import { formatBytes } from './presentation.js'

function SizeBadge({ option }) {
  let label = '大小待确认'
  if (option.ready) label = '已就绪'
  else if (!option.available) label = '不可用'
  else if (option.sizeKnown) {
    label = formatBytes(option.downloadBytes)
  }
  return (
    <span className="resource-badge" data-tone={option.ready ? 'ready' : undefined}>
      {label}
    </span>
  )
}

function ResourceChoice({ option, title, description, avatar, selection, locked, onToggle }) {
  const disabled = locked || !option.available || option.ready
  const id = 'resource-' + option.id
  function handleChange() {
    onToggle(option.id)
  }
  return (
    <div className="resource-choice" data-disabled={Boolean(disabled)}>
      <div className="resource-choice-line">
        <Checkbox
          id={id}
          aria-label={title}
          checked={option.ready || selection.includes(option.id)}
          disabled={Boolean(disabled)}
          onCheckedChange={handleChange}
        />
        {avatar && <img className="resource-avatar" src={avatar} alt="" width="28" height="28" />}
        <label className="resource-label" htmlFor={id}>
          <span className="resource-name">{title}</span>
          {description && <span className="resource-description">{description}</span>}
        </label>
        <SizeBadge option={option} />
      </div>
    </div>
  )
}

/** @param {{state: import('./setup-flow.js').SetupFlowState, flow: import('./setup-flow.js').SetupFlow}} props 后端目录与本地选择。 @returns {import('react').ReactElement} 语音输入、合成引擎和三位角色的产品选项。 */
export function ResourceSelection({ state, flow }) {
  const options = state.catalog?.groups.flatMap((group) => group.options) ?? []
  const asr = options.find((item) => item.id === 'asr')
  const gsv = options.find((item) => item.id === 'gsv')
  const locked = Boolean(state.pending || state.refreshing)
  const voices = previewCharacters
    .map((character) => ({
      character,
      option: options.find((item) => item.id === 'voice.' + character.id)
    }))
    .filter((item) => item.option)
  return (
    <>
      <header className="setup-heading">
        <h1>添加语音功能</h1>
        <p>让交流不止于文字。按需选择语音合成引擎和角色声音。</p>
      </header>
      {state.catalogProblem && (
        <div className="setup-problem" role="alert">
          {state.catalogProblem}
        </div>
      )}
      {!state.catalog && state.catalogLoading && <p role="status">正在读取资源目录…</p>}
      <div className="resource-groups">
        {asr && (
          <section className="resource-group" aria-label="语音输入">
            <h2>语音输入</h2>
            <div className="resource-card resource-standalone">
              <ResourceChoice
                option={asr}
                title="语音转文字"
                description="直接用麦克风说话，自动转换成文字发送"
                selection={state.selection}
                locked
                onToggle={flow.toggle}
              />
            </div>
          </section>
        )}
        {gsv && (
          <section className="resource-group" aria-label="角色说话">
            <h2>角色说话</h2>
            <div className="resource-card">
              <div className="resource-engine">
                <ResourceChoice
                  option={gsv}
                  title="GPT-SoVITS"
                  description={gsv.unavailableReason || '让角色开口说话'}
                  selection={state.selection}
                  locked={locked}
                  onToggle={flow.toggle}
                />
              </div>
              <div className="resource-voices">
                <div className="resource-section-label">
                  <h3>角色语音</h3>
                  <span>按需勾选想要使用的角色声音</span>
                </div>
                {voices.map(({ character, option }) => (
                  <ResourceChoice
                    key={character.id}
                    option={option}
                    title={character.name}
                    avatar={character.avatar}
                    selection={state.selection}
                    locked={locked}
                    onToggle={flow.toggle}
                  />
                ))}
              </div>
            </div>
          </section>
        )}
      </div>
      <Button
        className="catalog-refresh"
        variant="ghost"
        size="sm"
        onClick={flow.refreshCatalog}
        disabled={locked}
      >
        <RefreshCw
          className={state.refreshing ? 'welcome-spinner' : undefined}
          aria-hidden="true"
        />
        {state.refreshing ? '正在检查资源…' : '检查资源更新'}
      </Button>
    </>
  )
}
