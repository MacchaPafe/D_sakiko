import { useId, useState } from 'react'
import {
  AlertCircle,
  AlertTriangle,
  ArchiveRestore,
  CheckCircle2,
  Download,
  Info,
  Search
} from 'lucide-react'
import { Button } from '../../components/ui/button.jsx'
import { Checkbox } from '../../components/ui/checkbox.jsx'
import { Tab, TabList, TabPanel, Tabs } from '../../components/ui/segmented-tabs.jsx'
import { desktopHost } from '../../host/desktop.js'

/**
 * @typedef {{tone: 'info'|'success'|'warning'|'error', text: string, warnings?: string[]}} ArchiveStatus
 */

function describeChat(chat, roleNames) {
  const names = (chat.characterIds ?? []).map((id) => roleNames.get(id) ?? id).filter(Boolean)
  if (chat.mode === 'scripted-dialogue') {
    if (chat.participantIssue) return '小剧场 · 角色缺失'
    return names.length ? '小剧场 · ' + names.join('、') : '小剧场'
  }
  if (chat.participantIssue) return '角色缺失'
  return names.join('、')
}

/**
 * 导出列表中的单条对话选项行，通过 `aria-labelledby` 固定复选框名称为对话标题。
 * @param {{chat: object, checked: boolean, current: boolean, meta: string, busy: boolean, idPrefix: string, onToggle: (chatId: string, checked: boolean) => void}} props 行属性与切换回调。
 * @returns {import('react').ReactElement} 可点击整行切换的对话选项。
 */
function ChatArchiveRow({ chat, checked, current, meta, busy, idPrefix, onToggle }) {
  const titleId = `${idPrefix}-title-${chat.id}`
  const metaId = `${idPrefix}-meta-${chat.id}`

  function handleCheckedChange(next) {
    onToggle(chat.id, Boolean(next))
  }

  return (
    <label className="chat-archive-row" data-selected={checked ? 'true' : undefined}>
      <Checkbox
        checked={checked}
        aria-labelledby={titleId}
        aria-describedby={meta ? metaId : undefined}
        disabled={busy}
        onCheckedChange={handleCheckedChange}
      />
      <div className="chat-archive-row-body">
        <div className="chat-archive-row-heading">
          <span id={titleId} className="chat-archive-row-title">
            {chat.title}
          </span>
          {current && <span className="chat-archive-badge">当前对话</span>}
        </div>
        {meta && (
          <span id={metaId} className="chat-archive-row-meta">
            {meta}
          </span>
        )}
      </div>
    </label>
  )
}

/**
 * 归档操作结果与告警提示条。
 * @param {{status: ArchiveStatus|null}} props 当前状态信息。
 * @returns {import('react').ReactElement|null} 分级状态提示。
 */
function ArchiveStatusBanner({ status }) {
  if (!status) return null
  const isError = status.tone === 'error'
  let Icon = Info
  if (status.tone === 'success') Icon = CheckCircle2
  else if (status.tone === 'warning') Icon = AlertTriangle
  else if (isError) Icon = AlertCircle

  return (
    <div
      className={`chat-archive-status chat-archive-status-${status.tone}`}
      role={isError ? 'alert' : 'status'}
    >
      <Icon className="chat-archive-status-icon" size={15} aria-hidden="true" />
      <div className="chat-archive-status-body">
        <p>{status.text}</p>
        {status.warnings?.length > 0 && (
          <ul className="chat-archive-warnings">
            {status.warnings.map((warning, index) => (
              <li key={index}>{warning}</li>
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}

/**
 * 设置页中的对话导入与导出工作区，采用分段页签区分导出与导入流程。
 * @param {{client: object, flow: object, chats: object[], characters?: object[], currentId?: string|null}} props 会话目录、角色目录与共享客户端。
 * @returns {import('react').ReactElement} 跨端归档视图。
 */
export function ChatArchive({ client, flow, chats, characters = [], currentId }) {
  const idPrefix = useId()
  const [tab, setTab] = useState('export')
  const [ids, setIds] = useState(
    () => new Set(currentId && chats.some((chat) => chat.id === currentId) ? [currentId] : [])
  )
  const [query, setQuery] = useState('')
  const [audio, setAudio] = useState(true)
  const [status, setStatus] = useState(null)
  const [busy, setBusy] = useState(false)

  const roleNames = new Map(
    (characters ?? []).map((role) => [role.id, role.name ?? role.displayName ?? role.id])
  )
  const validChatIds = new Set(chats.map((chat) => chat.id))
  const selectedIds = [...ids].filter((id) => validChatIds.has(id))
  const selectedSet = new Set(selectedIds)

  const normalizedQuery = query.trim().toLowerCase()
  const filtered = chats.filter((chat) => {
    if (!normalizedQuery) return true
    const title = (chat.title ?? '').toLowerCase()
    const meta = describeChat(chat, roleNames).toLowerCase()
    return title.includes(normalizedQuery) || meta.includes(normalizedQuery)
  })
  const allFilteredSelected =
    filtered.length > 0 && filtered.every((chat) => selectedSet.has(chat.id))

  async function perform(operation) {
    setBusy(true)
    setStatus(null)
    try {
      await operation()
    } catch (error) {
      setStatus({ tone: 'error', text: error.message })
    } finally {
      setBusy(false)
    }
  }

  function handleTabChange(nextTab) {
    if (busy) return
    setTab(nextTab)
    setStatus(null)
  }

  function handleQueryChange(event) {
    setQuery(event.target.value)
  }

  function toggleChat(chatId, checked) {
    setIds((previous) => {
      const next = new Set(previous)
      if (checked) next.add(chatId)
      else next.delete(chatId)
      return next
    })
  }

  function selectAllFiltered() {
    setIds((previous) => {
      const next = new Set(previous)
      for (const chat of filtered) next.add(chat.id)
      return next
    })
  }

  function clearSelection() {
    setIds(new Set())
  }

  function handleAudioChange(next) {
    setAudio(Boolean(next))
  }

  function importFile() {
    void perform(async () => {
      const { draftId } = await client.assets.createDraft()
      try {
        const [file] = await desktopHost().importFiles({
          purpose: 'archive',
          draftId,
          single: true,
          extensions: ['zip']
        })
        if (!file) return
        setStatus({ tone: 'info', text: '正在导入对话…' })
        const result = await client.conversations.importChats(file.asset)
        await flow.refreshChats()
        const selected = flow.getSnapshot().chats.find((chat) => chat.id === result.chatIds[0])
        if (result.chatIds.length === 1 && selected) flow.selectChat(selected)
        const warnings = result.warnings.map((item) => item.message)
        setStatus({
          tone: warnings.length ? 'warning' : 'success',
          text: '已导入 ' + result.chatIds.length + ' 个对话。',
          ...(warnings.length ? { warnings } : {})
        })
      } finally {
        await client.assets.discardDraft(draftId)
      }
    })
  }

  function exportFile() {
    void perform(async () => {
      setStatus({ tone: 'info', text: '正在打包对话…' })
      const archive = await client.conversations.exportChats(selectedIds, { includeAudio: audio })
      const saved = await desktopHost().saveChatArchive(archive)
      setStatus(
        saved ? { tone: 'success', text: '已导出 ' + selectedIds.length + ' 个对话。' } : null
      )
    })
  }

  const audioLabelId = `${idPrefix}-audio-label`
  const audioHintId = `${idPrefix}-audio-hint`
  const selectionSummary = normalizedQuery
    ? `匹配 ${filtered.length} 个 · 已选 ${selectedIds.length} 个`
    : `已选 ${selectedIds.length} / 共 ${chats.length} 个对话`

  let listContent
  if (chats.length === 0) {
    listContent = <p className="chat-archive-empty">暂无可导出的对话。</p>
  } else if (filtered.length === 0) {
    listContent = <p className="chat-archive-empty">没有匹配的对话。</p>
  } else {
    listContent = filtered.map((chat) => (
      <ChatArchiveRow
        key={chat.id}
        chat={chat}
        checked={selectedSet.has(chat.id)}
        current={chat.id === currentId}
        meta={describeChat(chat, roleNames)}
        busy={busy}
        idPrefix={idPrefix}
        onToggle={toggleChat}
      />
    ))
  }

  return (
    <div className="chat-archive" data-dialog-busy={busy || undefined}>
      <Tabs className="chat-archive-tabs-root" value={tab} onValueChange={handleTabChange}>
        <TabList className="chat-archive-tabs" aria-label="对话归档操作">
          <Tab value="export" disabled={busy}>
            导出对话
          </Tab>
          <Tab value="import" disabled={busy}>
            导入对话
          </Tab>
        </TabList>

        <TabPanel className="chat-archive-panel" value="export">
          <div className="chat-archive-search">
            <Search className="chat-archive-search-icon" size={15} aria-hidden="true" />
            <input
              type="search"
              className="field-input"
              aria-label="搜索对话"
              placeholder="按标题或角色搜索对话…"
              value={query}
              disabled={busy}
              onChange={handleQueryChange}
            />
          </div>

          <div className="chat-archive-selection-bar">
            <span>{selectionSummary}</span>
            <div className="chat-archive-selection-actions">
              <Button
                type="button"
                size="xs"
                variant="ghost"
                disabled={busy || filtered.length === 0 || allFilteredSelected}
                onClick={selectAllFiltered}
              >
                全选
              </Button>
              <Button
                type="button"
                size="xs"
                variant="ghost"
                disabled={busy || selectedIds.length === 0}
                onClick={clearSelection}
              >
                清空选择
              </Button>
            </div>
          </div>

          <div className="chat-archive-list">{listContent}</div>

          <div className="chat-archive-export-footer">
            <label className="chat-archive-option">
              <Checkbox
                checked={audio}
                aria-labelledby={audioLabelId}
                aria-describedby={audioHintId}
                disabled={busy}
                onCheckedChange={handleAudioChange}
              />
              <span className="chat-archive-option-text">
                <span id={audioLabelId} className="chat-archive-option-title">
                  包含历史语音
                </span>
                <span id={audioHintId} className="chat-archive-option-hint">
                  打包已合成的语音文件，归档体积会明显增大
                </span>
              </span>
            </label>
            <Button type="button" disabled={busy || !selectedIds.length} onClick={exportFile}>
              <Download size={15} aria-hidden="true" />
              导出所选 {selectedIds.length} 个对话…
            </Button>
          </div>
        </TabPanel>

        <TabPanel className="chat-archive-panel" value="import">
          <article className="settings-card chat-archive-import-card">
            <div className="chat-archive-import-icon" aria-hidden="true">
              <ArchiveRestore size={20} />
            </div>
            <div className="chat-archive-import-copy">
              <h4>从归档文件导入对话</h4>
              <p>
                选择之前导出的 .zip
                对话归档。
              </p>
            </div>
            <Button type="button" disabled={busy} onClick={importFile}>
              导入对话…
            </Button>
          </article>
        </TabPanel>
      </Tabs>

      <ArchiveStatusBanner status={status} />
    </div>
  )
}
