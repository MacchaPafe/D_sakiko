import { LoaderCircle } from 'lucide-react'
import { Button } from '../../components/ui/button.jsx'
import { ResourceSelection } from './ResourceSelection.jsx'
import { DownloadProgress } from './DownloadProgress.jsx'
import { formatBytes, taskRows } from './presentation.js'
import { SetupLayout } from './SetupLayout.jsx'
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
  let primaryDisabled = busy
  let secondaryDisabled = busy
  let secondaryAction = flow.back
  let primaryAction = flow.finish
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
  if (state.pending === 'finish') primary = '正在进入…'
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
        {!(state.view === 'resources' && state.onboarding?.path === 'legacy') && (
          <Button
            variant="outline"
            className="setup-secondary"
            onClick={secondaryAction}
            disabled={secondaryDisabled}
          >
            {secondary}
          </Button>
        )}
        <Button className="setup-primary" onClick={primaryAction} disabled={primaryDisabled}>
          {primary}
        </Button>
      </div>
      {state.view === 'download' && state.onboarding?.path === 'quick' && (
        <Button variant="ghost" onClick={flow.returnQuick} disabled={busy}>
          返回欢迎页
        </Button>
      )}
    </footer>
  )
}

/** @param {{state: import('./setup-flow.js').SetupFlowState, flow: import('./setup-flow.js').SetupFlow}} props 应用级任务容器。 @returns {import('react').ReactElement} 两阶段暖纸引导，共用舞台和固定底部。 */
export function SetupPage({ state, flow }) {
  return (
    <SetupLayout
      firstStage={state.onboarding?.path === 'legacy' ? '导入旧版数据' : '快速开始'}
      view={state.view}
      footer={<SetupFooter state={state} flow={flow} />}
    >
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
    </SetupLayout>
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
