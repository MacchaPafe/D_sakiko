import { useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { ChevronLeft, ChevronRight, FolderOpen, RefreshCw, Trash2 } from 'lucide-react'
import { Button } from '../../components/ui/button.jsx'
import { Checkbox } from '../../components/ui/checkbox.jsx'
import { Tabs, TabList, Tab, TabPanel } from '../../components/ui/segmented-tabs.jsx'
import { desktopHost } from '../../host/desktop.js'
import { CharacterAvatar } from '../home/CharacterAvatar.jsx'
import { formatBytes } from '../setup/presentation.js'
import './storage.css'

/**
 * @typedef {object} StorageVolume
 * @property {number} dev 磁盘卷设备标识。
 * @property {number} totalBytes 磁盘总容量（字节）。
 * @property {number} availableBytes 磁盘剩余可用容量（字节）。
 */

/**
 * @typedef {object} CharacterStorageSummary
 * @property {number} totalBytes 角色关联资源总占用。
 * @property {number} live2dBytes Live2D 模型包占用。
 * @property {number} voiceBytes 角色语音模型与参考音频占用。
 * @property {number} otherBytes 头像等其他资源占用。
 * @property {number} formCount 人格数量。
 * @property {number} outfitCount 已安装服装数量。
 * @property {boolean} hasVoice 是否已配置语音。
 * @property {number} chatCount 参与的对话数量。
 */

const VOICE_NAMES = { gsv: 'GPT-SoVITS 语音合成', asr: '语音识别' }

/** 不可单独删除的系统/核心分组说明。 */
const READONLY_ENTRY_HINTS = {
  appDataRoot: {
    设置与索引: '核心配置',
    任务与远端文件记录: '核心数据'
  },
  cacheRoot: {
    'GPU 缓存': '自动管理',
    '其他 Chromium 会话文件': '自动管理'
  },
  applicationBundle: {
    程序本体: '卸载应用时移除'
  }
}

/** 可直接映射到缓存单项清理 ID 的分组。 */
const CACHE_CLEANUP_BY_ENTRY = {
  'HTTP 缓存': 'http',
  代码缓存: 'code',
  模型目录缓存: 'indexes',
  'Live2D 动作索引': 'indexes',
  内置角色预览: 'previews',
  在线图库缩略图: 'background-previews',
  语音临时文件: 'speech',
  语音日志: 'logs'
}

/** 需要跳转到对应管理界面的数据分组。 */
const MANAGE_TARGET_BY_ENTRY = {
  appDataRoot: {
    图片与附件: { kind: 'data', tab: 'chats', label: '管理聊天' },
    聊天存档: { kind: 'data', tab: 'chats', label: '管理聊天' },
    角色与头像: { kind: 'data', tab: 'characters', label: '管理角色' }
  },
  resourceLibraryRoot: {
    角色语音模型: { kind: 'data', tab: 'characters', label: '管理角色' },
    角色参考音频与其他音频: { kind: 'data', tab: 'characters', label: '管理角色' },
    'Live2D 模型包': { kind: 'data', tab: 'characters', label: '管理角色' },
    历史回复音频及聊天录音: { kind: 'data', tab: 'chats', label: '管理聊天' },
    待回收资源: { kind: 'clean-assets', label: '立即清理' }
  },
  cacheRoot: {
    资源安装下载: { kind: 'downloads', label: '前往下载任务' },
    服装下载: { kind: 'downloads', label: '前往下载任务' }
  }
}

function errorMessage(error) {
  return error.message.replace(/^Error invoking remote method '[^']+': Error: /, '')
}

function displayChatName(chat) {
  return chat.title || '未命名聊天'
}

function formatSharePercent(part, total) {
  if (!total || part <= 0) return '0%'
  const ratio = (part / total) * 100
  if (ratio < 0.1) return '< 0.1%'
  return `${ratio.toFixed(1)}%`
}

/**
 * 汇总主磁盘卷的三段式空间占比（本应用 / 同卷其他已用 / 剩余可用）。
 * @param {object[]} roots 各存储根目录统计结果。
 * @returns {{appBytes: number, otherUsedBytes: number, availableBytes: number, totalBytes: number, multiVolume: boolean} | null} 主卷摘要；无法读取磁盘信息时返回 null。
 */
function summarizePrimaryVolume(roots) {
  const volumes = new Map()
  for (const root of roots) {
    const volume = root.volume
    if (!volume || !volume.totalBytes) continue
    const key = String(volume.dev)
    const current = volumes.get(key) ?? {
      dev: volume.dev,
      totalBytes: volume.totalBytes,
      availableBytes: volume.availableBytes,
      appBytes: 0,
      hasLibrary: false
    }
    current.appBytes += root.bytes ?? 0
    if (root.id === 'resourceLibraryRoot') current.hasLibrary = true
    volumes.set(key, current)
  }
  if (!volumes.size) return null
  const ordered = [...volumes.values()].sort((a, b) => {
    if (a.hasLibrary !== b.hasLibrary) return a.hasLibrary ? -1 : 1
    return b.appBytes - a.appBytes
  })
  const primary = ordered[0]
  const clampedApp = Math.min(primary.appBytes, primary.totalBytes)
  const clampedAvailable = Math.min(primary.availableBytes, primary.totalBytes)
  const otherUsedBytes = Math.max(0, primary.totalBytes - clampedAvailable - clampedApp)
  return {
    appBytes: clampedApp,
    otherUsedBytes,
    availableBytes: clampedAvailable,
    totalBytes: primary.totalBytes,
    multiVolume: volumes.size > 1
  }
}

function useStorageRefresh(load) {
  const [state, setState] = useState({ loading: true, value: null, problem: null })
  const request = useRef(0)

  useEffect(() => {
    const id = ++request.current
    void load().then(
      (value) => {
        if (request.current === id) setState({ loading: false, value, problem: null })
      },
      (error) => {
        if (request.current === id)
          setState({ loading: false, value: null, problem: errorMessage(error) })
      }
    )
    return () => {
      request.current += 1
    }
  }, [load])

  const refresh = useCallback(async () => {
    const id = ++request.current
    setState((current) => ({ ...current, loading: true, problem: null }))
    try {
      const value = await load()
      if (request.current === id) setState({ loading: false, value, problem: null })
    } catch (error) {
      if (request.current === id)
        setState((current) => ({ ...current, loading: false, problem: errorMessage(error) }))
    }
  }, [load])

  return { ...state, refresh }
}

/**
 * 语音组件卸载/安装状态管理，供资源库卡片就近渲染操作按钮与内联确认块。
 */
function useVoiceRuntimeActions({ client, setup, onChanged }) {
  const [plans, setPlans] = useState(null)
  const [confirmation, setConfirmation] = useState(null)
  const [status, setStatus] = useState(null)
  const [busy, setBusy] = useState(false)
  const request = useRef(0)

  const fetchPlans = useCallback(async () => {
    const pairs = await Promise.all(
      Object.keys(VOICE_NAMES).map(async (kind) => [
        kind,
        await client.setup.previewRuntimeRemoval(kind)
      ])
    )
    return Object.fromEntries(pairs)
  }, [client])

  const reloadPlans = useCallback(async () => {
    const id = ++request.current
    try {
      const next = await fetchPlans()
      if (request.current === id) setPlans(next)
    } catch (error) {
      if (request.current === id) setStatus(errorMessage(error))
    }
  }, [fetchPlans])

  useEffect(() => {
    const id = ++request.current
    void fetchPlans().then(
      (next) => {
        if (request.current === id) setPlans(next)
      },
      (error) => {
        if (request.current === id) setStatus(errorMessage(error))
      }
    )
    return () => {
      request.current += 1
    }
  }, [fetchPlans])

  async function prepare(kind) {
    setBusy(true)
    setStatus(null)
    try {
      const plan = await client.setup.previewRuntimeRemoval(kind)
      setPlans((current) => ({ ...current, [kind]: plan }))
      if ((plan.installed || plan.pending) && !plan.taskId) setConfirmation(kind)
    } catch (error) {
      setStatus(errorMessage(error))
    } finally {
      setBusy(false)
    }
  }

  async function uninstall(kind) {
    setBusy(true)
    setStatus(null)
    try {
      const result = await client.setup.uninstallRuntime(kind)
      setConfirmation(null)
      const plan = await client.setup.previewRuntimeRemoval(kind)
      setPlans((current) => ({ ...current, [kind]: plan }))
      setStatus(
        result.pending || result.collection?.failed?.length || result.collection?.aborted
          ? '组件已卸载，部分文件待回收。可以在这里重试或清理未使用资源。'
          : VOICE_NAMES[kind] + '已卸载。'
      )
      await Promise.all([
        setup.refreshFeatures(),
        setup.refreshManagedCatalog({ localChanged: true }),
        onChanged()
      ])
    } catch (error) {
      setStatus(errorMessage(error))
    } finally {
      setBusy(false)
    }
  }

  return {
    plans,
    confirmation,
    status,
    busy,
    prepare,
    uninstall,
    cancelConfirmation: () => setConfirmation(null),
    reloadPlans
  }
}

function VoiceRuntimeActionButton({ kind, plan, busy, locked, onDownloads, onPrepare, onInstall }) {
  if (!plan) return null
  const name = VOICE_NAMES[kind]
  if (plan.taskId) {
    return (
      <Button variant="outline" size="xs" onClick={onDownloads}>
        前往下载任务
      </Button>
    )
  }
  if (plan.installed || plan.pending) {
    return (
      <Button
        variant="outline"
        size="xs"
        aria-label={plan.installed ? '卸载' + name : '重试回收' + name}
        disabled={busy || locked}
        onClick={() => void onPrepare(kind)}
      >
        {plan.installed ? '卸载' : '重试回收'}
      </Button>
    )
  }
  return (
    <Button
      variant="outline"
      size="xs"
      aria-label={'安装' + name}
      disabled={busy || locked}
      onClick={() => onInstall(kind)}
    >
      安装
    </Button>
  )
}

function VoiceRuntimeConfirmation({ kind, plan, busy, onCancel, onUninstall }) {
  if (!plan) return null
  return (
    <div className="storage-voice-confirm">
      <p>
        {kind === 'gsv'
          ? '卸载后无法为角色合成语音。每位角色自身的语音模型不会被删除。'
          : '卸载后无法使用语音识别。'}
      </p>
      {plan.sharedBytes > 0 && (
        <p className="storage-shared-note">
          另有 {formatBytes(plan.sharedBytes)}{' '}
          基础模型与其他目录硬链接共享，卸载不会立即释放这部分磁盘空间。
        </p>
      )}
      <div className="storage-confirm">
        <strong>预计释放 {formatBytes(plan.runtimeBytes + plan.modelBytes)}</strong>
        <Button variant="ghost" size="sm" disabled={busy} onClick={onCancel}>
          取消
        </Button>
        <Button
          variant="destructive"
          size="sm"
          disabled={busy}
          onClick={() => void onUninstall(kind)}
        >
          {busy ? '正在处理…' : plan.installed ? '确认卸载' : '确认回收'}
        </Button>
      </div>
    </div>
  )
}

function DiskUsageMeter({ roots, appTotal }) {
  const volume = useMemo(() => summarizePrimaryVolume(roots), [roots])
  if (!volume) {
    return (
      <div className="storage-meter-block">
        <div className="storage-meter" aria-label="应用存储占用比例">
          {roots
            .filter(({ bytes }) => bytes > 0)
            .map(({ id, bytes, name }) => (
              <span
                key={id}
                data-root={id}
                style={{ flexGrow: appTotal ? bytes / appTotal : 0 }}
                title={name + ' ' + formatBytes(bytes)}
              />
            ))}
        </div>
      </div>
    )
  }

  const appRatio = volume.appBytes / volume.totalBytes
  const otherRatio = volume.otherUsedBytes / volume.totalBytes
  const freeRatio = volume.availableBytes / volume.totalBytes

  return (
    <div className="storage-meter-block">
      <div className="storage-meter storage-disk-meter" aria-label="磁盘空间占用比例">
        {volume.appBytes > 0 && (
          <span
            className="storage-meter-app"
            style={{ flexGrow: appRatio }}
            title={`数字小祥占用 ${formatBytes(volume.appBytes)}（${formatSharePercent(volume.appBytes, volume.totalBytes)}）`}
          />
        )}
        {volume.otherUsedBytes > 0 && (
          <span
            className="storage-meter-other"
            style={{ flexGrow: otherRatio }}
            title={`磁盘其他已用 ${formatBytes(volume.otherUsedBytes)}`}
          />
        )}
        {volume.availableBytes > 0 && (
          <span
            className="storage-meter-free"
            style={{ flexGrow: freeRatio }}
            title={`磁盘剩余可用 ${formatBytes(volume.availableBytes)}`}
          />
        )}
      </div>
      <div className="storage-disk-legend">
        <span>
          <i className="storage-legend-dot storage-dot-app" aria-hidden="true" />
          数字小祥 {formatBytes(volume.appBytes)}（
          {formatSharePercent(volume.appBytes, volume.totalBytes)}）
        </span>
        <span>
          <i className="storage-legend-dot storage-dot-other" aria-hidden="true" />
          其他已用 {formatBytes(volume.otherUsedBytes)}
        </span>
        <span className="storage-disk-total">
          <i className="storage-legend-dot storage-dot-free" aria-hidden="true" />
          可用 {formatBytes(volume.availableBytes)} / 共 {formatBytes(volume.totalBytes)}
        </span>
      </div>
      {volume.multiVolume && (
        <p className="storage-disk-note">部分目录位于其他磁盘，上方进度条显示主目录所在磁盘。</p>
      )}
    </div>
  )
}

function ChatStorage({ client, flow, onChanged }) {
  const list = useStorageRefresh(client.conversations.previewChatCleanup)
  const [query, setQuery] = useState('')
  const [selected, setSelected] = useState([])
  const [confirmation, setConfirmation] = useState(null)
  const [busy, setBusy] = useState(false)
  const [results, setResults] = useState(null)
  const summaryId = useId()
  const cancel = useRef(null)

  useEffect(() => {
    if (confirmation) cancel.current?.focus()
  }, [confirmation])

  const chats = list.value?.chats ?? []
  const shown = chats
    .filter((chat) =>
      (
        displayChatName(chat) +
        ' ' +
        chat.characterIds.map((id) => chat.characterNames?.[id] ?? id).join(' ')
      )
        .toLocaleLowerCase()
        .includes(query.trim().toLocaleLowerCase())
    )
    .sort((a, b) => b.estimatedBytes - a.estimatedBytes)

  const candidates = shown.filter((chat) => !chat.busy).map(({ id }) => id)
  const selectedIds = new Set(selected)
  const allCandidatesSelected =
    candidates.length > 0 && candidates.every((id) => selectedIds.has(id))
  const reviewedIds = confirmation && new Set(confirmation.entries.map(({ id }) => id))
  const locked = busy || Boolean(confirmation)

  function handleToggleSelect(id, checked) {
    setSelected((current) =>
      checked ? [...new Set([...current, id])] : current.filter((item) => item !== id)
    )
  }

  function handleToggleSelectAll() {
    if (allCandidatesSelected) {
      const candidateSet = new Set(candidates)
      setSelected((current) => current.filter((id) => !candidateSet.has(id)))
    } else {
      setSelected((current) => [...new Set([...current, ...candidates])])
    }
  }

  async function prepareDelete(ids, inlineChatId = null) {
    setBusy(true)
    setResults(null)
    try {
      const version = new Map(chats.map(({ id, revision }) => [id, revision]))
      const preview = await client.conversations.previewChatCleanup(ids)
      const valid = preview.chats.filter(
        ({ id, busy: isBusy, revision }) => !isBusy && version.get(id) === revision
      )
      if (valid.length !== ids.length) {
        setResults({ notes: ['部分聊天正在使用或已更新，请核对刷新后的列表。'] })
        await list.refresh()
      }
      if (valid.length) {
        // 只确认预览时列出的版本。无效项会在新的估算中排除。
        const checked =
          valid.length === ids.length
            ? preview
            : await client.conversations.previewChatCleanup(valid.map(({ id }) => id))
        const byId = new Map(valid.map(({ id, revision }) => [id, revision]))
        const entries = checked.chats.filter(
          ({ id, revision, busy: isBusy }) => !isBusy && byId.get(id) === revision
        )
        if (entries.length !== valid.length) {
          setResults({ notes: ['所选聊天再次发生变化，请重新勾选。'] })
          await list.refresh()
          return
        }
        setConfirmation({ entries, bytes: checked.estimatedBytes, inlineChatId })
      }
    } catch (error) {
      setResults({ notes: [errorMessage(error)] })
    } finally {
      setBusy(false)
    }
  }

  async function handleConfirmRemove() {
    setBusy(true)
    try {
      const result = await client.conversations.deleteChats(
        confirmation.entries.map(({ id, revision }) => ({ id, revision }))
      )
      const titles = new Map(confirmation.entries.map((chat) => [chat.id, displayChatName(chat)]))
      const notes = result.skipped.map(
        ({ id, problem }) => titles.get(id) + ' · ' + problem.message
      )
      if (result.collection?.failed?.length || result.collection?.aborted)
        notes.push('部分文件未能回收，可在缓存可清理项中重试。')
      setResults({ deleted: result.deleted.length, notes })
      setConfirmation(null)
      setSelected((current) => current.filter((id) => !result.deleted.includes(id)))
      await Promise.all([flow.applyDeletedChats(result.deleted), list.refresh(), onChanged()])
    } catch (error) {
      setResults({ notes: [errorMessage(error)] })
    } finally {
      setBusy(false)
    }
  }

  function formatChatMeta(chat) {
    const names = chat.characterIds.map((id) => chat.characterNames?.[id] ?? id).join(' · ')
    if (chat.turnCount > 0) return `${names} · ${chat.turnCount} 轮对话`
    return names
  }

  return (
    <div className="storage-chat-list" data-dialog-busy={busy || undefined}>
      <p className="storage-sub-note">聊天大小包含附件以及角色的音频。</p>
      <div className="storage-chat-actions">
        <input
          className="field-input"
          type="search"
          aria-label="搜索聊天"
          placeholder="按标题或角色搜索"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          disabled={locked}
        />
        <Button
          size="sm"
          variant="outline"
          disabled={locked || !candidates.length}
          onClick={handleToggleSelectAll}
        >
          {allCandidatesSelected ? '取消全选' : '全选当前'}
        </Button>
        <Button
          size="sm"
          variant="destructive"
          disabled={locked || !selected.length}
          onClick={() => void prepareDelete(selected, null)}
        >
          删除{selected.length ? `（${selected.length}）` : ''}
        </Button>
        <Button
          size="icon-sm"
          variant="ghost"
          aria-label="刷新聊天占用"
          title="刷新聊天占用"
          disabled={locked || list.loading}
          onClick={list.refresh}
        >
          <RefreshCw className={list.loading ? 'storage-spin' : undefined} aria-hidden="true" />
        </Button>
      </div>
      {list.loading && !list.value && <p role="status">正在计算聊天占用…</p>}
      {list.problem && (
        <div className="storage-inline">
          <p role="alert">{list.problem}</p>
          <Button size="sm" variant="ghost" onClick={list.refresh}>
            重试
          </Button>
        </div>
      )}
      {results && (
        <div role="status" className="storage-result">
          {results.deleted > 0 && <p>已删除 {results.deleted} 个聊天。</p>}
          {results.notes.map((note, index) => (
            <p key={index}>{note}</p>
          ))}
        </div>
      )}
      <div className="storage-grid">
        {shown.map((chat) => {
          const isInlineTarget = confirmation?.inlineChatId === chat.id
          return (
            <div className="storage-chat-item" key={chat.id}>
              <article className="storage-row">
                <Checkbox
                  aria-label={'选择 ' + displayChatName(chat)}
                  checked={(reviewedIds ?? selectedIds).has(chat.id)}
                  disabled={locked || chat.busy}
                  onCheckedChange={(checked) => handleToggleSelect(chat.id, checked === true)}
                />
                <div className="storage-row-text">
                  <h4>{displayChatName(chat)}</h4>
                  <p>{formatChatMeta(chat)}</p>
                </div>
                <div className="storage-size-column">
                  <span className="storage-number">
                    {chat.busy ? '暂不能删除' : formatBytes(chat.estimatedBytes)}
                  </span>
                  {!chat.busy && chat.audioBytes > 0 && (
                    <small className="storage-size-breakdown">
                      含语音 {formatBytes(chat.audioBytes)}
                    </small>
                  )}
                </div>
                <Button
                  size="icon-sm"
                  variant="ghost"
                  className="storage-delete-icon"
                  aria-label={'删除 ' + displayChatName(chat)}
                  title="删除聊天"
                  disabled={locked || chat.busy}
                  onClick={() => void prepareDelete([chat.id], chat.id)}
                >
                  <Trash2 aria-hidden="true" />
                </Button>
              </article>
              {isInlineTarget && (
                <div
                  className="storage-confirm storage-inline-confirm"
                  role="group"
                  aria-labelledby={summaryId}
                >
                  <strong id={summaryId}>
                    删除后聊天将永远消失。预计释放 {formatBytes(confirmation.bytes)}
                  </strong>
                  <Button
                    size="sm"
                    variant="ghost"
                    ref={cancel}
                    disabled={busy}
                    onClick={() => setConfirmation(null)}
                  >
                    取消
                  </Button>
                  <Button
                    size="sm"
                    variant="destructive"
                    disabled={busy}
                    onClick={handleConfirmRemove}
                  >
                    {busy ? '正在删除…' : '确认删除'}
                  </Button>
                </div>
              )}
            </div>
          )
        })}
        {list.value && !shown.length && <p>{query ? '没有匹配的聊天。' : '没有已保存的聊天。'}</p>}
      </div>
      {confirmation && !confirmation.inlineChatId && (
        <div
          className="storage-confirm storage-sticky-confirm"
          role="group"
          aria-labelledby={summaryId}
        >
          <strong id={summaryId}>
            删除后聊天将永远消失。预计释放 {formatBytes(confirmation.bytes)}
          </strong>
          <Button
            size="sm"
            variant="ghost"
            ref={cancel}
            disabled={busy}
            onClick={() => setConfirmation(null)}
          >
            取消
          </Button>
          <Button size="sm" variant="destructive" disabled={busy} onClick={handleConfirmRemove}>
            {busy ? '正在删除…' : `确认删除 ${confirmation.entries.length} 个聊天`}
          </Button>
        </div>
      )}
    </div>
  )
}

function CharacterStorage({ client, characters, onManageCharacter }) {
  const usage = useStorageRefresh(desktopHost().getCharacterStorageUsage)

  const sortedCharacters = useMemo(() => {
    const usageMap = usage.value ?? {}
    return [...characters].sort((a, b) => {
      const sizeA = usageMap[a.id]?.totalBytes ?? 0
      const sizeB = usageMap[b.id]?.totalBytes ?? 0
      return sizeB - sizeA
    })
  }, [characters, usage.value])
  const usageMap = usage.value ?? {}

  function buildUsageTags(info) {
    if (!info || info.totalBytes <= 0) return []
    const tags = []
    if (info.live2dBytes > 0)
      tags.push({ key: 'live2d', label: `Live2D ${formatBytes(info.live2dBytes)}` })
    if (info.voiceBytes > 0)
      tags.push({ key: 'voice', label: `语音 ${formatBytes(info.voiceBytes)}` })
    if (info.otherBytes > 0 && tags.length === 0) {
      tags.push({ key: 'other', label: `头像等 ${formatBytes(info.otherBytes)}` })
    }
    return tags
  }

  return (
    <div className="storage-grid storage-characters">
      {usage.problem && (
        <div className="storage-inline">
          <p role="alert">{usage.problem}</p>
          <Button size="sm" variant="ghost" onClick={usage.refresh}>
            重试
          </Button>
        </div>
      )}
      {sortedCharacters.map((role) => {
        const info = usageMap[role.id]
        const tags = buildUsageTags(info)
        return (
          <article className="storage-row storage-character-card" key={role.id}>
            <CharacterAvatar role={role} client={client} size={38} />
            <div className="storage-row-text">
              <h4>{role.name ?? role.displayName}</h4>
              {tags.length > 0 && (
                <div className="storage-usage-tags">
                  {tags.map((tag) => (
                    <span className="storage-usage-tag" key={tag.key}>
                      {tag.label}
                    </span>
                  ))}
                </div>
              )}
            </div>
            <span className="storage-number">
              {info ? formatBytes(info.totalBytes) : usage.loading ? '计算中…' : '—'}
            </span>
            <Button size="sm" variant="outline" onClick={() => onManageCharacter(role.id)}>
              前往管理
              <ChevronRight aria-hidden="true" />
            </Button>
          </article>
        )
      })}
      {!characters.length && <p>没有已保存的角色。</p>}
    </div>
  )
}

function StorageLocations({ roots }) {
  const [error, setError] = useState(null)
  const writableRoots = roots.filter((root) => !root.readonly)

  async function handleOpen(id) {
    setError(null)
    try {
      await desktopHost().openStorageFolder(id)
    } catch (failure) {
      setError({ id, message: errorMessage(failure) })
    }
  }

  if (!writableRoots.length) return null

  return (
    <section className="settings-card storage-locations">
      <h3>存储位置</h3>
      {writableRoots.map(({ id, name, directory }) => (
        <div className="storage-location" key={id}>
          <div className="storage-row-text">
            <h4>{name}</h4>
            <p title={directory}>{directory}</p>
            {error?.id === id && (
              <p className="settings-error" role="alert">
                {error.message}
              </p>
            )}
          </div>
          <Button
            variant="outline"
            size="sm"
            aria-label={'打开' + name + '文件夹'}
            onClick={() => void handleOpen(id)}
          >
            <FolderOpen aria-hidden="true" />
            打开文件夹
          </Button>
        </div>
      ))}
    </section>
  )
}

function UsageGroups({
  roots,
  appTotal,
  voice,
  cleanup,
  onManage,
  onDownloads,
  onInstall,
  onOpenFolder,
  onQuickClean
}) {
  function resolveVoiceKindForEntry(root, entryName) {
    if (root.id !== 'resourceLibraryRoot') return null
    if (entryName === VOICE_NAMES.gsv) return 'gsv'
    if (entryName === VOICE_NAMES.asr) return 'asr'
    return null
  }

  function renderEntryAction(root, entry) {
    if (entry.name === '未识别文件') {
      return (
        <Button
          size="xs"
          variant="ghost"
          className="storage-entry-action"
          onClick={(event) => {
            event.preventDefault()
            event.stopPropagation()
            onOpenFolder(root.id)
          }}
        >
          打开文件夹
        </Button>
      )
    }

    const voiceKind = resolveVoiceKindForEntry(root, entry.name)
    if (voiceKind && voice.plans?.[voiceKind]) {
      return (
        <span
          className="storage-entry-action"
          onClick={(event) => {
            event.preventDefault()
            event.stopPropagation()
          }}
        >
          <VoiceRuntimeActionButton
            kind={voiceKind}
            plan={voice.plans[voiceKind]}
            busy={voice.busy}
            locked={Boolean(voice.confirmation)}
            onDownloads={onDownloads}
            onPrepare={voice.prepare}
            onInstall={onInstall}
          />
        </span>
      )
    }

    const manageTarget = MANAGE_TARGET_BY_ENTRY[root.id]?.[entry.name]
    if (manageTarget) {
      if (manageTarget.kind === 'data') {
        return (
          <Button
            size="xs"
            variant="ghost"
            className="storage-entry-action"
            onClick={(event) => {
              event.preventDefault()
              event.stopPropagation()
              onManage(event, manageTarget.tab)
            }}
          >
            {manageTarget.label}
          </Button>
        )
      }
      if (manageTarget.kind === 'downloads') {
        return (
          <Button
            size="xs"
            variant="ghost"
            className="storage-entry-action"
            onClick={(event) => {
              event.preventDefault()
              event.stopPropagation()
              onDownloads()
            }}
          >
            {manageTarget.label}
          </Button>
        )
      }
      if (manageTarget.kind === 'clean-assets') {
        return (
          <Button
            size="xs"
            variant="ghost"
            className="storage-entry-action"
            disabled={cleanup.busy}
            onClick={(event) => {
              event.preventDefault()
              event.stopPropagation()
              void onQuickClean(['assets'])
            }}
          >
            {manageTarget.label}
          </Button>
        )
      }
    }

    if (root.id === 'cacheRoot') {
      const cleanupId = CACHE_CLEANUP_BY_ENTRY[entry.name]
      if (cleanupId) {
        return (
          <Button
            size="xs"
            variant="ghost"
            className="storage-entry-action"
            aria-label={'清理' + entry.name}
            disabled={cleanup.busy}
            onClick={(event) => {
              event.preventDefault()
              event.stopPropagation()
              void onQuickClean([cleanupId])
            }}
          >
            清理
          </Button>
        )
      }
    }

    const hint = READONLY_ENTRY_HINTS[root.id]?.[entry.name]
    if (hint) {
      return <span className="storage-entry-hint">{hint}</span>
    }

    return null
  }

  function renderMissingVoiceRows(root) {
    if (root.id !== 'resourceLibraryRoot' || !voice.plans) return null
    const renderedKinds = new Set(
      root.entries.map((entry) => resolveVoiceKindForEntry(root, entry.name)).filter(Boolean)
    )
    const missing = Object.entries(voice.plans).filter(([kind]) => !renderedKinds.has(kind))
    if (!missing.length) return null
    return (
      <div className="storage-voice-extras">
        {missing.map(([kind, plan]) => (
          <div key={kind}>
            <div className="storage-usage-row">
              <span className="storage-entry-label">
                {VOICE_NAMES[kind]}
                <span className="storage-entry-hint">
                  {plan.taskId ? '有安装或更新任务' : plan.installed ? '已安装' : '未安装'}
                </span>
              </span>
              <span className="storage-entry-action-slot">
                <VoiceRuntimeActionButton
                  kind={kind}
                  plan={plan}
                  busy={voice.busy}
                  locked={Boolean(voice.confirmation)}
                  onDownloads={onDownloads}
                  onPrepare={voice.prepare}
                  onInstall={onInstall}
                />
              </span>
            </div>
            {voice.confirmation === kind && (
              <VoiceRuntimeConfirmation
                kind={kind}
                plan={plan}
                busy={voice.busy}
                onCancel={voice.cancelConfirmation}
                onUninstall={voice.uninstall}
              />
            )}
          </div>
        ))}
      </div>
    )
  }

  return (
    <div className="storage-grid">
      <DiskUsageMeter roots={roots} appTotal={appTotal} />
      {roots.map((root) => (
        <section className="storage-root" key={root.id} data-root={root.id}>
          <div className="storage-root-heading">
            <span className="storage-legend-dot" aria-hidden="true" />
            <h3>{root.name}</h3>
            <span className="storage-number">{formatBytes(root.bytes)}</span>
          </div>
          {root.problem && (
            <p className="settings-error" role="alert">
              {root.problem.message}
            </p>
          )}
          <div className="storage-root-entries">
            {root.entries.map((entry) => {
              const action = renderEntryAction(root, entry)
              const voiceKind = resolveVoiceKindForEntry(root, entry.name)
              const hasExpandableDetails = entry.details.length > 0 || entry.hiddenCount > 0
              return (
                <div key={entry.name}>
                  {hasExpandableDetails ? (
                    <details className="storage-entry-details">
                      <summary className="storage-usage-row">
                        <span className="storage-entry-label">{entry.name}</span>
                        <span className="storage-number">{formatBytes(entry.bytes)}</span>
                        <span className="storage-entry-action-slot">{action}</span>
                      </summary>
                      <div className="storage-detail">
                        {entry.details.map((item) => (
                          <div className="storage-usage-row storage-detail-row" key={item.name}>
                            <span className="storage-entry-label">{item.name}</span>
                            <span className="storage-number">{formatBytes(item.bytes)}</span>
                          </div>
                        ))}
                        {entry.hiddenCount > 0 && (
                          <p className="storage-detail-hidden-note">
                            另有 {entry.hiddenCount} 个小于 10 MB 的文件（共{' '}
                            {formatBytes(entry.hiddenBytes)}），可打开文件夹查看。
                          </p>
                        )}
                      </div>
                    </details>
                  ) : (
                    <div className="storage-usage-row">
                      <span className="storage-entry-label">{entry.name}</span>
                      <span className="storage-number">{formatBytes(entry.bytes)}</span>
                      <span className="storage-entry-action-slot">{action}</span>
                    </div>
                  )}
                  {voiceKind && voice.confirmation === voiceKind && (
                    <VoiceRuntimeConfirmation
                      kind={voiceKind}
                      plan={voice.plans?.[voiceKind]}
                      busy={voice.busy}
                      onCancel={voice.cancelConfirmation}
                      onUninstall={voice.uninstall}
                    />
                  )}
                </div>
              )
            })}
            {renderMissingVoiceRows(root)}
          </div>
          {root.id === 'resourceLibraryRoot' && voice.status && (
            <p role="status" className="storage-result">
              {voice.status}
            </p>
          )}
          {root.id === 'cacheRoot' && cleanup.result && (
            <div role="status" className="storage-result">
              {cleanup.result.map((item, index) => (
                <p key={index}>{item}</p>
              ))}
            </div>
          )}
        </section>
      ))}
    </div>
  )
}

/**
 * 设置浮窗里的存储总览与应用数据子页。
 * @param {object} props 桌面业务客户端、主页和资源状态，以及浮窗导航回调。
 * @returns {import('react').ReactElement} 存储统计、清理与应用数据管理视图。
 */
export function StoragePage({
  client,
  flow,
  characters,
  setup,
  onDownloads,
  onInstall,
  onManageCharacter
}) {
  const overview = useStorageRefresh(desktopHost().getStorageOverview)
  const [detail, setDetail] = useState(false)
  const [detailTab, setDetailTab] = useState('chats')
  const [cleanupBusy, setCleanupBusy] = useState(false)
  const [cleanupResult, setCleanupResult] = useState(null)
  const [folderError, setFolderError] = useState(null)
  const screen = useRef(null)
  const bookmark = useRef(null)
  const back = useRef(null)

  const voice = useVoiceRuntimeActions({
    client,
    setup,
    onChanged: overview.refresh
  })

  useLayoutEffect(() => {
    if (!bookmark.current) return
    const { container, scroll, focus } = bookmark.current
    container.scrollTop = detail ? 0 : scroll
    if (detail) back.current?.focus({ preventScroll: true })
    else focus?.focus({ preventScroll: true })
  }, [detail])

  function handleOpenDetails(event, tab = 'chats') {
    const container = screen.current.closest('.settings-content')
    bookmark.current = { container, scroll: container.scrollTop, focus: event.currentTarget }
    setDetailTab(tab)
    setDetail(true)
  }

  async function handleOpenFolder(id) {
    setFolderError(null)
    try {
      await desktopHost().openStorageFolder(id)
    } catch (failure) {
      setFolderError(errorMessage(failure))
    }
  }

  async function runClean(ids) {
    const targetIds = Array.isArray(ids) ? ids : [ids]
    setCleanupBusy(true)
    setCleanupResult(null)
    try {
      const results = await desktopHost().cleanStorage(targetIds)
      const notes = results.flatMap((row) =>
        row.failed.map((failure) => row.name + ' · ' + failure.message)
      )
      const skipped = results.reduce((total, row) => total + (row.skipped ?? 0), 0)
      if (skipped) notes.push(`另有 ${skipped} 项资源仍在使用或已经不存在。`)
      setCleanupResult(notes.length ? ['所选项目已检查。', ...notes] : ['清理完成'])
      await overview.refresh()
    } catch (error) {
      setCleanupResult([errorMessage(error)])
    } finally {
      setCleanupBusy(false)
    }
  }

  async function handleRefreshAll() {
    await Promise.all([overview.refresh(), voice.reloadPlans()])
  }

  const roots = overview.value ?? []
  const total = roots.reduce((sum, root) => sum + (root.bytes ?? 0), 0)

  const cleanupController = {
    busy: cleanupBusy,
    result: cleanupResult
  }

  return (
    <div ref={screen} className="storage-page">
      <div hidden={detail}>
        <div className="storage-summary">
          <div>
            <p>应用占用</p>
            <strong>{overview.value ? formatBytes(total) : '—'}</strong>
            {roots.some(({ bytes }) => bytes == null) && <small>仅统计当前可读取的目录</small>}
          </div>
          <Button
            size="sm"
            variant="ghost"
            onClick={() => void handleRefreshAll()}
            disabled={overview.loading}
          >
            <RefreshCw
              className={overview.loading ? 'storage-spin' : undefined}
              aria-hidden="true"
            />
            {overview.loading ? '正在统计…' : '刷新'}
          </Button>
        </div>
        {(overview.problem || folderError) && (
          <p className="settings-error" role="alert">
            {overview.problem ?? folderError}
          </p>
        )}
        <UsageGroups
          roots={roots}
          appTotal={total}
          voice={voice}
          cleanup={cleanupController}
          onManage={handleOpenDetails}
          onDownloads={onDownloads}
          onInstall={onInstall}
          onOpenFolder={(id) => void handleOpenFolder(id)}
          onQuickClean={runClean}
        />
        {!!roots.length && <StorageLocations roots={roots} />}
      </div>
      {detail && (
        <section className="storage-data">
          <div className="storage-data-heading">
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label="返回存储总览"
              ref={back}
              onClick={() => setDetail(false)}
            >
              <ChevronLeft aria-hidden="true" />
            </Button>
            <h3>应用数据</h3>
          </div>
          <Tabs value={detailTab} onValueChange={setDetailTab}>
            <TabList className="storage-tabs" aria-label="应用数据类型">
              <Tab value="chats">聊天记录</Tab>
              <Tab value="characters">角色</Tab>
            </TabList>
            <TabPanel value="chats">
              <ChatStorage client={client} flow={flow} onChanged={overview.refresh} />
            </TabPanel>
            <TabPanel value="characters">
              <CharacterStorage
                client={client}
                characters={characters}
                onManageCharacter={onManageCharacter}
              />
            </TabPanel>
          </Tabs>
        </section>
      )}
    </div>
  )
}
