import { characterFormName } from './character-form.js'
import { useCallback, useEffect, useRef, useState } from 'react'
import * as DropdownMenu from '@radix-ui/react-dropdown-menu'
import { Download, FolderOpen, MoreHorizontal, RefreshCw, Shirt, X } from 'lucide-react'
import { Button } from '../../components/ui/button.jsx'
import { Input } from '../../components/ui/fields.jsx'
import { SelectField } from '../../components/ui/SelectField.jsx'
import { Tabs, TabList, Tab, TabPanel } from '../../components/ui/segmented-tabs.jsx'
import { desktopHost } from '../../host/desktop.js'
import { formatBytes } from '../setup/presentation.js'
import { pendingDownloads } from './download-jobs.js'
import { CharacterAvatar } from './CharacterAvatar.jsx'
import './outfit-manager.css'

const pageSize = 24
const catalogSources = { v2: 'bestdori', v3: 'modelscope' }

function savedVersion() {
  try {
    return localStorage.getItem('sakiko.outfits.version') === 'v3' ? 'v3' : 'v2'
  } catch {
    return 'v2'
  }
}

function OutfitDetailsForm({
  title,
  submitLabel,
  submitVariant = 'default',
  children,
  disabled,
  onSubmit,
  onClose
}) {
  const [pending, setPending] = useState(false)
  const [error, setError] = useState(null)
  function close() {
    if (!pending) onClose()
  }
  async function submit(event) {
    event.preventDefault()
    if (disabled || pending) return
    setPending(true)
    setError(null)
    try {
      await onSubmit()
      onClose()
    } catch (failure) {
      setError(failure.message)
      setPending(false)
    }
  }

  return (
    <form className="outfit-detail-form" onSubmit={submit} data-dialog-busy={pending || undefined}>
      <h2 tabIndex={-1} data-dialog-focus>
        {title}
      </h2>
      {children}
      {error && (
        <p role="alert" className="outfit-error">
          {error}
        </p>
      )}
      <footer>
        <Button type="button" variant="outline" disabled={pending} onClick={close}>
          取消
        </Button>
        <Button type="submit" variant={submitVariant} disabled={disabled || pending}>
          {pending ? '正在处理…' : submitLabel}
        </Button>
      </footer>
    </form>
  )
}

/**
 * @param {object} props 本地角色、业务连接与保存后的刷新动作。
 * @returns {import('react').ReactElement} 共享浮窗中的角色归属表单。
 */
export function CharacterAffiliationForm({ client, role, onClose, onSaved }) {
  const [directory, setDirectory] = useState(null)
  const [error, setError] = useState(null)
  const [mode, setMode] = useState(role.bangDreamId === null ? 'other' : 'bang-dream')
  const [selected, setSelected] = useState(role.bangDreamId ?? '')
  useEffect(() => {
    let current = true
    void client.outfits.listCharacters().then(
      (result) => {
        if (current) setDirectory(result)
      },
      (failure) => {
        if (current) setError(failure.message)
      }
    )
    return () => {
      current = false
    }
  }, [client])

  function changeMode(event) {
    setMode(event.currentTarget.value)
  }
  async function save() {
    await client.outfits.setCharacter(role.id, mode === 'other' ? null : selected)
    onSaved()
  }
  const roleName = role.displayName ?? role.name
  let options = []
  if (directory) {
    options = directory.map((item) => {
      const keywords = new Set([item.id, item.name, ...item.aliases])
      keywords.delete(item.displayName)
      return {
        value: item.id,
        label: item.displayName,
        group: item.band ?? '其他 BanG Dream! 角色',
        keywords: [...keywords]
      }
    })
  } else if (selected && roleName) {
    // 目录尚未返回的几毫秒内先用当前角色名称兜底，避免输入框由空白闪烁跳变。
    options = [{ value: selected, label: roleName, keywords: [selected, roleName] }]
  }

  return (
    <OutfitDetailsForm
      title="编辑角色归属"
      submitLabel="保存归属"
      disabled={mode === 'bang-dream' && !selected}
      onSubmit={save}
      onClose={onClose}
    >
      <p className="outfit-detail-description">
        {mode === 'bang-dream'
          ? '关联后可匹配头像、快捷下载 Live2D 服装。'
          : `「${roleName}」设为其他角色时，可通过导入本地模型添加服装。`}
      </p>
      <fieldset className="outfit-field outfit-form-picker outfit-affiliation-picker">
        <legend>角色归属</legend>
        <div className="outfit-form-choices">
          <Button
            type="button"
            variant="ghost"
            size="sm"
            value="bang-dream"
            aria-pressed={mode === 'bang-dream'}
            onClick={changeMode}
          >
            BanG Dream! 角色
          </Button>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            value="other"
            aria-pressed={mode === 'other'}
            onClick={changeMode}
          >
            其他角色
          </Button>
        </div>
      </fieldset>
      {mode === 'bang-dream' && (
        <>
          <label className="outfit-field outfit-affiliation-character">
            对应角色
            <SelectField
              label="对应角色"
              options={options}
              value={selected}
              onChange={setSelected}
              disabled={!directory}
              placeholder="选择角色"
            />
          </label>
          {error && (
            <p role="alert" className="outfit-error">
              {error}
            </p>
          )}
        </>
      )}
    </OutfitDetailsForm>
  )
}

function Thumbnail({ load, className }) {
  const image = useRef(null)
  const [url, setUrl] = useState(null)
  useEffect(() => {
    if (!load) return
    let current = true
    let release
    const observer = new IntersectionObserver(
      (entries) => {
        if (!entries.some((entry) => entry.isIntersecting)) return
        observer.disconnect()
        void load()
          .then((result) => {
            if (!result) return
            release = result.release
            if (current) setUrl(result.url)
            else release?.()
          })
          .catch(() => {})
      },
      { rootMargin: '180px' }
    )
    observer.observe(image.current)
    return () => {
      current = false
      observer.disconnect()
      release?.()
    }
  }, [load])
  return (
    <div ref={image} className={'outfit-thumbnail ' + className}>
      {url ? <img src={url} alt="" /> : <Shirt aria-hidden="true" />}
    </div>
  )
}

function InstalledOutfit({ client, model, isDefault, current, canTry, onAction, menuContainer }) {
  const trigger = useRef(null)
  const [load] = useState(() =>
    model.thumbnail
      ? async () => {
          const access = await client.assets.access(model.presentation.model)
          const url = new URL(access.url)
          url.searchParams.set('file', model.thumbnail)
          return {
            url: url.href,
            release: () => void client.assets.releaseAccess(access.leaseId).catch(() => {})
          }
        }
      : null
  )
  function selectAction(action) {
    // 切到详情前先把焦点记在触发按钮上，返回列表时菜单项已卸载。
    trigger.current?.focus({ preventScroll: true })
    onAction(action, model)
  }
  return (
    <li className="outfit-installed-row">
      <Thumbnail load={load} className="outfit-thumbnail-small" />
      <div className="outfit-row-text">
        <div className="outfit-row-title">
          <strong>{model.name}</strong>
          {model.version && (
            <span className="outfit-version-badge">{model.version.toUpperCase()}</span>
          )}
        </div>
        {(current || isDefault) && (
          <div className="outfit-row-labels">
            {current && <span className="outfit-badge outfit-badge-current">当前对话在用</span>}
            {isDefault && <span className="outfit-badge">新对话默认</span>}
          </div>
        )}
      </div>
      {canTry && !current && (
        <Button variant="outline" size="sm" onClick={() => onAction('try', model)}>
          换上
        </Button>
      )}
      <DropdownMenu.Root modal={false}>
        <DropdownMenu.Trigger asChild>
          <Button
            ref={trigger}
            className="outfit-menu-trigger"
            variant="ghost"
            size="icon-sm"
            aria-label={'管理 ' + model.name}
          >
            <MoreHorizontal size={18} />
          </Button>
        </DropdownMenu.Trigger>
        {/* 原生模态框外的节点不可交互，菜单须挂在当前浮窗的视图内。 */}
        <DropdownMenu.Portal container={menuContainer}>
          <DropdownMenu.Content
            className="outfit-menu"
            side="left"
            sideOffset={6}
            collisionBoundary={menuContainer}
            collisionPadding={8}
            hideWhenDetached
          >
            <DropdownMenu.Item onSelect={() => selectAction('rename')}>重命名</DropdownMenu.Item>
            <DropdownMenu.Item onSelect={() => selectAction('default')} disabled={isDefault}>
              设为新对话默认
            </DropdownMenu.Item>
            <DropdownMenu.Item
              className="outfit-menu-delete"
              onSelect={() => selectAction('delete')}
              disabled={isDefault}
              title={isDefault ? '请先另设默认服装' : undefined}
            >
              删除
            </DropdownMenu.Item>
          </DropdownMenu.Content>
        </DropdownMenu.Portal>
      </DropdownMenu.Root>
    </li>
  )
}

function OutfitFormPicker({ options, value, onChange }) {
  if (options.length < 2) return null
  if (options.length > 3) {
    return (
      <div className="outfit-target-form">
        <SelectField label="形态" value={value} onChange={onChange} options={options} />
      </div>
    )
  }

  function choose(event) {
    onChange(event.currentTarget.value)
  }
  return (
    <div className="outfit-form-choices" role="group" aria-label="形态">
      {options.map((option) => (
        <Button
          key={option.value}
          variant="ghost"
          size="sm"
          value={option.value}
          aria-pressed={value === option.value}
          onClick={choose}
        >
          {option.label}
        </Button>
      ))}
    </div>
  )
}

function DownloadOutfit({ client, target, item, status, tryModel, onDownload, onTry }) {
  const [load] = useState(() =>
    item.thumbnail
      ? async () => {
          const url = await client.outfits.getThumbnail(target, item.source, item.id)
          return url && { url }
        }
      : null
  )
  function download() {
    if (tryModel) onTry('try', tryModel)
    else onDownload(item)
  }
  let actionLabel = status || '下载'
  if (tryModel) actionLabel = '换上'
  return (
    <li className="outfit-card">
      <Thumbnail load={load} className="outfit-thumbnail-card" />
      <div className="outfit-card-content">
        <strong title={item.name}>{item.name}</strong>
        {item.sizeBytes > 0 && <span>{formatBytes(item.sizeBytes)}</span>}
        <Button
          variant="outline"
          size="sm"
          disabled={Boolean(status) && !tryModel}
          onClick={download}
        >
          {!status && <Download size={14} />}
          {actionLabel}
        </Button>
      </div>
    </li>
  )
}

const jobLabels = {
  running: '正在下载',
  installing: '正在校验和安装',
  interrupted: '已中断',
  failed: '下载或安装失败'
}

function formatJobStatus(job) {
  if (!job) return null
  if (job.status === 'running' && job.progress?.total > 0) {
    if (job.item.version === 'v2') {
      return `正在下载 ${job.progress.completed}/${job.progress.total}`
    }
    const percent = Math.min(100, Math.floor((job.progress.completed * 100) / job.progress.total))
    return `正在下载 ${percent}%`
  }
  return jobLabels[job.status]
}

function RenameOutfit({ model, onClose, onRename }) {
  const [name, setName] = useState(model.name)
  function change(event) {
    setName(event.target.value)
  }
  return (
    <OutfitDetailsForm
      title="重命名服装"
      submitLabel="保存名称"
      disabled={!name.trim()}
      onSubmit={() => onRename(name)}
      onClose={onClose}
    >
      <label className="outfit-field">
        服装名称
        <Input autoFocus value={name} onChange={change} maxLength={120} />
      </label>
    </OutfitDetailsForm>
  )
}

function DeleteOutfit({ client, target, model, onClose, onRemove }) {
  const [preview, setPreview] = useState(null)
  const [error, setError] = useState(null)
  useEffect(() => {
    let current = true
    async function check() {
      try {
        const result = await client.outfits.previewRemoval(target, model.id)
        if (current) {
          setPreview(result)
          setError(null)
        }
      } catch (failure) {
        if (current) setError(failure.message)
      }
    }
    void check()
    const timer = setInterval(check, 1400)
    return () => {
      current = false
      clearInterval(timer)
    }
  }, [client, model.id, target])
  function remove() {
    return onRemove(preview.defaultId)
  }
  return (
    <OutfitDetailsForm
      title={'删除「' + model.name + '」？'}
      submitLabel="删除服装"
      submitVariant="destructive"
      disabled={!preview || preview.busy || Boolean(error)}
      onSubmit={remove}
      onClose={onClose}
    >
      {preview?.count > 0 && (
        <p className="outfit-detail-status">
          {preview.count} 个对话将改用「{preview.defaultName}」。
        </p>
      )}
      {preview && !preview.busy && preview.count === 0 && (
        <p className="outfit-detail-status">删除后将从本地移除该服装模型文件。</p>
      )}
      {!preview && !error && (
        <p className="outfit-detail-status" data-loading="true" role="status">
          正在检查服装占用…
        </p>
      )}
      {preview?.busy && (
        <p className="outfit-detail-status" role="status">
          有对话正在使用这套服装，本轮结束后可删除。
        </p>
      )}
      {error && (
        <p className="outfit-error" role="alert">
          {error}
        </p>
      )}
    </OutfitDetailsForm>
  )
}

function OutfitForm({
  client,
  definition,
  form,
  menuContainer,
  currentChat,
  tab,
  version,
  jobs,
  onTry,
  onDeleted,
  onRefresh,
  onDownloadStarted,
  onVersionChange,
  onAffiliation,
  onShowDetail
}) {
  const [catalogResult, setCatalogResult] = useState(null)
  const [visible, setVisible] = useState(pageSize)
  const [searchQuery, setSearchQuery] = useState('')
  const [revision, setRevision] = useState(0)
  const [error, setError] = useState(null)
  const [notice, setNotice] = useState(null)
  const [busy, setBusy] = useState(false)
  const [target] = useState(() => ({ characterId: definition.id, formId: form.id }))
  useEffect(() => {
    if (tab !== 'download' || !definition.bangDreamId) return
    if (
      catalogResult?.revision === revision &&
      catalogResult.affiliation === definition.bangDreamId
    )
      return
    let current = true
    const affiliation = definition.bangDreamId
    void client.outfits.catalog(target, revision > 0).then(
      (result) => {
        if (current) setCatalogResult({ revision, affiliation, result })
      },
      (failure) => {
        if (current) setCatalogResult({ revision, affiliation, error: failure.message })
      }
    )
    return () => {
      current = false
    }
  }, [catalogResult, client, definition.bangDreamId, revision, tab, target])
  const installations = jobs
    .filter((job) => job.status === 'succeeded')
    .map((job) => job.id)
    .join(',')
  useEffect(() => {
    if (installations) void onRefresh()
  }, [installations, onRefresh])
  async function run(operation, message) {
    const activeTab = tab
    setBusy(true)
    setError(null)
    setNotice(null)
    try {
      const result = await operation()
      if (message) setNotice({ tab: activeTab, message })
      return result
    } catch (failure) {
      setError({ tab: activeTab, message: failure.message })
      return null
    } finally {
      setBusy(false)
    }
  }
  function changeVersion(event) {
    setVisible(pageSize)
    setSearchQuery('')
    onVersionChange(event.currentTarget.value)
  }
  function changeSearch(event) {
    setVisible(pageSize)
    setSearchQuery(event.target.value)
  }
  function refreshCatalog() {
    setRevision((value) => value + 1)
  }
  function more() {
    setVisible((value) => value + pageSize)
  }
  function importFolder() {
    void run(async () => {
      const response = await desktopHost().importOutfitDirectory(target)
      if (response?.problem) throw new Error(response.problem.message)
      if (response?.result) {
        await onRefresh()
        setNotice({
          tab: 'installed',
          message: response.result.alreadyInstalled ? '这套服装已安装。' : '模型已导入。'
        })
      }
    })
  }
  function installedAction(action, model) {
    if (action === 'try') void run(() => onTry(form.id, model))
    if (action === 'default')
      void run(async () => {
        await client.outfits.setDefault(target, model.id)
        await onRefresh()
      }, '已设为新对话的默认服装。')
    if (action === 'rename')
      onShowDetail('重命名服装', (onClose) => (
        <RenameOutfit model={model} onClose={onClose} onRename={(name) => rename(model.id, name)} />
      ))
    if (action === 'delete')
      onShowDetail('删除「' + model.name + '」？', (onClose) => (
        <DeleteOutfit
          client={client}
          target={target}
          model={model}
          onClose={onClose}
          onRemove={(defaultId) => remove(model.id, defaultId)}
        />
      ))
  }
  async function rename(modelId, name) {
    await client.outfits.rename(target, modelId, name)
    await onRefresh()
  }
  async function remove(modelId, defaultId) {
    await client.outfits.remove(target, modelId, defaultId)
    onDeleted(target, modelId)
    await onRefresh()
  }
  function download(item) {
    void run(async () => {
      await client.outfits.start(target, item.source, item.id)
      onDownloadStarted()
    })
  }
  function editAffiliation() {
    onAffiliation(definition)
  }

  const selectedId = currentChat?.formId === form.id ? currentChat.modelId : null
  const activeJobs = jobs.filter((job) => !['succeeded', 'cancelled'].includes(job.status))
  const catalog =
    catalogResult?.revision === revision && catalogResult.affiliation === definition.bangDreamId
      ? catalogResult
      : null
  const online = catalog?.result
  const v2Failed = Boolean(
    catalog?.error || online?.problems.some((problem) => problem.source === catalogSources.v2)
  )
  const v3Failed = Boolean(
    catalog?.error || online?.problems.some((problem) => problem.source === catalogSources.v3)
  )
  const catalogFailed = version === 'v2' ? v2Failed : v3Failed
  const v2Items = online?.items.filter((item) => item.version === 'v2') ?? []
  const v3Items = online?.items.filter((item) => item.version === 'v3') ?? []
  const versionItems = version === 'v2' ? v2Items : v3Items
  const otherVersion = version === 'v2' ? 'v3' : 'v2'
  const otherItems = otherVersion === 'v2' ? v2Items : v3Items
  const normalizedQuery = searchQuery.normalize('NFKC').trim().toLowerCase()
  const cards = normalizedQuery
    ? versionItems.filter((item) =>
        item.name.normalize('NFKC').toLowerCase().includes(normalizedQuery)
      )
    : versionItems
  function renderCard(item) {
    const installed = form.models.find(
      (model) =>
        model.source?.kind === item.source &&
        model.source.id === definition.bangDreamId + ':' + item.id
    )
    const job = activeJobs.find(
      (job) =>
        job.item.id === item.id &&
        job.item.source === item.source &&
        job.item.characterId === definition.bangDreamId
    )
    let status = null
    if (installed) status = '已安装'
    else if (job) status = formatJobStatus(job)
    else if (busy) status = '请稍候'
    const tryModel = currentChat && installed && installed.id !== selectedId ? installed : null
    return (
      <DownloadOutfit
        key={definition.bangDreamId + ':' + item.source + ':' + item.id}
        client={client}
        target={target}
        item={item}
        status={status}
        tryModel={tryModel}
        onDownload={download}
        onTry={installedAction}
      />
    )
  }

  const activeError = error?.tab === tab ? error.message : null
  const activeNotice = notice?.tab === tab ? notice.message : null
  const feedback = (
    <>
      {activeError && (
        <p className="outfit-error outfit-message" role="alert">
          {activeError}
        </p>
      )}
      {activeNotice && (
        <p className="outfit-message" role="status">
          {activeNotice}
        </p>
      )}
    </>
  )

  return (
    <>
      <TabPanel className="outfit-manager-body" value="installed">
        <div className="outfit-panel-content">
          {feedback}
          <div className="outfit-installed-toolbar">
            <span className="outfit-installed-count">
              {form.models.length ? `共 ${form.models.length} 套服装` : '尚未安装服装'}
            </span>
            <Button
              variant="ghost"
              size="sm"
              className="outfit-import-button"
              onClick={importFolder}
              disabled={busy}
            >
              <FolderOpen size={16} />
              导入模型
            </Button>
          </div>
          {form.models.length ? (
            <ul className="outfit-installed">
              {form.models.map((model) => (
                <InstalledOutfit
                  key={model.id}
                  client={client}
                  model={model}
                  isDefault={form.defaultModelId === model.id}
                  current={selectedId === model.id}
                  canTry={Boolean(currentChat)}
                  onAction={installedAction}
                  menuContainer={menuContainer}
                />
              ))}
            </ul>
          ) : (
            <div className="outfit-empty">
              <Shirt size={24} aria-hidden="true" />
              <p>这个形态还没有服装。</p>
            </div>
          )}
        </div>
      </TabPanel>
      <TabPanel className="outfit-manager-body" value="download">
        {definition.bangDreamId && (
          <div className="outfit-download-toolbar">
            <div className="outfit-versions" role="group" aria-label="模型版本">
              <Button
                size="sm"
                variant="ghost"
                value="v2"
                aria-pressed={version === 'v2'}
                onClick={changeVersion}
              >
                V2
                {online && !v2Failed && (
                  <span className="outfit-version-count" aria-hidden="true">
                    {v2Items.length}
                  </span>
                )}
              </Button>
              <Button
                size="sm"
                variant="ghost"
                value="v3"
                aria-pressed={version === 'v3'}
                onClick={changeVersion}
              >
                V3
                {online && !v3Failed && (
                  <span className="outfit-version-count" aria-hidden="true">
                    {v3Items.length}
                  </span>
                )}
              </Button>
            </div>
            <div className="outfit-download-actions">
              {versionItems.length > 12 && (
                <Input
                  className="outfit-search"
                  type="search"
                  aria-label="搜索服装"
                  placeholder="搜索服装名称…"
                  value={searchQuery}
                  onChange={changeSearch}
                />
              )}
              {!catalogFailed && (
                <Button variant="ghost" size="sm" disabled={!catalog} onClick={refreshCatalog}>
                  <RefreshCw size={14} />
                  刷新服装
                </Button>
              )}
            </div>
          </div>
        )}
        <div className="outfit-panel-content">
          {feedback}
          {!definition.bangDreamId && (
            <div className="outfit-empty">
              <p>
                {definition.bangDreamId === null
                  ? '这个角色可以通过模型文件夹导入服装。'
                  : '确认角色的 BanG Dream! 归属后可查看在线服装。'}
              </p>
              <Button variant="outline" size="sm" onClick={editAffiliation}>
                编辑角色归属
              </Button>
            </div>
          )}
          {definition.bangDreamId && !catalog && (
            <p className="outfit-empty" role="status">
              正在获取服装…
            </p>
          )}
          {catalogFailed && (
            <div className="outfit-empty" role="status">
              <p>{version.toUpperCase()} 服装加载失败</p>
              <Button variant="outline" size="sm" onClick={refreshCatalog}>
                重试
              </Button>
            </div>
          )}
          {online && !catalogFailed && !versionItems.length && otherItems.length > 0 && (
            <div className="outfit-empty">
              <p>
                暂无 {version.toUpperCase()} 服装，{otherVersion.toUpperCase()} 有{' '}
                {otherItems.length} 套可用服装。
              </p>
              <Button variant="outline" size="sm" value={otherVersion} onClick={changeVersion}>
                查看 {otherVersion.toUpperCase()} 服装
              </Button>
            </div>
          )}
          {online && !catalogFailed && !versionItems.length && otherItems.length === 0 && (
            <p className="outfit-empty">暂无在线服装。</p>
          )}
          {online && !catalogFailed && versionItems.length > 0 && !cards.length && (
            <p className="outfit-empty">没有匹配的服装。</p>
          )}
          {cards.length > 0 && (
            <ul className="outfit-grid">{cards.slice(0, visible).map(renderCard)}</ul>
          )}
          {cards.length > visible && (
            <div className="outfit-more-wrap">
              <Button className="outfit-more" variant="outline" onClick={more}>
                加载更多
              </Button>
            </div>
          )}
        </div>
      </TabPanel>
    </>
  )
}

/**
 * @param {object} props 起始角色形态、当前对话和全局下载任务。
 * @returns {import('react').ReactElement} 覆盖舞台的服装管理面板。
 */
export function OutfitManager({
  client,
  roles,
  initial,
  currentChat,
  jobs,
  onClose,
  onTry,
  onDeleted,
  onShowDownloads,
  onDownloadStarted,
  onShowDetail,
  onAffiliationSaved
}) {
  const [characterId, setCharacterId] = useState(initial.characterId)
  const [formId, setFormId] = useState(initial.formId)
  const [tab, setTab] = useState(initial.tab ?? 'installed')
  const [version, setVersion] = useState(savedVersion)
  const [snapshot, setSnapshot] = useState(null)
  const [error, setError] = useState(null)
  const [menuContainer, setMenuContainer] = useState(null)
  const request = useRef(0)
  const refreshRef = useRef(null)
  useEffect(() => {
    let current = true
    async function refresh() {
      const version = ++request.current
      try {
        const definition = await client.characters.get(characterId)
        if (current && version === request.current) setSnapshot({ characterId, definition })
      } catch (failure) {
        if (current && version === request.current) setError(failure.message)
      }
    }
    refreshRef.current = refresh
    void refresh()
    return () => {
      current = false
    }
  }, [characterId, client, roles])
  function characterChanged(id) {
    setError(null)
    setCharacterId(id)
    setFormId(null)
  }
  function changeTab(value) {
    setError(null)
    setTab(value)
  }
  function versionChanged(next) {
    setVersion(next)
    try {
      localStorage.setItem('sakiko.outfits.version', next)
    } catch {
      // 存储不可用时，本次打开仍保留选择。
    }
  }
  const refresh = useCallback(() => refreshRef.current?.(), [])
  function editAffiliation(role) {
    function saved() {
      void refresh()
      onAffiliationSaved()
    }
    function render(onClose) {
      return (
        <CharacterAffiliationForm client={client} role={role} onClose={onClose} onSaved={saved} />
      )
    }
    onShowDetail('编辑角色归属', render)
  }
  const definition = snapshot?.characterId === characterId ? snapshot.definition : null
  const form =
    definition?.forms.find((item) => item.id === formId) ??
    definition?.forms.find((item) => item.id === definition.defaultFormId)
  const activeChat = currentChat?.characterId === characterId ? currentChat : null
  const targetJobs = jobs.filter(
    (job) => job.target.characterId === characterId && job.target.formId === form?.id
  )
  const selectedRole = roles.find((item) => item.id === characterId)
  const pendingJobCount = pendingDownloads(jobs).length
  async function tryOutfit(selected, model) {
    await onTry(characterId, selected, model)
  }
  const bodyStatus = error ? (
    <p className="outfit-message outfit-error" role="alert">
      {error}
    </p>
  ) : (
    <p className="outfit-empty" role="status">
      {definition ? '这个角色没有可管理服装的形态。' : '正在读取角色服装…'}
    </p>
  )

  return (
    <Tabs
      className="outfit-manager-layout"
      value={tab}
      onValueChange={changeTab}
      activationMode="manual"
      ref={setMenuContainer}
    >
      <header className="outfit-page-heading">
        <div className="outfit-title-row">
          <h2 tabIndex={-1} data-dialog-focus>
            服装管理
          </h2>
          <Button
            variant="ghost"
            size="sm"
            aria-description={pendingJobCount ? pendingJobCount + ' 个待处理任务' : undefined}
            onClick={onShowDownloads}
          >
            <Download size={16} />
            下载任务
            {pendingJobCount > 0 && (
              <span className="outfit-download-count" aria-hidden="true">
                {pendingJobCount}
              </span>
            )}
          </Button>
          <Button variant="ghost" size="icon" aria-label="关闭服装管理" onClick={onClose}>
            <X size={20} />
          </Button>
        </div>
        <div className="outfit-nav-bar">
          <div className="outfit-target">
            {selectedRole && (
              <CharacterAvatar
                role={selectedRole}
                client={client}
                size={34}
                className="outfit-role-avatar"
              />
            )}
            <div className="outfit-target-character">
              <SelectField
                label="角色"
                value={characterId}
                onChange={characterChanged}
                options={roles.map((role) => ({
                  value: role.id,
                  label: role.displayName ?? role.name,
                  keywords: [role.id]
                }))}
              />
            </div>
            {definition && (
              <OutfitFormPicker
                value={form?.id ?? ''}
                onChange={setFormId}
                options={definition.forms.map((item) => ({
                  value: item.id,
                  label: characterFormName(definition, item),
                  keywords: [item.id]
                }))}
              />
            )}
          </div>
          <TabList className="outfit-tabs" aria-label="服装列表">
            <Tab value="installed">已安装</Tab>
            <Tab value="download">下载</Tab>
          </TabList>
        </div>
      </header>
      {definition && form ? (
        <>
          {error && bodyStatus}
          <OutfitForm
            key={characterId + ':' + form.id}
            client={client}
            definition={definition}
            form={form}
            menuContainer={menuContainer}
            currentChat={activeChat}
            tab={tab}
            version={version}
            jobs={targetJobs}
            onTry={tryOutfit}
            onDeleted={onDeleted}
            onRefresh={refresh}
            onDownloadStarted={onDownloadStarted}
            onVersionChange={versionChanged}
            onAffiliation={editAffiliation}
            onShowDetail={onShowDetail}
          />
        </>
      ) : (
        <>
          <TabPanel value="installed" className="outfit-body-status">
            {bodyStatus}
          </TabPanel>
          <TabPanel value="download" className="outfit-body-status">
            {bodyStatus}
          </TabPanel>
        </>
      )}
    </Tabs>
  )
}
