import { useEffect, useRef, useState } from 'react'
import * as DropdownMenu from '@radix-ui/react-dropdown-menu'
import { Download, MoreHorizontal } from 'lucide-react'
import { Button } from '../../components/ui/button.jsx'
import { formatBytes } from '../setup/presentation.js'

function resourceDetails(option, isVoice) {
  // 版本字段可能同时包含运行环境版本和按日期发布的模型批次。
  const versions = (option.version ?? '')
    .split(' / ')
    .filter((version) => version && !/^\d{4}-\d{2}-\d{2}$/.test(version))
  const details = [
    option.waiting && '已安装，等待生效',
    option.updateAvailable && '有可用更新',
    isVoice && option.installed && '推荐语音包',
    ...versions,
    option.installedBytes > 0 && formatBytes(option.installedBytes)
  ].filter(Boolean)
  if (isVoice && !option.installed && option.forms?.length === 1) {
    const [form] = option.forms
    if (form.materialBytes > 0) details.push('当前语音文件', formatBytes(form.materialBytes))
  }
  return details.join(' · ')
}

function ResourceCard({ option, onDownload, client, refresh, highlighted, menuContainer }) {
  const [confirmation, setConfirmation] = useState(null)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState(null)
  const [estimate, setEstimate] = useState(null)
  const moreButton = useRef(null)
  const cancelButton = useRef(null)
  const isVoice = option.id.startsWith('voice.')
  const confirm = confirmation === option
  const availableBytes = estimate?.option === option ? estimate.bytes : null
  const releaseBytes = estimate?.confirm ? availableBytes : null
  const details = resourceDetails(option, isVoice)

  useEffect(() => {
    if (!isVoice || !option.installed) return
    let current = true
    void client.setup
      .previewVoiceRemoval(option.id)
      .then(({ bytes }) => {
        if (current) setEstimate({ option, confirm, bytes })
      })
      .catch((error) => {
        if (!current) return
        setEstimate(null)
        if (confirm) setError(error.message)
      })
    return () => {
      current = false
    }
  }, [client, confirm, isVoice, option])

  useEffect(() => {
    if (confirm) cancelButton.current?.focus({ preventScroll: true })
  }, [confirm])

  async function clean() {
    setPending(true)
    setError(null)
    try {
      // 等待确认期间可能有角色开始使用这些材料，再核对一次当前引用。
      const { bytes } = await client.setup.previewVoiceRemoval(option.id)
      setEstimate({ option, confirm: true, bytes })
      if (bytes <= 0) return
      await client.setup.removeVoice(option.id)
      setEstimate(null)
      setConfirmation(null)
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
    setEstimate({ option, confirm: false, bytes: availableBytes })
    setError(null)
    setConfirmation(option)
  }
  function cancelRemoval() {
    setConfirmation(null)
    setError(null)
    requestAnimationFrame(() => moreButton.current?.focus({ preventScroll: true }))
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
            <Button size="sm" variant="outline" onClick={download} disabled={pending}>
              <Download />
              {option.updateAvailable ? '更新' : '安装'}
              {isVoice && '推荐语音包'}
            </Button>
          )}
          {isVoice && option.installed && availableBytes > 0 && !confirm && menuContainer && (
            <DropdownMenu.Root modal={false}>
              <DropdownMenu.Trigger asChild>
                <Button
                  ref={moreButton}
                  size="icon-sm"
                  variant="ghost"
                  aria-label={option.name + '的更多操作'}
                  title="更多操作"
                >
                  <MoreHorizontal aria-hidden="true" />
                </Button>
              </DropdownMenu.Trigger>
              {/* 将菜单放在原生浮窗内部，同时避开资源列表的滚动裁剪。 */}
              <DropdownMenu.Portal container={menuContainer}>
                <DropdownMenu.Content
                  className="settings-resource-menu"
                  align="end"
                  sideOffset={4}
                  collisionBoundary={menuContainer}
                  collisionPadding={8}
                  hideWhenDetached
                >
                  <DropdownMenu.Item onSelect={confirmRemoval}>
                    清理未使用的语音包文件
                  </DropdownMenu.Item>
                </DropdownMenu.Content>
              </DropdownMenu.Portal>
            </DropdownMenu.Root>
          )}
        </div>
      </div>
      {option.unavailableReason && !option.ready && <p>{option.unavailableReason}</p>}

      {confirm && (
        <div className="settings-confirm">
          <p role="status">
            {releaseBytes === null && !error && '正在核对可释放空间…'}
            {releaseBytes === 0 && '当前没有可清理的语音包文件。'}
            {releaseBytes > 0 &&
              `预计释放 ${formatBytes(releaseBytes)}。角色配置中使用的文件会保留。`}
          </p>
          <Button
            ref={cancelButton}
            size="sm"
            variant="ghost"
            onClick={cancelRemoval}
            disabled={pending}
          >
            {releaseBytes === 0 ? '关闭' : '取消'}
          </Button>
          {releaseBytes > 0 && (
            <Button size="sm" variant="destructive" onClick={clean} disabled={pending}>
              {pending ? '正在清理…' : '确认清理'}
            </Button>
          )}
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
export function Resources({
  client,
  target,
  onDownload,
  setupState,
  setup,
  onShowDownloads,
  menuContainer
}) {
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
                <p>在角色管理中可进一步修改角色的语音。</p>
              )}
              {group.options.map((option) => (
                <ResourceCard
                  key={option.id}
                  option={option}
                  highlighted={target === option.id}
                  client={client}
                  refresh={refresh}
                  onDownload={onDownload}
                  menuContainer={menuContainer}
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
export function ResourceDownloadConfirmation({
  title,
  target,
  formId,
  client,
  setup,
  onClose,
  onStarted
}) {
  const [detail, setDetail] = useState(null)
  const [pending, setPending] = useState(false)
  const [problem, setProblem] = useState(null)
  const [review, setReview] = useState(null)
  const [selection, setSelection] = useState({})
  useEffect(() => {
    let current = true
    async function read() {
      const catalog = await client.setup.getCatalog({ selection: [target], managed: true })
      const item = catalog.groups
        .flatMap((group) => group.options)
        .find((option) => option.id === target)
      if (target.startsWith('voice.')) {
        const value = await client.setup.reviewVoiceUpdate(
          target,
          formId ?? null,
          Boolean(item?.installed && item?.ready)
        )
        if (!current) return
        const selected = {}
        for (const form of value.forms)
          selected[form.id] = Object.fromEntries(
            form.changes.map((change) => [change.key, !change.modified])
          )
        setReview(value)
        setSelection(selected)
      }
      if (current) setDetail({ item, bytes: catalog.selectionDownloadBytes })
    }
    read().catch((error) => {
      if (current) setProblem(error.message)
    })
    return () => {
      current = false
    }
  }, [client, target, formId])
  function toggle(formId, key) {
    setSelection((current) => ({
      ...current,
      [formId]: { ...current[formId], [key]: !current[formId][key] }
    }))
  }
  async function start() {
    setPending(true)
    setProblem(null)
    try {
      let voiceUpdate
      if (review) {
        voiceUpdate = {
          id: review.id,
          characterId: review.characterId,
          contentId: review.contentId,
          forms: review.forms.map((form) => ({
            id: form.id,
            items: form.changes
              .filter((change) => selection[form.id][change.key])
              .map(({ key, before }) => ({ key, before }))
          }))
        }
        if (detail.item.ready) {
          await client.setup.applyVoiceUpdate(voiceUpdate)
          await setup.refreshFeatures()
          void setup.refreshManagedCatalog({ localChanged: true })
          onClose()
          return
        }
      }
      await setup.downloadManaged(target, voiceUpdate)
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
            {detail.item?.name}
            {review?.version && ' · v' + review.version} · 预计下载 {formatBytes(detail.bytes)}
          </p>
        )}
      </header>
      {review && (
        <div className="voice-update-items">
          {review.forms.map((form) => (
            <fieldset key={form.id} disabled={pending}>
              <legend>
                {form.name}
                {review.forms.length > 1 ? ' · ' + form.id : ''}
              </legend>
              {!form.changes.length && <p>当前材料与语音包一致。</p>}
              {form.changes.map((item) => (
                <label key={item.key}>
                  <input
                    type="checkbox"
                    checked={!!selection[form.id]?.[item.key]}
                    onChange={() => toggle(form.id, item.key)}
                  />
                  <span>
                    {item.label}
                    {item.removed && '（移除）'}
                    {item.modified && ' · 已自行修改'}
                    <small>当前 · {item.currentMaterial}</small>
                    <small>新版 · {item.newMaterial}</small>
                  </span>
                </label>
              ))}
            </fieldset>
          ))}
        </div>
      )}
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
          {pending
            ? '正在准备…'
            : review
              ? detail?.item.ready
                ? '应用到人格'
                : '下载并应用'
              : '开始下载'}
        </Button>
      </footer>
    </div>
  )
}
