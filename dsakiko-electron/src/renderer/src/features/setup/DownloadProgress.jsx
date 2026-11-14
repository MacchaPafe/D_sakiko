import {
  CheckCircle2,
  Clock3,
  Download,
  LoaderCircle,
  PauseCircle,
  CircleAlert
} from 'lucide-react'
import { Progress } from '../../components/ui/progress.jsx'
import { previewCharacters } from '../onboarding/characters.js'
import { formatBytes, remainingTime, statusLabel, taskRows } from './presentation.js'

function ResourceStatus({ row }) {
  let Icon = LoaderCircle
  if (row.status === 'ready') Icon = CheckCircle2
  else if (row.status === 'waiting') Icon = Clock3
  else if (row.status === 'downloading') Icon = Download
  else if (row.status === 'paused') Icon = PauseCircle
  else if (row.status === 'failed') Icon = CircleAlert
  const character = previewCharacters.find((item) => 'voice.' + item.id === row.id)
  return (
    <div className="resource-choice-line resource-task-row">
      <Icon
        size={20}
        aria-hidden="true"
        className={Icon === LoaderCircle ? 'welcome-spinner' : undefined}
      />
      {character && (
        <img className="resource-avatar" src={character.avatar} alt="" width="28" height="28" />
      )}
      <span className="resource-label">
        <span className="resource-name">{row.name}</span>
        {row.id === 'gsv' && <span className="resource-description">让角色开口说话</span>}
      </span>
      <span className="resource-badge" data-tone={row.status}>
        {statusLabel(row.status)}
      </span>
    </div>
  )
}

/** @param {{state: import('./setup-flow.js').SetupFlowState}} props 真实任务及功能快照。 @returns {import('react').ReactElement} 字节进度与安装就绪分开表达的下载详情。 */
export function DownloadProgress({ state }) {
  const job = state.job
  const rows = taskRows(job)
  const engine = rows.filter((row) => !row.id.startsWith('voice.'))
  const voices = rows.filter((row) => row.id.startsWith('voice.'))
  const total = job.download?.totalBytes ?? 0
  const bytes = Math.min(total, job.download?.bytes ?? 0)
  const percent = total > 0 ? Math.floor((bytes / total) * 100) : null
  const downloading = job.status === 'running' && job.phase === 'downloading'
  const speed = downloading ? job.download?.bytesPerSecond : null
  const remaining = downloading ? remainingTime(job.download?.remainingSeconds) : null
  const phase = job.status === 'running' ? job.phase : job.status
  let title = '正在准备语音功能'
  if (job.status === 'paused') title = '语音资源准备已暂停'
  else if (job.status === 'failed') title = '语音资源准备遇到问题'
  else if (job.status === 'succeeded') title = '资源准备完成'
  else if (job.status === 'planned') title = '等待开始资源准备'
  const readyNames = previewCharacters
    .filter((character) =>
      state.features?.characters.some((item) => item.characterId === character.id && item.ready)
    )
    .map((character) => character.name)
  return (
    <>
      <header className="setup-heading">
        <h1>{title}</h1>
      </header>
      <section className="download-summary" aria-label="下载进度">
        <div className="download-summary-line">
          <strong>{statusLabel(phase)}</strong>
          <span>{percent == null ? '无需下载' : `已下载 ${percent}%`}</span>
        </div>
        <Progress value={percent} aria-label="资源下载字节进度" />
        <div className="download-details">
          <span>
            已下载 {formatBytes(bytes)} / {formatBytes(total)}
          </span>
          <span>
            {speed != null && `${formatBytes(speed)}/s`}
            {speed != null && remaining && ' · '}
            {remaining}
          </span>
        </div>
      </section>
      {job.problem && (
        <p className="setup-problem" role="alert">
          {job.problem.message}
        </p>
      )}
      <section className="resource-group" aria-label="资源准备状态">
        <h2>角色说话</h2>
        <div className="resource-card">
          {engine.length > 0 && (
            <div className="resource-engine">
              {engine.map((row) => (
                <ResourceStatus key={row.id} row={row} />
              ))}
            </div>
          )}
          {voices.length > 0 && (
            <div className="resource-voices">
              <div className="resource-section-label">
                <h3>角色语音</h3>
              </div>
              {voices.map((row) => (
                <ResourceStatus key={row.id} row={row} />
              ))}
            </div>
          )}
        </div>
      </section>
      {job.status === 'succeeded' && readyNames.length > 0 && (
        <p className="setup-success" role="status">
          {readyNames.join('、')}的语音已就绪。
        </p>
      )}
      {job.warnings.length > 0 && (
        <ul className="setup-problem">
          {job.warnings.map((warning, index) => (
            <li key={index}>{warning.message || '部分资源需要检查。'}</li>
          ))}
        </ul>
      )}
    </>
  )
}
