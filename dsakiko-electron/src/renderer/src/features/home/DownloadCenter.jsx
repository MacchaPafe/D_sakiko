import { useEffect, useRef, useState } from 'react'
import { ArrowLeft, AudioLines, ChevronRight, Download, Shirt, X } from 'lucide-react'
import { Button } from '../../components/ui/button.jsx'
import { Progress } from '../../components/ui/progress.jsx'
import { formatBytes, statusLabel, taskRows } from '../setup/presentation.js'
import { pendingDownloads } from './download-jobs.js'
import './download-center.css'

const categories = [
  { id: 'all', label: '全部' },
  { id: 'outfits', label: 'Live2D 服装' },
  { id: 'resources', label: '附加资源' }
]

const outfitStatus = {
  running: '正在下载',
  installing: '正在校验和安装',
  interrupted: '等待继续',
  failed: '下载或安装失败',
  succeeded: '已安装'
}

function resourceDescription(job) {
  return (
    taskRows(job)
      .map((row) => (row.id.startsWith('voice.') ? row.name + '语音包' : row.name))
      .join('、') || '附加资源'
  )
}

function resourceTarget(job) {
  const components = job.download?.components ?? []
  return (
    components.find((item) => item.id.startsWith('voice.'))?.id ?? components[0]?.id.split('.')[0]
  )
}

function outfitTargetName(job, roles, definitions) {
  const role = roles.find((item) => item.id === job.target.characterId)
  const definition = definitions.get(job.target.characterId)
  const form = definition?.forms.find((item) => item.id === job.target.formId)
  let formName = form?.displayName ?? job.target.formId
  if (role?.bangDreamId === 'sakiko' && job.target.formId === 'black') formName = '黑祥'
  if (role?.bangDreamId === 'sakiko' && job.target.formId === 'white') formName = '白祥'
  return [role?.displayName ?? role?.name ?? job.target.characterId, formName]
    .filter(Boolean)
    .join(' · ')
}

function describeProgress(job, outfit) {
  if (outfit) {
    if (!job.progress?.total) return null
    const { completed, total } = job.progress
    const description =
      job.item.version === 'v2'
        ? `${completed} / ${total} 个文件`
        : formatBytes(completed) + ' / ' + formatBytes(total)
    return { completed, total, description }
  }
  const { bytes, totalBytes } = job.download ?? {}
  if (!totalBytes) return null
  return {
    completed: bytes,
    total: totalBytes,
    description: formatBytes(bytes) + ' / ' + formatBytes(totalBytes)
  }
}

function DownloadRow({ job, roles, definitions, pending, error, onControl, onDestination }) {
  const outfit = 'item' in job
  const Icon = outfit ? Shirt : AudioLines
  const title = outfit ? job.item.name : resourceDescription(job)
  const completed = job.status === 'succeeded'
  const installing =
    job.status === 'installing' ||
    (job.status === 'running' && ['validating', 'installing', 'committing'].includes(job.phase))
  const status = outfit
    ? outfitStatus[job.status]
    : statusLabel(job.status === 'running' ? job.phase : job.status)
  const progress = completed ? null : describeProgress(job, outfit)
  const pause = job.status === 'running' && !installing
  const resume = ['paused', 'failed', 'interrupted'].includes(job.status)
  const problem = error ?? job.problem?.message

  function control(event) {
    void onControl(job, event.currentTarget.value)
  }
  function destination() {
    onDestination(job)
  }
  return (
    <li className="download-center-row">
      <span className="download-center-icon">
        <Icon size={18} aria-hidden="true" />
      </span>
      <div className="download-center-item">
        <strong>{title}</strong>
        {outfit && <p>安装到 {outfitTargetName(job, roles, definitions)}</p>}
        <div className="download-center-progress-label">
          <span>{status}</span>
          {progress && <span>{progress.description}</span>}
        </div>
        {!completed && (progress || pause || installing) && (
          <Progress
            value={
              progress
                ? Math.min(100, Math.floor((progress.completed * 100) / progress.total))
                : null
            }
            className="download-center-progress"
            aria-label={title + '下载进度'}
            aria-valuetext={progress?.description}
          />
        )}
        {problem && (
          <p className="outfit-error" role="alert">
            {problem}
          </p>
        )}
      </div>
      <div className="download-center-actions">
        {pause && (
          <Button size="sm" variant="outline" disabled={pending} value="pause" onClick={control}>
            暂停
          </Button>
        )}
        {resume && (
          <Button size="sm" variant="outline" disabled={pending} value="resume" onClick={control}>
            {job.status === 'failed' ? '重试' : '继续'}
          </Button>
        )}
        {(pause || resume) && (
          <Button size="sm" variant="ghost" disabled={pending} value="cancel" onClick={control}>
            取消
          </Button>
        )}
        {completed && (
          <Button size="sm" variant="ghost" onClick={destination}>
            {outfit ? '查看已安装' : '查看资源'}
            <ChevronRight size={14} />
          </Button>
        )}
      </div>
    </li>
  )
}

/** @param {object} props 独立查询结果、任务操作和返回入口。 @returns {import('react').ReactElement} 共享浮窗中的下载任务视图。 */
export function DownloadCenter({
  client,
  setup,
  downloads,
  initial,
  returnTo,
  roles,
  onClose,
  onOutfit,
  onResource
}) {
  const [category, setCategory] = useState(initial)
  const [operations, setOperations] = useState({})
  const [definitions, setDefinitions] = useState(() => new Map())
  const inFlight = useRef(new Set())
  const selectedQueries =
    category === 'all' ? [downloads.outfits, downloads.resources] : [downloads[category]]
  const jobs = selectedQueries.flatMap(({ items }) =>
    items.filter((job) => !('kind' in job) || job.kind === 'download')
  )
  const pending = pendingDownloads(jobs)
  const recent = jobs
    .filter((job) => job.status === 'succeeded')
    .sort((a, b) => (b.completedAt ?? b.expiresAt ?? 0) - (a.completedAt ?? a.expiresAt ?? 0))
    .slice(0, 10)
  const errors = selectedQueries.some((query) => query.error)
  const targetIds = JSON.stringify(
    [
      ...new Set(
        [...pending, ...recent].filter((job) => 'item' in job).map((job) => job.target.characterId)
      )
    ].sort()
  )

  useEffect(() => {
    const ids = JSON.parse(targetIds)
    if (!ids.length) return
    let current = true
    void Promise.allSettled(ids.map((id) => client.characters.get(id))).then((results) => {
      if (!current) return
      setDefinitions(
        new Map(
          results.flatMap((result, index) =>
            result.status === 'fulfilled' ? [[ids[index], result.value]] : []
          )
        )
      )
    })
    return () => {
      current = false
    }
  }, [client, targetIds])

  async function control(job, action) {
    if (inFlight.current.has(job.id)) return
    inFlight.current.add(job.id)
    setOperations((previous) => ({ ...previous, [job.id]: { pending: true } }))
    try {
      if ('item' in job) await client.outfits.control(job.id, action)
      else await setup.controlDownload(job.id, action)
      setOperations((previous) => ({ ...previous, [job.id]: null }))
    } catch (error) {
      setOperations((previous) => ({ ...previous, [job.id]: { error: error.message } }))
    } finally {
      inFlight.current.delete(job.id)
      downloads.refresh()
    }
  }
  function destination(job) {
    if ('item' in job) onOutfit(job.target)
    else onResource(resourceTarget(job))
  }
  function changeCategory(event) {
    setCategory(event.currentTarget.value)
  }
  function renderJob(job) {
    return (
      <DownloadRow
        key={job.id}
        job={job}
        roles={roles}
        definitions={definitions}
        pending={operations[job.id]?.pending}
        error={operations[job.id]?.error}
        onControl={control}
        onDestination={destination}
      />
    )
  }

  return (
    <div className="download-center-layout">
      <header className="download-center-header">
        <h2 tabIndex={-1} data-dialog-focus>
          下载中心
        </h2>
        {returnTo ? (
          <Button variant="ghost" size="sm" onClick={onClose}>
            <ArrowLeft size={16} />
            返回{returnTo}
          </Button>
        ) : (
          <Button variant="ghost" size="icon" aria-label="关闭下载中心" onClick={onClose}>
            <X size={20} />
          </Button>
        )}
      </header>
      <div className="download-center-filters" role="group" aria-label="任务类别">
        {categories.map(({ id, label }) => (
          <Button
            key={id}
            size="sm"
            variant="ghost"
            value={id}
            aria-pressed={category === id}
            onClick={changeCategory}
          >
            {label}
          </Button>
        ))}
      </div>
      <div className="download-center-content">
        {downloads.loading && (
          <p className="download-center-empty" role="status">
            正在读取下载任务…
          </p>
        )}
        {errors && (
          <div className="download-center-query-error" role="status">
            <span>部分下载任务暂时无法读取。</span>
            <Button size="sm" variant="outline" onClick={downloads.refresh}>
              重试
            </Button>
          </div>
        )}
        {!downloads.loading && !pending.length && (
          <div className="download-center-empty">
            <Download size={24} aria-hidden="true" />
            <p>{errors ? '暂无可显示的任务' : '没有待处理的下载'}</p>
          </div>
        )}
        {pending.length > 0 && <ul className="download-center-list">{pending.map(renderJob)}</ul>}
        {recent.length > 0 && (
          <details className="download-center-recent">
            <summary>
              最近完成 <span>{recent.length}</span>
            </summary>
            <ul className="download-center-list">{recent.map(renderJob)}</ul>
          </details>
        )}
      </div>
    </div>
  )
}
