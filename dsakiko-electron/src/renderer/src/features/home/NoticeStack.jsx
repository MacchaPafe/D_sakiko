import { useEffect, useRef, useState } from 'react'
import { Bell, Check, ChevronDown, ChevronUp, Download, X } from 'lucide-react'
import { Button } from '../../components/ui/button.jsx'
import { DownloadProgress } from '../setup/DownloadProgress.jsx'
import { statusLabel } from '../setup/presentation.js'

/** @param {{header: string, content: string, mode?: string, children?: import('react').ReactNode, onDismiss?: () => void}} props 通用卡片材料。 @returns {import('react').ReactElement} 任务和持久通知共用的展示外壳。 */
export function StatusCard({ header, content, mode = 'notice', children, onDismiss }) {
  const Icon = mode === 'success' ? Check : Bell
  return (
    <article className="status-card" data-mode={mode}>
      <div className="status-card-heading">
        <Icon size={18} />
        <strong>{header}</strong>
        {onDismiss && (
          <Button size="icon" variant="ghost" aria-label={'关闭' + header} onClick={onDismiss}>
            <X size={16} />
          </Button>
        )}
      </div>
      {content && <p>{content}</p>}
      {children}
    </article>
  )
}
function NoticeCard({ notice, onDismiss, onSettings }) {
  function dismiss() {
    void onDismiss(notice.id)
  }
  function settings() {
    if (notice.kind === 'voice-ready') onSettings('resources')
    else onSettings('connections')
  }
  let header = '配置大模型连接'
  let content = '当前没有配置大模型连接。'
  let action = '前往配置'
  if (notice.kind === 'voice-ready') {
    header = notice.data.characterName + '的语音已就绪'
    content = notice.data.enabled ? '接下来的对话可以使用角色语音。' : '语音资源准备完成。'
    action = '语音设置'
  } else if (notice.data.connectionState === 'builtin') content = '已添加内置连接，可以开始对话。'
  return (
    <StatusCard
      header={header}
      content={content}
      mode={notice.kind === 'voice-ready' ? 'success' : 'notice'}
      onDismiss={dismiss}
    >
      <div className="status-card-actions">
        <Button variant="outline" size="sm" onClick={settings}>
          {action}
        </Button>
        <Button size="sm" onClick={dismiss}>
          知道了
        </Button>
      </div>
    </StatusCard>
  )
}

/** @param {{notices: object[], onDismiss: (id: string) => void, setup: object, setupState: object}} props 后端通知和应用级下载任务。 @returns {import('react').ReactElement} 最旧通知在前；折叠不等于关闭，下载任务独立存在。 */
export function NoticeStack({ notices, onDismiss, onSettings, setup, setupState }) {
  const [expanded, setExpanded] = useState(false)
  const [details, setDetails] = useState(false)
  const [dismissedJob, setDismissedJob] = useState(() => {
    try {
      return localStorage.getItem('sakiko.dismissed-download')
    } catch {
      return null
    }
  })
  const container = useRef(null)
  useEffect(() => {
    function outside(event) {
      if (!container.current?.contains(event.target)) {
        setExpanded(false)
        setDetails(false)
      }
    }
    document.addEventListener('pointerdown', outside)
    return () => document.removeEventListener('pointerdown', outside)
  }, [])
  function toggle() {
    setExpanded((value) => !value)
  }
  function toggleDetails() {
    setDetails((value) => !value)
  }
  function pause() {
    void setup.control('pause')
  }
  function resume() {
    void setup.control('resume')
  }
  const job = setupState.job?.kind === 'download' ? setupState.job : null
  const completed = job && ['succeeded', 'cancelled'].includes(job.status)
  const showJob = job && (!completed || dismissedJob !== job.id)
  function dismissDownload() {
    if (!completed) return
    setDismissedJob(job.id)
    setDetails(false)
    try {
      localStorage.setItem('sakiko.dismissed-download', job.id)
    } catch {
      /* 存储不可用时仍允许本次关闭。 */
    }
  }
  const visible = expanded ? notices : notices.slice(0, 1)
  return (
    <div className="notice-area" ref={container}>
      {details && showJob && (
        <section className="home-download-panel" aria-label="后台下载详情">
          <Button
            variant="ghost"
            size="icon"
            className="download-close"
            aria-label="收起下载详情"
            onClick={toggleDetails}
          >
            <X />
          </Button>
          <DownloadProgress state={setupState} compact />
          {job.status === 'running' && (
            <Button variant="outline" disabled={Boolean(setupState.pending)} onClick={pause}>
              暂停下载
            </Button>
          )}
          {['paused', 'failed', 'planned'].includes(job.status) && (
            <Button disabled={Boolean(setupState.pending)} onClick={resume}>
              {job.status === 'failed' ? '重试下载' : '继续下载'}
            </Button>
          )}
        </section>
      )}
      {notices.length > 0 && (
        <div
          className={
            'notice-stack' +
            (expanded ? ' expanded' : '') +
            (!expanded && notices.length > 1 ? ' stacked' : '')
          }
        >
          <div className="notice-scroll">
            {visible.map((notice) => (
              <NoticeCard
                key={notice.id}
                notice={notice}
                onDismiss={onDismiss}
                onSettings={onSettings}
              />
            ))}
          </div>
          {(notices.length > 1 || expanded) && (
            <button
              className="notice-expand"
              aria-label={expanded ? '收起通知' : '展开通知'}
              aria-expanded={expanded}
              onClick={toggle}
            >
              {expanded ? <ChevronDown size={16} /> : <ChevronUp size={16} />}
            </button>
          )}
        </div>
      )}
      {showJob && (
        <div className="download-entry-row">
          <Button
            variant="ghost"
            className="download-entry"
            onClick={toggleDetails}
            aria-expanded={details}
          >
            <Download size={16} />
            <span>语音资源</span>
            <span>{statusLabel(job.status)}</span>
          </Button>
          {completed && (
            <Button
              variant="ghost"
              size="icon"
              aria-label="关闭已完成下载入口"
              onClick={dismissDownload}
            >
              <X size={16} />
            </Button>
          )}
        </div>
      )}
    </div>
  )
}
