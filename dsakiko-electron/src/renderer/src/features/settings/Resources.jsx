import { useEffect, useState } from 'react'
import { Download } from 'lucide-react'
import { Button } from '../../components/ui/button.jsx'
import { formatBytes } from '../setup/presentation.js'

function VoiceChoices({ option, pending, onSelect }) {
  const choices = option.forms?.filter((form) => form.packageAvailable || form.customAvailable)
  if (!choices?.length) return null
  const multipleForms = option.forms.length > 1
  return (
    <div className="settings-voice-choices">
      {choices.map((form) => (
        <label key={form.id}>
          <span>{multipleForms ? form.name + ' · 回复语音' : '回复语音'}</span>
          <select
            aria-label={(multipleForms ? form.name : option.name) + '的回复语音'}
            value={form.active}
            disabled={pending}
            onChange={(event) => onSelect(form.id, event.target.value)}
          >
            {form.packageAvailable && <option value="package">应用语音</option>}
            {form.customAvailable && <option value="custom">自定义语音</option>}
            <option value="none">不生成语音</option>
          </select>
        </label>
      ))}
    </div>
  )
}

function resourceDetails(option, isVoice) {
  // 版本字段可能同时包含运行环境版本和按日期发布的模型批次。
  const versions = (option.version ?? '')
    .split(' / ')
    .filter((version) => version && !/^\d{4}-\d{2}-\d{2}$/.test(version))
  const details = [
    option.waiting && '已安装，等待生效',
    option.updateAvailable && '有可用更新',
    isVoice && option.installed && '应用语音包',
    ...versions,
    option.installedBytes > 0 && formatBytes(option.installedBytes)
  ].filter(Boolean)
  if (isVoice && !option.installed && option.forms?.length === 1) {
    const [form] = option.forms
    if (form.customBytes > 0) details.push('自定义语音文件', formatBytes(form.customBytes))
  }
  return details.join(' · ')
}

function ResourceCard({ option, onDownload, client, refresh, highlighted }) {
  const [confirm, setConfirm] = useState(false)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState(null)
  const [releaseBytes, setReleaseBytes] = useState(null)
  const isVoice = option.id.startsWith('voice.')
  const details = resourceDetails(option, isVoice)
  const packageUsers = option.forms?.filter((form) => form.active === 'package') ?? []
  const inUse = packageUsers.length > 0
  const affectedNames =
    option.forms?.length > 1 ? packageUsers.map((form) => form.name).join('、') : option.name

  useEffect(() => {
    if (!confirm || inUse) return
    let current = true
    void client.setup
      .previewVoiceRemoval(option.id)
      .then(({ bytes }) => {
        if (current) setReleaseBytes(bytes)
      })
      .catch((error) => {
        if (current) setError(error.message)
      })
    return () => {
      current = false
    }
  }, [client, confirm, inUse, option.id])

  async function select(formId, source) {
    setPending(true)
    setError(null)
    try {
      await client.setup.selectVoice(option.id, formId, source)
      await refresh()
    } catch (error) {
      setError(error.message)
    } finally {
      setPending(false)
    }
  }
  async function remove() {
    setPending(true)
    setError(null)
    try {
      await client.setup.removeVoice(option.id)
      setConfirm(false)
      await refresh()
    } catch (error) {
      setError(error.message)
    } finally {
      setPending(false)
    }
  }
  function download() {
    onDownload(option.id, option.updateAvailable ? '更新附加资源' : '安装附加资源')
  }
  function confirmRemoval() {
    setReleaseBytes(null)
    setError(null)
    setConfirm(true)
  }
  return (
    <article
      className="settings-card"
      id={'settings-resource-' + option.id}
      data-highlighted={highlighted || undefined}
      tabIndex={highlighted ? -1 : undefined}
    >
      <div className="settings-card-title">
        <div>
          <h4>{option.name}</h4>
          {details && <p>{details}</p>}
        </div>
        <div className="settings-row-actions">
          {!option.ready && option.available && (
            <Button size="sm" variant="outline" onClick={download}>
              <Download />
              {option.updateAvailable ? '更新' : '安装'}
              {isVoice && '应用语音包'}
            </Button>
          )}
          {isVoice && option.installed && !confirm && (
            <Button size="sm" variant="ghost" onClick={confirmRemoval} disabled={pending}>
              移除应用语音包
            </Button>
          )}
        </div>
      </div>
      {option.unavailableReason && !option.ready && <p>{option.unavailableReason}</p>}
      <VoiceChoices option={option} pending={pending} onSelect={select} />
      {confirm && (
        <div className="settings-confirm">
          <p>
            {inUse ? (
              <>{affectedNames}正在使用应用语音。请先在上方切换回复语音。</>
            ) : (
              <>
                移除应用语音包会保留自定义语音及其使用的文件。
                {releaseBytes === 0 && '预计不释放磁盘空间。'}
                {releaseBytes > 0 && '预计释放 ' + formatBytes(releaseBytes) + '。'}
              </>
            )}
          </p>
          <Button size="sm" variant="ghost" onClick={() => setConfirm(false)}>
            取消
          </Button>
          <Button size="sm" variant="destructive" onClick={remove} disabled={pending || inUse}>
            确认移除
          </Button>
        </div>
      )}
      {error && (
        <p className="settings-error" role="alert">
          {error}
        </p>
      )}
    </article>
  )
}

const headings = ['speech', 'voices', 'asr']

/** @param {object} props 设置目录、共享任务和导航。 @returns {import('react').ReactElement} 资源状态与管理动作。 */
export function Resources({ client, target, onDownload, setupState, setup, onShowDownloads }) {
  const catalog = setupState.managedCatalog
  const problem = setupState.managedCatalogProblem
  const hasCatalog = Boolean(catalog)
  const job = setupState.job?.kind === 'download' ? setupState.job : null

  useEffect(() => {
    if (target && hasCatalog)
      document.getElementById('settings-resource-' + target)?.scrollIntoView({ block: 'nearest' })
  }, [hasCatalog, target])

  async function refresh() {
    await Promise.all([
      setup.refreshManagedCatalog({ localChanged: true }),
      setup.refreshFeatures()
    ])
  }

  return (
    <>
      {!catalog && setupState.managedCatalogChecking && (
        <p className="settings-status-delayed" role="status">
          正在读取资源…
        </p>
      )}
      {problem && (
        <p className="settings-error" role="alert">
          {problem}
        </p>
      )}
      {job && ['running', 'paused', 'failed'].includes(job.status) && (
        <Button variant="outline" size="sm" onClick={onShowDownloads}>
          <Download size={16} />
          查看下载任务
        </Button>
      )}
      {headings.map((id) => {
        const group = catalog?.groups.find((item) => item.id === id)
        return (
          group && (
            <section className="settings-stack" key={id}>
              <h3>{group.name}</h3>
              {id === 'voices' && (
                <p>
                  安装应用语音包后，已有的自定义语音仍可选用。选择「不生成语音」会保留语音文件。
                </p>
              )}
              {group.options.map((option) => (
                <ResourceCard
                  key={option.id}
                  option={option}
                  highlighted={target === option.id}
                  client={client}
                  refresh={refresh}
                  onDownload={onDownload}
                />
              ))}
            </section>
          )
        )
      })}
    </>
  )
}

/** @param {object} props 资源目标、应用任务容器与下载受理后的导航。 @returns {import('react').ReactElement} 设置中的下载大小与安装确认。 */
export function ResourceDownloadConfirmation({ title, target, client, setup, onClose, onStarted }) {
  const [detail, setDetail] = useState(null)
  const [pending, setPending] = useState(false)
  const [problem, setProblem] = useState(null)
  useEffect(() => {
    let current = true
    void client.setup
      .getCatalog({ selection: [target], managed: true })
      .then((catalog) => {
        if (!current) return
        setDetail({
          item: catalog.groups
            .flatMap((group) => group.options)
            .find((option) => option.id === target),
          bytes: catalog.selectionDownloadBytes
        })
      })
      .catch((error) => {
        if (current) setProblem(error.message)
      })
    return () => {
      current = false
    }
  }, [client, target])
  async function start() {
    setPending(true)
    setProblem(null)
    try {
      await setup.downloadManaged(target)
      onStarted()
    } catch (error) {
      setProblem(error.message)
    } finally {
      setPending(false)
    }
  }
  function close() {
    if (!pending) onClose()
  }
  return (
    <div className="settings-resource-download-content" data-dialog-busy={pending || undefined}>
      <header>
        <h2 tabIndex={-1} data-dialog-focus>
          {title}
        </h2>
        {detail?.item && detail.bytes != null && (
          <p role="status">
            {detail.item?.name} · 预计下载 {formatBytes(detail.bytes)}
          </p>
        )}
      </header>
      {!detail && !problem && (
        <p className="settings-status-delayed" role="status">
          正在检查下载大小…
        </p>
      )}
      {detail && (!detail.item || detail.bytes == null) && (
        <p className="settings-error" role="alert">
          暂时无法确认下载大小。请返回附加资源并重试。
        </p>
      )}
      {problem && (
        <p className="settings-error" role="alert">
          {problem}
        </p>
      )}
      <footer className="settings-row-actions">
        <Button variant="outline" onClick={close} disabled={pending}>
          取消
        </Button>
        <Button onClick={start} disabled={pending || !detail?.item || detail.bytes == null}>
          {pending ? '正在准备…' : '开始下载'}
        </Button>
      </footer>
    </div>
  )
}
