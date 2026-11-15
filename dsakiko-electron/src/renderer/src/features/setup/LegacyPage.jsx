import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { CheckCircle2, CircleAlert, Clock3, FolderOpen, LoaderCircle } from 'lucide-react'
import { Button } from '../../components/ui/button.jsx'
import { Checkbox } from '../../components/ui/checkbox.jsx'
import { Progress } from '../../components/ui/progress.jsx'
import { previewCharacters } from '../onboarding/characters.js'
import { SetupLayout } from './SetupLayout.jsx'
import {
  legacyCategories,
  legacyCount,
  legacyProgress,
  legacyWarning
} from './legacy-presentation.js'

function Warnings({ warnings = [] }) {
  if (!warnings.length) return null
  return (
    <details className="legacy-warnings">
      <summary>有 {warnings.length} 项需要注意</summary>
      <ul>
        {warnings.map((warning, index) => (
          <li key={index}>{legacyWarning(warning)}</li>
        ))}
      </ul>
    </details>
  )
}
function RoleSummary({ characters = [] }) {
  if (!characters.length) return null
  return (
    <div className="legacy-roles">
      <span>已识别角色</span>
      <div>
        {characters.map((role) => (
          <RoleChip key={role.id} role={role} />
        ))}
      </div>
    </div>
  )
}
function RoleChip({ role }) {
  const avatar = previewCharacters.find((item) => item.id === role.id)?.avatar
  return (
    <span className="legacy-role">
      {avatar && <img src={avatar} alt="" width="20" height="20" />}
      {role.name}
    </span>
  )
}
function ImportChoice({ category, state, flow }) {
  const scan = state.legacyScan
  const selected = Boolean(state.legacyInclude[category.id])
  const disabled = Boolean(
    state.pending || state.scanning || !scan.availableCategories[category.id]
  )
  function handleToggle() {
    flow.toggleLegacy(category.id)
  }
  return (
    <div className="legacy-card" data-selected={selected} data-interactive={!disabled}>
      <div className="legacy-row">
        <Checkbox
          id={'legacy-' + category.id}
          aria-label={category.name}
          checked={selected}
          disabled={disabled}
          onCheckedChange={handleToggle}
        />
        <label className="resource-label" htmlFor={'legacy-' + category.id}>
          <span className="resource-name">{category.name}</span>
          <span className="resource-description">{category.description}</span>
          {category.id === 'gsv' && (
            <span className="legacy-note">新版本运行环境将在下一步补齐</span>
          )}
        </label>
        <span className="resource-badge">{legacyCount(category.id, scan)}</span>
      </div>
      {category.id === 'characters' && <RoleSummary characters={scan.characters} />}
    </div>
  )
}
function LegacyDirectory({ state, flow }) {
  const source = state.legacySource
  const compact = Boolean(source)
  const frame = useRef(null)
  const measuredHeight = useRef(null)
  useLayoutEffect(() => {
    const wrapper = frame.current
    const card = wrapper.firstElementChild
    const from = measuredHeight.current
    const to = card.getBoundingClientRect().height
    let animation
    // 先量新布局的自然高度，再过渡外框。内部保持正常换行，也无需保留第二个目录按钮。
    if (from !== null && from !== to && !matchMedia('(prefers-reduced-motion: reduce)').matches) {
      animation = wrapper.animate([{ height: `${from}px` }, { height: `${to}px` }], {
        duration: 210,
        easing: 'cubic-bezier(0.2, 0.7, 0.3, 1)'
      })
    }
    measuredHeight.current = to
    const observer = new ResizeObserver(() => {
      const height = card.getBoundingClientRect().height
      if (height === measuredHeight.current) return
      animation?.cancel()
      measuredHeight.current = height
    })
    observer.observe(card)
    return () => {
      observer.disconnect()
      animation?.cancel()
    }
  }, [compact])
  let pickerLabel = source ? '更换文件夹' : '选择文件夹'
  if (state.picking) pickerLabel = '正在选择…'
  let directoryStatus = '尚未选择文件夹'
  if (state.scanning) directoryStatus = '正在识别旧版数据…'
  else if (state.legacyScan)
    directoryStatus = state.legacyScan.canImport ? '成功识别旧版程序目录' : '没有找到可导入的资料'
  else if (source) directoryStatus = '未完成扫描'
  return (
    <div className="legacy-directory-frame" ref={frame}>
      <div className={source ? 'legacy-directory' : 'legacy-directory legacy-directory-empty'}>
        <FolderOpen size={source ? 24 : 40} aria-hidden="true" />
        <div className="resource-label">
          <span className="resource-name">{source?.name ?? directoryStatus}</span>
          <span className="resource-description">
            {source ? directoryStatus : '请选择你之前启动旧版数字小祥时使用的程序文件夹。'}
          </span>
        </div>
        <Button
          variant={source ? 'outline' : 'default'}
          onClick={flow.chooseLegacy}
          disabled={Boolean(state.pending || state.picking)}
        >
          {pickerLabel}
        </Button>
      </div>
    </div>
  )
}
function LegacySelection({ state, flow }) {
  const source = state.legacySource
  function handleRescan() {
    void flow.scanLegacy()
  }
  return (
    <div className="legacy-content">
      <header className="setup-heading">
        <h1>导入旧版数据</h1>
        <p>从之前的程序中带回你的角色、聊天记录与配置。</p>
      </header>
      <LegacyDirectory state={state} flow={flow} />
      {state.scanning && (
        <p className="legacy-scan-status" role="status">
          <LoaderCircle size={16} className="welcome-spinner" aria-hidden="true" />
          正在扫描旧版资料…
        </p>
      )}
      {state.problem && (
        <div className="setup-problem" role="alert">
          <p>{state.problem}</p>
          {source && !state.scanning && (
            <Button
              variant="outline"
              size="sm"
              onClick={handleRescan}
              disabled={Boolean(state.pending)}
            >
              重新扫描
            </Button>
          )}
        </div>
      )}
      {state.legacyScan && (
        <div className="legacy-scan-results">
          <div>
            <div className="legacy-options">
              <h2>选择要导入的内容</h2>
              {legacyCategories.map((category) => (
                <ImportChoice key={category.id} category={category} state={state} flow={flow} />
              ))}
            </div>
            <Warnings warnings={state.legacyScan.warnings} />
          </div>
        </div>
      )}
    </div>
  )
}
const itemLabels = {
  waiting: '等待导入',
  committing: '导入中',
  ready: '已导入',
  partial: '部分完成',
  failed: '导入失败',
  paused: '已暂停',
  skipped: '已跳过'
}
function ImportResult({ category, item }) {
  if (!item) return null
  let Icon = Clock3
  if (item.status === 'ready') Icon = CheckCircle2
  if (item.status === 'committing') Icon = LoaderCircle
  if (['failed', 'partial'].includes(item.status)) Icon = CircleAlert
  return (
    <div className="legacy-card" data-status={item.status}>
      <div className="legacy-row">
        <Icon
          size={20}
          className={item.status === 'committing' ? 'welcome-spinner' : undefined}
          aria-hidden="true"
        />
        <div className="resource-label">
          <span className="resource-name">{category.name}</span>
          <span className="resource-description">
            已导入 {item.completed} / {item.total}
          </span>
          {item.problem && <span className="legacy-note">{item.problem.message}</span>}
        </div>
        <span className="resource-badge" data-tone={item.status}>
          {itemLabels[item.status] ?? '等待导入'}
        </span>
      </div>
    </div>
  )
}
function LegacyResults({ state }) {
  const job = state.job
  const progress = legacyProgress(job)
  const partial = Object.values(job.items ?? {}).some((item) => item.completed > 0)
  let title = '正在导入旧版数据'
  if (job.status === 'paused') title = '导入已暂停'
  else if (job.status === 'cancelled') title = '导入已取消'
  else if (job.status === 'failed') title = partial ? '导入部分完成' : '导入遇到问题'
  else if (job.status === 'succeeded') {
    const incomplete =
      job.warnings?.length ||
      Object.values(job.items ?? {}).some((item) => item.status === 'partial')
    title = incomplete ? '导入部分完成' : '导入完成'
  }
  return (
    <div className="legacy-content">
      <header className="setup-heading">
        <h1>{title}</h1>
      </header>
      <div className="download-summary">
        <div className="download-summary-line">
          <span>{progress.label}</span>
          <span>{progress.percent == null ? '' : progress.percent + '%'}</span>
        </div>
        <Progress value={progress.percent} aria-label="导入进度" />
      </div>
      <div className="legacy-options">
        <h2>导入项目</h2>
        {legacyCategories.map((category) => (
          <ImportResult key={category.id} category={category} item={job.items?.[category.id]} />
        ))}
      </div>
      <Warnings warnings={job.warnings} />
      {(job.problem || state.problem) && (
        <p className="setup-problem" role="alert">
          {state.problem ?? job.problem.message}
        </p>
      )}
    </div>
  )
}
function CancelImportDialog({ onClose, onConfirm }) {
  const dialog = useRef(null)
  useEffect(() => {
    dialog.current.showModal()
  }, [])
  return (
    <dialog
      ref={dialog}
      className="legacy-confirm"
      onCancel={onClose}
      aria-labelledby="cancel-import-title"
    >
      <h2 id="cancel-import-title">取消导入？</h2>
      <p>将停止剩余内容的导入，已经导入的资料会保留。</p>
      <div>
        <Button variant="outline" onClick={onClose} autoFocus>
          继续等待
        </Button>
        <Button onClick={onConfirm}>确认取消导入</Button>
      </div>
    </dialog>
  )
}
/** @param {{state: import('./setup-flow.js').SetupFlowState, flow: import('./setup-flow.js').SetupFlow}} props 目录扫描与持久导入任务。 @returns {import('react').ReactElement} 首阶段导入界面，不拥有舞台或后台任务。 */
export function LegacyPage({ state, flow }) {
  const [confirmCancel, setConfirmCancel] = useState(false)
  const selecting = state.view === 'legacy'
  const job = state.job
  const busy = Boolean(state.pending)
  const selected = Object.values(state.legacyInclude).filter(Boolean).length
  const canImport =
    !busy && !state.picking && !state.scanning && state.legacyScan?.canImport && selected > 0
  function handlePause() {
    void flow.control('pause')
  }
  function handleResume() {
    void flow.control('resume')
  }
  function openCancel() {
    setConfirmCancel(true)
  }
  function closeCancel() {
    setConfirmCancel(false)
  }
  function handleCancel() {
    closeCancel()
    void flow.control('cancel')
  }
  const footer = (
    <footer className="setup-footer">
      <div className="setup-budget">
        <span>{selecting ? `已选择 ${selected} 项导入内容` : '已导入的内容会保留'}</span>
        {busy && <span role="status">正在处理…</span>}
      </div>
      <div className="setup-footer-actions">
        {selecting && !state.onboarding?.path && (
          <Button className="setup-secondary" variant="outline" onClick={flow.back} disabled={busy}>
            返回
          </Button>
        )}
        {selecting && (
          <Button className="setup-primary" onClick={flow.importLegacy} disabled={!canImport}>
            开始导入
          </Button>
        )}
        {!selecting && job.status === 'running' && (
          <>
            <Button
              className="setup-secondary"
              variant="outline"
              onClick={openCancel}
              disabled={busy}
            >
              取消导入
            </Button>
            <Button className="setup-primary" onClick={handlePause} disabled={busy}>
              暂停导入
            </Button>
          </>
        )}
        {!selecting && ['planned', 'paused', 'failed'].includes(job.status) && (
          <Button
            className="setup-secondary"
            variant="outline"
            onClick={handleResume}
            disabled={busy}
          >
            {job.status === 'failed' ? '重试导入' : '继续导入'}
          </Button>
        )}
        {!selecting && job.status !== 'running' && (
          <Button className="setup-primary" onClick={flow.continueLegacy} disabled={busy}>
            {job.status === 'succeeded' ? '继续' : '跳过并继续'}
          </Button>
        )}
      </div>
      {!selecting && ['planned', 'paused', 'failed', 'cancelled'].includes(job.status) && (
        <Button variant="ghost" onClick={flow.reselectLegacy} disabled={busy}>
          重新选择导入内容
        </Button>
      )}
      {selecting && state.onboarding?.path === 'legacy' && (
        <Button variant="ghost" onClick={flow.returnToLegacyResult} disabled={busy}>
          返回导入结果
        </Button>
      )}
    </footer>
  )
  return (
    <SetupLayout
      firstStage="导入旧版数据"
      activeStage={1}
      view={state.view}
      footer={footer}
      scrollLabel="导入详情"
    >
      {state.connectionProblem && (
        <p className="setup-problem" role="status">
          连接已断开，正在重连…
        </p>
      )}
      {selecting ? <LegacySelection state={state} flow={flow} /> : <LegacyResults state={state} />}
      {confirmCancel && <CancelImportDialog onClose={closeCancel} onConfirm={handleCancel} />}
    </SetupLayout>
  )
}
