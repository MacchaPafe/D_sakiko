import { Check, LoaderCircle } from 'lucide-react'
import { Button } from '../../components/ui/button.jsx'
import { ResourceSelection } from './ResourceSelection.jsx'
import { DownloadProgress } from './DownloadProgress.jsx'
import { formatBytes, taskRows } from './presentation.js'
import logo from '../../assets/welcome/logo.svg'
import wordmark from '../../assets/welcome/wordmark.svg'

function SetupFooter({ state, flow }) {
  const busy = Boolean(state.pending)
  let summary = '暂不下载语音'
  let estimateLabel = '预计下载'
  let estimateBytes = state.catalog?.selectionDownloadBytes ?? null
  if (!state.catalog && !state.selection.length) estimateBytes = 0
  const estimateLoading = state.view === 'resources' && state.catalogLoading
  const estimateFailed = state.view === 'resources' && Boolean(state.catalogProblem)
  let primary = '跳过，直接开始'
  let secondary = '上一步'
  let primaryDisabled = true
  let secondaryDisabled = busy
  let secondaryAction = flow.back
  let primaryAction
  if (state.view === 'resources') {
    if (state.selection.length) {
      summary = `已选择 ${state.selection.length} 项`
      primary = '下载并继续'
      primaryAction = flow.download
      primaryDisabled =
        busy ||
        state.catalogLoading ||
        estimateFailed ||
        state.catalog?.selectionDownloadBytes == null
    }
  } else {
    const rows = taskRows(state.job)
    summary = `已完成 ${rows.filter((row) => row.status === 'ready').length} / ${rows.length} 项`
    estimateLabel = '预计新增'
    estimateBytes = state.job.space.added
    primary = state.job.status === 'succeeded' ? '开始使用' : '先进入聊天'
    if (state.job.status === 'running') {
      secondary = '暂停下载'
      secondaryAction = handlePause
    } else if (['planned', 'paused', 'failed'].includes(state.job.status)) {
      secondary = state.job.status === 'failed' ? '重试' : '继续下载'
      secondaryAction = handleResume
    } else {
      secondary = '返回资源选择'
      secondaryDisabled ||= !['succeeded', 'cancelled'].includes(state.job.status)
    }
  }
  if (state.pending === 'pause') secondary = '正在暂停…'
  if (state.pending === 'resume') secondary = '正在继续…'
  if (state.pending === 'download') primary = '正在准备下载…'
  function handlePause() {
    void flow.control('pause')
  }
  function handleResume() {
    void flow.control('resume')
  }
  return (
    <footer className="setup-footer">
      <div className="setup-budget">
        <span>{summary}</span>
        <span className="setup-estimate" aria-busy={estimateLoading}>
          <span>{estimateLabel}</span>
          <span className="setup-estimate-value" data-failed={estimateFailed || undefined}>
            {estimateFailed ? '核对失败' : formatBytes(estimateBytes, 2)}
          </span>
          <span className="setup-estimate-indicator">
            {estimateLoading && (
              <span className="setup-estimate-updating" role="status" aria-label="正在核对下载量">
                <LoaderCircle className="welcome-spinner" size={14} aria-hidden="true" />
              </span>
            )}
          </span>
        </span>
      </div>
      <div className="setup-footer-actions">
        <Button
          variant="outline"
          className="setup-secondary"
          onClick={secondaryAction}
          disabled={secondaryDisabled}
        >
          {secondary}
        </Button>
        <Button className="setup-primary" onClick={primaryAction} disabled={primaryDisabled}>
          {primary}
        </Button>
      </div>
    </footer>
  )
}

/** @param {{state: import('./setup-flow.js').SetupFlowState, flow: import('./setup-flow.js').SetupFlow}} props 应用级任务容器。 @returns {import('react').ReactElement} 两阶段暖纸引导，共用舞台和固定底部。 */
export function SetupPage({ state, flow }) {
  return (
    <section className="setup-panel">
      <header className="setup-brand">
        <img className="setup-logo" src={logo} alt="" />
        <img src={wordmark} alt="数字小祥" width="132" height="38" />
      </header>
      <ol className="setup-steps" aria-label="初始化阶段">
        <li>
          <span>
            <Check size={16} aria-hidden="true" />
            准备内置角色
          </span>
          <div />
        </li>
        <li aria-current="step">
          <span>
            <span className="setup-step-number">2</span>添加语音（可选）
          </span>
          <div />
        </li>
      </ol>
      <div className="setup-scroll" tabIndex={0} aria-label="资源详情">
        {state.connectionProblem && (
          <p className="setup-problem" role="status">
            连接已断开，正在重连…
          </p>
        )}
        {state.view === 'download' ? (
          <DownloadProgress state={state} />
        ) : (
          <ResourceSelection state={state} flow={flow} />
        )}
        {state.problem && (
          <p className="setup-problem" role="alert">
            {state.problem}
          </p>
        )}
        {state.job && ['planned', 'paused', 'failed'].includes(state.job.status) && (
          <Button
            variant="ghost"
            className="catalog-refresh"
            onClick={flow.reselect}
            disabled={Boolean(state.pending)}
          >
            重新选择资源
          </Button>
        )}
      </div>
      <SetupFooter state={state} flow={flow} />
    </section>
  )
}

/** @param {{problem?: string|null, onRetry?: () => void}} props 启动错误与重试。 @returns {import('react').ReactElement} 启动期间的真实加载或错误状态。 */
export function SetupLoading({ problem, onRetry }) {
  return (
    <main className="setup-loading">
      <div>
        <img src={wordmark} alt="数字小祥" width="205" height="58" />
        {problem ? (
          <>
            <p role="alert">{problem}</p>
            <Button variant="outline" onClick={onRetry}>
              重试
            </Button>
          </>
        ) : (
          <p role="status">
            <LoaderCircle className="welcome-spinner" size={18} aria-hidden="true" />
            正在读取初始化状态…
          </p>
        )}
      </div>
    </main>
  )
}
