import { useEffect, useId, useRef, useState } from 'react'
import { ChevronRight, Search } from 'lucide-react'
import { Button } from '../../components/ui/button.jsx'
import { SelectField } from '../../components/ui/SelectField.jsx'
import { toolGroups, toggleTools } from '../../../../shared/tool-options.js'
import './tool-controls.css'

/** 极简两列竖向滑块图标，用于表示可展开调控的工具菜单入口。 */
function ToolsToggleIcon() {
  return (
    <svg
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <circle cx="7" cy="7" r="3" />
      <path d="M7 10v10" />
      <path d="M17 4v10" />
      <circle cx="17" cy="17" r="3" />
    </svg>
  )
}

function groupCheckState(names, enabled) {
  const count = names.filter((name) => enabled.includes(name)).length
  if (count === names.length) return true
  if (count > 0) return 'mixed'
  return false
}

function ToolRow({ names, label, enabled, onChange, className = '' }) {
  const checked = groupCheckState(names, enabled)
  return (
    <button
      type="button"
      role="checkbox"
      aria-checked={checked}
      className={className ? 'tool-row ' + className : 'tool-row'}
      onClick={() => onChange(toggleTools(enabled, names))}
    >
      <span>{label}</span>
      <span className="tool-switch" aria-hidden="true" data-state={String(checked)}>
        <i />
      </span>
    </button>
  )
}

function ToolGroup({ label, items, term, expanded, onToggleExpand, enabled, onChange }) {
  const groupListId = useId()
  const matches = term
    ? items.filter(([name, text]) => (label + name + text).toLowerCase().includes(term))
    : items
  if (!matches.length) return null

  if (items.length === 1) {
    const [name, text] = items[0]
    return (
      <ToolRow
        names={[name]}
        label={text}
        enabled={enabled}
        onChange={onChange}
        className="tool-row-standalone"
      />
    )
  }

  const allNames = items.map(([name]) => name)
  const checked = groupCheckState(allNames, enabled)
  const isExpanded = Boolean(term) || expanded

  return (
    <section className="tool-group">
      <div className="tool-group-row">
        <button
          type="button"
          className="tool-expand-btn"
          aria-expanded={isExpanded}
          aria-controls={groupListId}
          onClick={() => onToggleExpand(label)}
        >
          <ChevronRight size={14} className="tool-expand-chevron" aria-hidden="true" />
          <span>{label}</span>
        </button>
        <button
          type="button"
          role="checkbox"
          aria-checked={checked}
          aria-label={label}
          className="tool-switch-btn"
          onClick={() => onChange(toggleTools(enabled, allNames))}
        >
          <span className="tool-switch" aria-hidden="true" data-state={String(checked)}>
            <i />
          </span>
        </button>
      </div>
      {isExpanded && (
        <div id={groupListId} className="tool-group-children">
          {matches.map(([name, text]) => (
            <ToolRow
              key={name}
              names={[name]}
              label={text}
              enabled={enabled}
              onChange={onChange}
              className="tool-row-child"
            />
          ))}
        </div>
      )}
    </section>
  )
}

/**
 * 单视图可展开的工具开关菜单，支持分组总开关与逐项微调。
 * @param {object} props 当前启用工具列表、变更回调、轮次活动态、禁用状态与是否显示文字标签。
 * @returns {import('react').ReactElement} 工具设置触发按钮与弹出菜单。
 */
export function ToolMenu({
  enabled,
  onChange,
  active = false,
  disabled = false,
  showLabel = false
}) {
  const pop = useRef(null),
    trigger = useRef(null),
    id = useId()
  const [opened, setOpened] = useState(false)
  const [query, setQuery] = useState('')
  const [expandedGroups, setExpandedGroups] = useState({})

  function position() {
    const rect = trigger.current.getBoundingClientRect()
    const style = pop.current.style
    style.left =
      Math.max(8, Math.min(rect.left, window.innerWidth - pop.current.offsetWidth - 8)) + 'px'
    const anchorTop =
      trigger.current.closest('.home-composer')?.getBoundingClientRect().top ?? rect.top
    const above = anchorTop > Math.min(330, window.innerHeight - rect.bottom)
    style.top = above ? 'auto' : rect.bottom + 6 + 'px'
    style.bottom = above ? window.innerHeight - anchorTop + 6 + 'px' : 'auto'
    const availableHeight = (above ? anchorTop : window.innerHeight - rect.bottom) - 16
    style.maxHeight = Math.min(350, Math.max(110, availableHeight)) + 'px'
  }

  function toggle() {
    if (pop.current.matches(':popover-open')) {
      pop.current.hidePopover()
      return
    }
    setQuery('')
    setExpandedGroups({})
    pop.current.showPopover()
    position()
  }

  function toggleExpand(label) {
    setExpandedGroups((current) => ({ ...current, [label]: !current[label] }))
  }

  useEffect(() => {
    if (!opened) return
    window.addEventListener('resize', position)
    return () => window.removeEventListener('resize', position)
  }, [opened])

  const term = query.trim().toLowerCase()
  const hasMatches = toolGroups.some(
    ([label, items]) =>
      !term || items.some(([name, text]) => (label + name + text).toLowerCase().includes(term))
  )

  function keydown(event) {
    if (event.key === 'Escape') {
      event.stopPropagation()
      pop.current.hidePopover()
      trigger.current.focus()
      return
    }
    if (!['ArrowDown', 'ArrowUp'].includes(event.key)) return
    const rows = [...pop.current.querySelectorAll('.tools-list button')]
    if (!rows.length) return
    event.preventDefault()
    const index = rows.indexOf(document.activeElement)
    rows[
      (index + (event.key === 'ArrowDown' ? 1 : rows.length - 1) + rows.length) % rows.length
    ].focus()
  }

  return (
    <>
      <Button
        type="button"
        ref={trigger}
        aria-label="工具"
        title={showLabel ? undefined : '工具'}
        aria-expanded={opened}
        aria-controls={id}
        size={showLabel ? 'sm' : 'icon-sm'}
        variant="ghost"
        className="tools-trigger"
        disabled={disabled}
        onClick={toggle}
      >
        <ToolsToggleIcon />
        {showLabel && '工具'}
      </Button>
      <div
        popover="auto"
        id={id}
        ref={pop}
        className="tools-popover"
        role="dialog"
        aria-label="工具设置"
        onKeyDown={keydown}
        onToggle={(e) => setOpened(e.newState === 'open')}
      >
        <div className="tools-search">
          <Search size={14} className="tools-search-icon" aria-hidden="true" />
          <input
            aria-label="搜索工具"
            placeholder="搜索工具"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>
        <div className="tools-list">
          {toolGroups.map(([label, items]) => (
            <ToolGroup
              key={label}
              label={label}
              items={items}
              term={term}
              expanded={Boolean(expandedGroups[label])}
              onToggleExpand={toggleExpand}
              enabled={enabled}
              onChange={onChange}
            />
          ))}
          {!hasMatches && <p className="tools-empty">没有匹配的工具</p>}
        </div>
        {active && <small>更改从下一轮生效</small>}
      </div>
    </>
  )
}

const labels = { low: '低', medium: '中', high: '高', xhigh: '超高', max: '最高', minimal: '最低' }
/** @param {object} props 当前对话状态。 @returns {import('react').ReactElement|null} 模型实际支持的思考选项。 */
export function ThinkingPicker({ flow }) {
  const { model, value } = flow.reasoningState()
  const caps = model?.capabilities ?? {}
  const efforts = (caps.reasoningEfforts ?? []).filter((level) => level !== 'none')
  const unavailable = Boolean(
    value.enabled &&
    value.effort &&
    caps.reasoning?.status === 'supported' &&
    !efforts.includes(value.effort)
  )
  const [notice, setNotice] = useState('')
  useEffect(() => {
    if (!unavailable) return
    void flow
      .setReasoning({ enabled: true, effort: null })
      .then(() => setNotice('此前选择的思考强度已不可用，已恢复模型默认。'))
  }, [unavailable, flow])
  if (!model || caps.reasoning?.status !== 'supported') return null
  const options = [
    {
      value: 'default',
      label: caps.reasoningDefault
        ? '默认（' + (labels[caps.reasoningDefault] ?? caps.reasoningDefault) + '）'
        : '默认'
    }
  ]
  if (caps.reasoningToggle || caps.reasoningEfforts?.includes('none'))
    options.unshift({ value: 'off', label: '关闭' })
  for (const effort of efforts) options.push({ value: effort, label: labels[effort] ?? effort })
  function choose(choice) {
    let selectedEffort = choice
    if (choice === 'off') selectedEffort = value.effort
    if (choice === 'default') selectedEffort = null
    setNotice('')
    void flow.setReasoning({
      enabled: choice !== 'off',
      effort: selectedEffort
    })
  }
  let choice = value.effort ?? 'default'
  if (!value.enabled && options.some((o) => o.value === 'off')) choice = 'off'
  if (!options.some((o) => o.value === choice)) choice = 'default'
  if (options.length === 1) return null
  return (
    <div className="thinking-picker" title={notice || '思考强度'}>
      <SelectField
        label="思考强度"
        options={options}
        value={choice}
        onChange={choose}
        customPopover
        formatSelected={(option) => option.label}
        footer={notice ? { label: notice, onAction: () => setNotice('') } : undefined}
      />
    </div>
  )
}

/** @param {object} props 当前会话。 @returns {import('react').ReactElement|null} 联网和本地函数的冲突提示。 */
export function ThinkingConflict({ flow }) {
  const { value, conflict } = flow.reasoningState()
  if (!conflict) return null
  return (
    <div className="thinking-conflict" role="status">
      <span>使用 DeepSeek 时，同时使用网页访问工具和本地工具需要开启思考。</span>
      <button type="button" onClick={() => flow.setReasoning({ ...value, enabled: true })}>
        开启思考
      </button>
    </div>
  )
}
