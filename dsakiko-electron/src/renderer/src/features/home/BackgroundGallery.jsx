import { useCallback, useEffect, useRef, useState } from 'react'
import { Check, ChevronRight, Download, Image as Picture, Search, X } from 'lucide-react'
import { Button } from '../../components/ui/button.jsx'
import { Tabs, TabList, Tab, TabPanel } from '../../components/ui/segmented-tabs.jsx'
import { desktopHost } from '../../host/desktop.js'
import { pendingDownloads } from './download-jobs.js'
import defaultBackgroundUrl from '../../assets/welcome/background.png'
import './background-gallery.css'

const extensions = ['png', 'jpg', 'jpeg', 'webp']
const previewDelayMs = 350
// 对话用 null 表示默认背景。仅在选择器补上内置条目，直接复用舞台底图。
const builtin = {
  key: 'builtin-default',
  name: '默认背景 · 内置',
  filename: 'background.png',
  asset: null,
  builtin: true
}

function useLibrary(client) {
  const [items, setItems] = useState(null)
  useEffect(() => client.observe('backgrounds', null, setItems), [client])
  return items
}

function usePicture(client, id, onlineKey) {
  const [result, setResult] = useState(null)
  const key = id ?? onlineKey
  useEffect(() => {
    if (!key) return
    let active = true
    let lease
    function release() {
      if (lease) void client.assets.releaseAccess(lease).catch(() => {})
      lease = null
    }
    async function load() {
      let url
      if (id) {
        const access = await client.assets.access({ id })
        lease = access.leaseId
        url = access.url
      } else url = await client.backgrounds.preview(onlineKey)
      if (active) setResult({ key, url })
      else release()
    }
    void load().catch(() => {
      if (active) setResult({ key, error: true })
    })
    return () => {
      active = false
      release()
    }
  }, [client, id, onlineKey, key])
  return result?.key === key ? result : null
}

/**
 * @param {{client: import('../../backend/client.js').BackendClient, asset?: import('../../../../shared/contracts/common.js').AssetRef|null, hidden?: boolean, story?: boolean, onStartupReady?: (id: string|null) => void}} props 舞台背景及普通舞台首张背景的解码通知。
 * @returns {import('react').ReactElement|null} 位于透明角色画布下方的背景层。
 */
export function StageBackdrop({ client, asset, hidden = false, story = false, onStartupReady }) {
  const assetId = asset?.id ?? null
  const picture = usePicture(client, assetId, null)
  const image = useRef(null)
  const [failedUrl, setFailedUrl] = useState(null)
  const failed = picture?.error || (picture?.url && failedUrl === picture.url)
  let url = picture?.url
  if (failed || (story && !asset)) url = defaultBackgroundUrl
  function imageFailed() {
    if (url && url !== defaultBackgroundUrl) setFailedUrl(url)
  }
  useEffect(() => {
    if (!onStartupReady || story || (assetId && !url)) return
    let current = true
    // 内置背景由 CSS 绘制；复用同一个 URL 的浏览器资源，等到像素可用再揭开 Logo。
    const source = image.current ?? new Image()
    if (!url) source.src = defaultBackgroundUrl
    function ready() {
      if (current) onStartupReady(assetId)
    }
    void source.decode().then(ready, () => {
      // 自定义图失败后由 onError 切到内置图；内置图失败时可直接展示舞台底色。
      if (!url || url === defaultBackgroundUrl) ready()
    })
    return () => {
      current = false
    }
  }, [assetId, url, story, onStartupReady])
  if (!url) return null
  if (story) return <StoryBackdrop key={url} url={url} hidden={hidden} onError={imageFailed} />
  return (
    <img
      ref={image}
      className="stage-backdrop"
      hidden={hidden}
      src={url}
      onError={imageFailed}
      alt=""
      draggable={false}
    />
  )
}

function StoryBackdrop({ url, hidden, onError }) {
  const box = useRef(null)
  const image = useRef(null)
  const [framed, setFramed] = useState(false)
  const measure = useCallback(() => {
    const { naturalWidth: width, naturalHeight: height } = image.current
    if (!width || !height || !box.current.clientWidth) return
    const x = box.current.clientWidth / width
    const y = box.current.clientHeight / height
    // 保留至少约七成的构图；过小的原图也避免强行放大铺满高分屏窗口。
    setFramed(Math.min(x, y) / Math.max(x, y) < 0.7 || Math.max(x, y) * window.devicePixelRatio > 2)
  }, [])
  useEffect(() => {
    const observer = new ResizeObserver(measure)
    observer.observe(box.current)
    return () => observer.disconnect()
  }, [measure])
  return (
    <div className="story-backdrop" ref={box} hidden={hidden} data-framed={framed}>
      {framed && <img className="story-fill" src={url} alt="" draggable={false} />}
      <img
        className="story-picture"
        ref={image}
        src={url}
        onLoad={measure}
        onError={onError}
        alt=""
        draggable={false}
      />
    </div>
  )
}

function GalleryPicture({ client, item }) {
  const picture = usePicture(client, item.asset?.id, item.asset ? null : item.key)
  if (picture?.url) return <img src={picture.url} alt="" loading="lazy" />
  return <Picture size={20} aria-hidden="true" />
}

function Thumbnail({ client, item }) {
  const host = useRef(null)
  const [visible, setVisible] = useState(false)
  useEffect(() => {
    const observer = new IntersectionObserver(
      (entries) => {
        setVisible(entries.some((entry) => entry.isIntersecting))
      },
      { rootMargin: '80px' }
    )
    observer.observe(host.current)
    return () => observer.disconnect()
  }, [])
  return (
    <span ref={host} className="background-thumb">
      {item.builtin && <img src={defaultBackgroundUrl} alt="" />}
      {visible && !item.builtin && <GalleryPicture client={client} item={item} />}
    </span>
  )
}

function matches(item, query) {
  const value = query.trim().toLocaleLowerCase()
  return [item.name, item.filename].some((text) => text.toLocaleLowerCase().includes(value))
}

function useBackgroundPreview(onPreview, enabled, current, query) {
  const timer = useRef(null)
  const cancel = useCallback(() => {
    if (timer.current === null) return
    clearTimeout(timer.current)
    timer.current = null
    onPreview(null)
  }, [onPreview])
  useEffect(() => cancel, [enabled, current?.id, query, cancel])
  function start(item) {
    cancel()
    if (enabled && item.asset?.id !== current?.id)
      timer.current = setTimeout(() => onPreview(item), previewDelayMs)
  }
  return { start, cancel }
}

function navigateGrid(event) {
  const grid = event.currentTarget.querySelector('[data-background-grid]')
  if (!grid) return
  const buttons = [...grid.querySelectorAll('[data-background-option]')]
  const index = buttons.indexOf(event.target)
  const columns = getComputedStyle(grid).gridTemplateColumns.split(' ').length
  const offsets = { ArrowDown: columns, ArrowUp: -columns, ArrowRight: 1, ArrowLeft: -1 }
  if (!(event.key in offsets) || (!event.target.matches('input[type="search"]') && index < 0))
    return
  if (index < 0 && event.key !== 'ArrowDown') return
  event.preventDefault()
  buttons[
    Math.max(0, Math.min(buttons.length - 1, index < 0 ? 0 : index + offsets[event.key]))
  ]?.focus()
}

/** @param {object} props 当前舞台、图库和浮窗操作。 @returns {import('react').ReactElement} 已下载背景的快捷面板。 */
export function BackgroundPicker({
  client,
  current,
  panelRef,
  suspended,
  onPreview,
  onApply,
  onManage,
  onClose
}) {
  const items = useLibrary(client)
  const [query, setQuery] = useState('')
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)
  const preview = useBackgroundPreview(onPreview, !suspended && !saving, current, query)
  const manageButton = useRef(null)
  const wasSuspended = useRef(false)
  useEffect(() => {
    if (!suspended && wasSuspended.current) manageButton.current?.focus({ preventScroll: true })
    wasSuspended.current = suspended
  }, [suspended])
  async function apply(asset) {
    preview.cancel()
    setSaving(true)
    setError('')
    try {
      await onApply(asset)
      onClose()
    } catch (failure) {
      setError(failure.message)
    } finally {
      setSaving(false)
    }
  }
  function manage() {
    preview.cancel()
    onManage()
  }
  function tile(item) {
    const active = current?.id === item.asset?.id
    return (
      <button
        key={item.asset?.id ?? item.key}
        type="button"
        className="background-tile"
        data-background-option
        aria-pressed={active}
        disabled={saving}
        onPointerEnter={() => preview.start(item)}
        onPointerLeave={preview.cancel}
        onFocus={() => preview.start(item)}
        onBlur={preview.cancel}
        onClick={() => void apply(item.asset)}
      >
        <Thumbnail client={client} item={item} />
        <span className="background-caption">
          {item.name}
          {active && <Check size={12} aria-hidden="true" />}
        </span>
      </button>
    )
  }
  const filtered = items?.filter((item) => matches(item, query))
  return (
    <section
      className="stage-wardrobe background-picker"
      ref={panelRef}
      role="dialog"
      aria-label="切换舞台背景"
      onKeyDown={navigateGrid}
    >
      <header className="stage-wardrobe-heading">
        <h2>背景</h2>
        <Button size="icon" variant="ghost" aria-label="收起背景" onClick={onClose}>
          <X size={17} />
        </Button>
      </header>
      <label className="background-search">
        <Search size={14} aria-hidden="true" />
        <input
          type="search"
          aria-label="搜索已下载背景"
          placeholder="搜索名称或文件名"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
      </label>
      <div className="stage-wardrobe-list">
        <div className="background-grid" data-background-grid>
          {tile(builtin)}
          {filtered?.map(tile)}
        </div>
        {!items && <p role="status">正在读取背景…</p>}
        {query && filtered?.length === 0 && !matches(builtin, query) && <p>没有匹配的背景</p>}
      </div>
      {error && <p role="alert">{error}</p>}
      <footer>
        <Button size="sm" variant="ghost" ref={manageButton} onClick={manage}>
          管理背景
          <ChevronRight size={14} />
        </Button>
      </footer>
    </section>
  )
}

function GalleryCard({ client, item, installed, online, job, onApply, preview }) {
  const [edit, setEdit] = useState(null)
  const [name, setName] = useState('')
  const [pending, setPending] = useState(false)
  const [error, setError] = useState('')
  async function perform(action) {
    preview?.cancel()
    setError('')
    setPending(true)
    try {
      await action()
      setEdit(null)
    } catch (failure) {
      setError(failure.message)
    } finally {
      setPending(false)
    }
  }
  function rename() {
    preview?.cancel()
    setName(item.name)
    setEdit('rename')
  }
  function saveName(event) {
    event.preventDefault()
    void perform(() => client.backgrounds.rename(item.asset.id, name))
  }
  function remove() {
    void perform(() => client.backgrounds.remove(item.asset.id))
  }
  function activate() {
    preview?.cancel()
    if (installed) void perform(() => onApply(installed.asset))
    else if (job && ['failed', 'paused', 'interrupted'].includes(job.status))
      void perform(() => client.backgrounds.control(job.id, 'resume'))
    else void perform(() => client.backgrounds.start(item.key))
  }
  let action = '下载'
  if (installed) action = '应用'
  else if (job?.status === 'running') action = '正在下载…'
  else if (job?.status === 'installing') action = '正在保存…'
  else if (job?.status === 'failed') action = '重试'
  else if (job && ['paused', 'interrupted'].includes(job.status)) action = '继续'
  return (
    <article
      className="background-card"
      onPointerEnter={preview && (() => preview.start(item))}
      onPointerLeave={preview?.cancel}
    >
      <Thumbnail client={client} item={installed ?? item} />
      <div className="background-card-info">
        <strong title={item.filename}>{installed?.name ?? item.name}</strong>
        {edit === 'rename' && (
          <form onSubmit={saveName}>
            <input
              aria-label="背景名称"
              value={name}
              maxLength={160}
              autoFocus
              onChange={(event) => setName(event.target.value)}
            />
            <Button size="sm" disabled={pending || !name.trim()}>
              保存
            </Button>
            <Button type="button" size="sm" variant="ghost" onClick={() => setEdit(null)}>
              取消
            </Button>
          </form>
        )}
        {edit === 'remove' && (
          <>
            <p>从图库移除？已使用它的对话会保留背景。</p>
            <Button size="sm" disabled={pending} onClick={remove}>
              确认移除
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setEdit(null)}>
              取消
            </Button>
          </>
        )}
        {!edit && (
          <div className="background-card-actions">
            <Button
              size="sm"
              variant="outline"
              data-background-option
              disabled={
                pending ||
                (installed && !onApply) ||
                (!installed && ['running', 'installing'].includes(job?.status))
              }
              onClick={activate}
              onFocus={preview && (() => preview.start(item))}
              onBlur={preview?.cancel}
            >
              {action}
            </Button>
            {!online && !item.builtin && (
              <>
                <Button size="sm" variant="ghost" onClick={rename}>
                  改名
                </Button>
                <Button size="sm" variant="ghost" onClick={() => setEdit('remove')}>
                  移除
                </Button>
              </>
            )}
          </div>
        )}
        {(error || (!installed && job?.problem?.message)) && (
          <p role="alert">{error || job.problem.message}</p>
        )}
      </div>
    </article>
  )
}

function BackgroundManager({ client, initial, jobs, onApply, onPreview, current, active }) {
  const [tab, setTab] = useState(initial ?? 'online')
  const [queries, setQueries] = useState({ online: '', installed: '' })
  const [catalog, setCatalog] = useState(null)
  const [revision, setRevision] = useState(0)
  const [catalogError, setCatalogError] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const local = useLibrary(client)
  const preview = useBackgroundPreview(
    onPreview,
    Boolean(onApply) && active && tab === 'installed',
    current,
    queries.installed
  )
  const wanted = active && tab === 'online'
  useEffect(() => {
    if (!wanted) return
    let current = true
    void client.backgrounds.catalog(revision > 0).then(
      (items) => {
        if (current) {
          setCatalog(items)
          setCatalogError('')
        }
      },
      (failure) => {
        if (current) setCatalogError(failure.message)
      }
    )
    return () => {
      current = false
    }
  }, [client, wanted, revision])
  async function importLocal() {
    setBusy(true)
    setError('')
    let draft
    try {
      draft = await client.assets.createDraft()
      const files = await desktopHost().importFiles({
        purpose: 'chat-image',
        draftId: draft.draftId,
        extensions
      })
      if (files.length) await client.backgrounds.importImages(files.map((file) => file.asset))
    } catch (failure) {
      setError(failure.message)
    } finally {
      if (draft) await client.assets.discardDraft(draft.draftId).catch(() => {})
      setBusy(false)
    }
  }
  const installed = new Map(local?.map((item) => [item.key, item]))
  const tasks = new Map(
    jobs
      .filter((job) => !['succeeded', 'cancelled'].includes(job.status))
      .map((job) => [job.source.key, job])
  )
  function renderPane(value) {
    const online = value === 'online'
    const items = online ? catalog : local
    const query = queries[value]
    const filtered = items?.filter(
      (item) =>
        matches(item, query) ||
        (online && installed.has(item.key) && matches(installed.get(item.key), query))
    )
    function card(item) {
      return (
        <GalleryCard
          key={item.asset?.id ?? item.key}
          client={client}
          online={online}
          item={item}
          installed={online ? installed.get(item.key) : item}
          job={tasks.get(item.key)}
          onApply={onApply}
          preview={online ? null : preview}
        />
      )
    }
    return (
      <TabPanel
        key={value}
        value={value}
        forceMount
        hidden={value !== tab}
        className="background-gallery-pane"
        onKeyDown={navigateGrid}
      >
        <div className="background-search-row">
          <label className="background-search">
            <Search size={14} aria-hidden="true" />
            <input
              type="search"
              aria-label={online ? '搜索在线背景' : '搜索本地背景'}
              placeholder="搜索名称或文件名"
              value={query}
              onChange={(event) => setQueries({ ...queries, [value]: event.target.value })}
            />
          </label>
          {!online && (
            <Button variant="outline" size="sm" disabled={busy} onClick={importLocal}>
              导入图片
            </Button>
          )}
        </div>
        <div className="background-gallery-scroll">
          {online && catalogError && (
            <p role="alert">
              {catalogError}{' '}
              <Button size="sm" onClick={() => setRevision((n) => n + 1)}>
                重试
              </Button>
            </p>
          )}
          {!online && error && <p role="alert">{error}</p>}
          {!items && !(online && catalogError) && <p role="status">正在读取背景…</p>}
          {filtered?.length === 0 && (online || (query && !matches(builtin, query))) && (
            <p>{query ? '没有匹配的背景' : '暂无背景图片'}</p>
          )}
          <div className="background-grid" data-background-grid>
            {!online && card(builtin)}
            {filtered?.map(card)}
          </div>
        </div>
      </TabPanel>
    )
  }
  return (
    <Tabs value={tab} onValueChange={setTab} className="background-gallery">
      <TabList aria-label="背景列表">
        <Tab value="installed">已下载</Tab>
        <Tab value="online">在线图库</Tab>
      </TabList>
      {['online', 'installed'].map(renderPane)}
    </Tabs>
  )
}

/** @param {object} props 服装和背景的共用窗口、入口和任务状态。 @returns {import('react').ReactElement} 舞台资源管理窗口。 */
export function StageResources({
  initial,
  client,
  jobs,
  outfitJobs,
  onClose,
  onDownloads,
  onApply,
  current,
  onPreview,
  active,
  children
}) {
  const [section, setSection] = useState(initial.section ?? 'outfits')
  const [visited, setVisited] = useState(() => new Set([section]))
  const count = pendingDownloads(section === 'outfits' ? outfitJobs : jobs).length
  function select(value) {
    setSection(value)
    setVisited((old) => new Set([...old, value]))
  }
  return (
    <Tabs value={section} onValueChange={select} className="stage-resources">
      <header className="stage-resource-header">
        <h2 tabIndex={-1} data-dialog-focus>
          舞台资源
        </h2>
        <TabList aria-label="舞台资源类型">
          <Tab value="outfits">服装</Tab>
          <Tab value="backgrounds">背景</Tab>
        </TabList>
        <div className="stage-resource-actions">
          <Button
            variant="ghost"
            size="sm"
            onClick={() => onDownloads(section)}
            aria-description={count ? count + ' 个待处理任务' : undefined}
          >
            <Download size={16} />
            下载任务
            {count > 0 && (
              <span aria-hidden="true" className="outfit-download-count">
                {count}
              </span>
            )}
          </Button>
          <Button variant="ghost" size="icon" aria-label="关闭舞台资源" onClick={onClose}>
            <X size={20} />
          </Button>
        </div>
      </header>
      <TabPanel
        className="stage-resource-pane"
        value="outfits"
        hidden={section !== 'outfits'}
        forceMount
      >
        {visited.has('outfits') && children}
      </TabPanel>
      <TabPanel
        className="stage-resource-pane"
        value="backgrounds"
        hidden={section !== 'backgrounds'}
        forceMount
      >
        {visited.has('backgrounds') && (
          <BackgroundManager
            client={client}
            active={active && section === 'backgrounds'}
            jobs={jobs}
            onApply={onApply}
            onPreview={onPreview}
            current={current}
            initial={initial.backgroundTab}
          />
        )}
      </TabPanel>
    </Tabs>
  )
}
