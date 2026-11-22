import { Fragment, useState } from 'react'
import * as Menu from '@radix-ui/react-dropdown-menu'
import {
  AlertTriangle,
  GitBranch,
  MoreHorizontal,
  Paperclip,
  Pencil,
  RefreshCw,
  RotateCcw,
  Send,
  Undo2,
  Volume2
} from 'lucide-react'
import { Button } from '../../components/ui/button.jsx'

/**
 * @typedef {{label: string, icon?: import('react').ReactNode, danger?: boolean, onSelect: () => void}} ActionMenuItem
 */

/**
 * 聊天记录行与轮次的下拉操作菜单，样式与服装管理页菜单保持一致。
 * @param {{label: string, items: ActionMenuItem[], disabled?: boolean, triggerClassName?: string}} props 菜单属性。
 * @returns {import('react').ReactElement} Radix 下拉菜单。
 */
function Actions({ label, items, disabled, triggerClassName }) {
  const triggerClass = triggerClassName
    ? `history-action-trigger ${triggerClassName}`
    : 'history-action-trigger'
  return (
    /* 关闭 modal 以免打开菜单时锁定聊天记录列表滚动。 */
    <Menu.Root modal={false}>
      <Menu.Trigger asChild>
        <Button
          size="icon-xs"
          variant="ghost"
          className={triggerClass}
          disabled={disabled}
          aria-label={label}
          title={label}
        >
          <MoreHorizontal size={14} aria-hidden="true" />
        </Button>
      </Menu.Trigger>
      <Menu.Portal>
        <Menu.Content
          className="history-action-menu"
          align="end"
          sideOffset={4}
          collisionPadding={8}
        >
          {items.map((item) => (
            <Menu.Item
              key={item.label}
              className={
                item.danger
                  ? 'history-action-item history-action-item-danger'
                  : 'history-action-item'
              }
              onSelect={item.onSelect}
            >
              {item.icon}
              <span>{item.label}</span>
            </Menu.Item>
          ))}
        </Menu.Content>
      </Menu.Portal>
    </Menu.Root>
  )
}

/**
 * 原地替换用户消息气泡的编辑组件，同时服务「编辑消息」与「编辑并重发」。
 * @param {{action: 'edit'|'resend', text: string, attachments?: {asset?: {id?: string}, name: string}[], busy: boolean, canSubmit: boolean, onChangeText: (value: string) => void, onCancel: () => void, onSubmit: () => void}} props 编辑状态与回调。
 * @returns {import('react').ReactElement} 原地编辑气泡。
 */
function InlineMessageEditor({
  action,
  text,
  attachments,
  busy,
  canSubmit,
  onChangeText,
  onCancel,
  onSubmit
}) {
  function moveCaretToEnd(event) {
    const length = event.currentTarget.value.length
    event.currentTarget.setSelectionRange(length, length)
  }

  function handleTextChange(event) {
    onChangeText(event.target.value)
  }

  function handleKeyDown(event) {
    if (event.key === 'Escape' && !busy) {
      event.preventDefault()
      onCancel()
      return
    }
    if ((event.metaKey || event.ctrlKey) && event.key === 'Enter' && canSubmit) {
      event.preventDefault()
      onSubmit()
    }
  }

  const hint =
    action === 'edit' ? '仅修改这条消息，后续回复会保留。' : '将重新生成回复。'
  const submitLabel = action === 'edit' ? '保存修改' : '保存并重新生成'

  return (
    <div className="history-bubble history-bubble-editing">
      <textarea
        className="history-edit-input"
        autoFocus
        aria-label="编辑消息"
        value={text}
        disabled={busy}
        onFocus={moveCaretToEnd}
        onChange={handleTextChange}
        onKeyDown={handleKeyDown}
      />
      {attachments?.length > 0 && (
        <div className="history-edit-attachments" aria-label="保留的原附件">
          {attachments.map((file, index) => (
            <span
              className="history-edit-attachment"
              key={(file.asset?.id ?? index) + ':' + file.name}
            >
              <Paperclip size={11} aria-hidden="true" />
              <span>{file.name}</span>
            </span>
          ))}
        </div>
      )}
      <div className="history-edit-footer">
        <span className="history-edit-hint">{hint}</span>
        <div className="history-edit-actions">
          <Button size="xs" variant="ghost" disabled={busy} onClick={onCancel}>
            取消
          </Button>
          <Button size="xs" disabled={!canSubmit} onClick={onSubmit}>
            {submitLabel}
          </Button>
        </div>
      </div>
    </div>
  )
}

/**
 * 轮次级别的轻量行内二次确认卡片，按破坏程度区分危险态与警告态。
 * @param {{action: 'rollback'|'recreate', remaining: number, busy: boolean, canConfirm: boolean, onCancel: () => void, onConfirm: () => void}} props 确认状态与回调。
 * @returns {import('react').ReactElement} 紧凑型确认卡片。
 */
function TurnConfirmCard({ action, remaining, busy, canConfirm, onCancel, onConfirm }) {
  const danger = action === 'rollback'
  const message = danger
    ? '后续的对话内容将被删除。'
    : '重新生成回复。'
  const confirmLabel = danger ? '移除后续 ' + remaining + ' 轮' : '重新生成'

  return (
    <div
      className={
        danger
          ? 'history-confirm history-confirm-danger'
          : 'history-confirm history-confirm-warning'
      }
      role="group"
      aria-label={danger ? '确认回溯对话' : '确认重新生成回复'}
    >
      <div className="history-confirm-body">
        {danger ? (
          <AlertTriangle className="history-confirm-icon" size={14} aria-hidden="true" />
        ) : (
          <RotateCcw className="history-confirm-icon" size={14} aria-hidden="true" />
        )}
        <span>{message}</span>
      </div>
      <div className="history-confirm-actions">
        <Button size="xs" variant="ghost" disabled={busy} onClick={onCancel}>
          取消
        </Button>
        <Button
          size="xs"
          variant={danger ? 'destructive' : 'default'}
          disabled={!canConfirm}
          onClick={onConfirm}
        >
          {confirmLabel}
        </Button>
      </div>
    </div>
  )
}

/**
 * @param {object} props 当前轮次和历史快照。
 * @returns {import('react').ReactElement} 按真实轮次绑定的编辑、回溯和回放入口。
 */
export function HistoryTurn({ snapshot, turn, rows, remaining, client, onBranch, renderRow }) {
  const [intent, setIntent] = useState(null)
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const api = client.conversations
  const chatId = snapshot.chat.id
  const locked = busy || !snapshot.admission?.canEditHistory
  const manual = rows.find((row) => row.kind === 'user' && row.id === turn.input.messageId)
  const lastTurn = remaining === 0
  const lastManual = lastTurn && manual
  const editing = ['edit', 'resend'].includes(intent?.action)

  function cancelIntent() {
    setIntent(null)
  }

  function begin(action, row) {
    setError('')
    setText(row?.text ?? '')
    setIntent({ action, row, requestId: crypto.randomUUID(), remaining })
  }

  function beginRecreate() {
    begin('recreate')
  }

  async function submit(action, row) {
    setBusy(true)
    setError('')
    try {
      if (action === 'audio') await api.regenerateAudio(chatId, row.line)
      if (action === 'fork') {
        const id = await api.forkChat(chatId, turn.id)
        await onBranch(id, chatId)
      }
      if (action === 'rollback') await api.rollback(chatId, turn.id, intent.remaining)
      if (action === 'recreate')
        await api.recreateTurn(chatId, turn.id, { requestId: intent.requestId })
      if (action === 'edit' || action === 'resend') {
        const content = { text, attachments: row.attachments ?? [] }
        if (action === 'edit')
          await api.editHistory(chatId, {
            kind: 'user',
            turnId: turn.id,
            messageId: row.id,
            content,
            previousContent: { text: row.text, attachments: row.attachments ?? [] }
          })
        else
          await api.editAndRecreateTurn(chatId, turn.id, content, { requestId: intent.requestId })
      }
      setIntent(null)
    } catch (failure) {
      setError(failure.message)
    } finally {
      setBusy(false)
    }
  }

  function confirm() {
    void submit(intent.action, intent.row)
  }

  function rowActions(row) {
    if (editing && intent.row.id === row.id) return null
    const items = []
    if (row.kind === 'user') {
      items.push({
        label: '编辑消息',
        icon: <Pencil size={14} aria-hidden="true" />,
        onSelect: () => begin('edit', row)
      })
      if (lastManual && manual.id === row.id) {
        items.push({
          label: '编辑并重发',
          icon: <Send size={14} aria-hidden="true" />,
          onSelect: () => begin('resend', row)
        })
      }
    }
    if (row.kind === 'assistant' && row.canSynthesize) {
      items.push({
        label: row.audio ? '重新生成这句音频' : '生成这句音频',
        icon: row.audio ? (
          <RefreshCw size={14} aria-hidden="true" />
        ) : (
          <Volume2 size={14} aria-hidden="true" />
        ),
        onSelect: () => submit('audio', row)
      })
    }
    if (!items.length) return null
    if (snapshot.synthesizingLines?.includes(row.id)) {
      return (
        <span className="history-synthesizing" role="status">
          正在合成…
        </span>
      )
    }
    return <Actions label={row.name + '的消息操作'} items={items} disabled={locked} />
  }

  const turnItems = [
    {
      label: '从本轮结束处分叉',
      icon: <GitBranch size={14} aria-hidden="true" />,
      onSelect: () => submit('fork')
    }
  ]
  if (remaining) {
    turnItems.unshift({
      label: '回溯到本轮结束',
      icon: <Undo2 size={14} aria-hidden="true" />,
      danger: true,
      onSelect: () => begin('rollback')
    })
  }

  const stale = intent?.action === 'rollback' && remaining !== intent.remaining
  const canSubmitEdit =
    editing &&
    !locked &&
    Boolean(text.trim() || intent.row.attachments?.length) &&
    (intent.action !== 'resend' || Boolean(lastManual))
  const canConfirmTurn = !locked && !stale && (intent?.action !== 'recreate' || lastTurn)
  const footerClassName = lastTurn
    ? 'history-turn-footer history-turn-footer-latest'
    : 'history-turn-footer'

  function renderTurnRow(row) {
    const isEditingRow = editing && intent.row.id === row.id
    const editor = isEditingRow ? (
      <InlineMessageEditor
        action={intent.action}
        text={text}
        attachments={row.attachments}
        busy={busy}
        canSubmit={canSubmitEdit}
        onChangeText={setText}
        onCancel={cancelIntent}
        onSubmit={confirm}
      />
    ) : null
    const hasAudioProblem = Boolean(
      snapshot.audioProblems?.findLast((item) => item.line.lineId === row.id)?.problem.message
    )
    return (
      <Fragment key={row.id}>
        {renderRow(row, rowActions(row), editor)}
        {hasAudioProblem && (
          <p className="history-audio-problem" role="status">
            这句语音合成失败，可重新生成。
          </p>
        )}
      </Fragment>
    )
  }

  return (
    <section className="history-turn">
      {rows.map(renderTurnRow)}
      <footer className={footerClassName} data-active={intent ? 'true' : undefined}>
        {lastTurn && (
          <Button
            variant="ghost"
            size="xs"
            className="history-turn-action"
            disabled={locked}
            onClick={beginRecreate}
          >
            <RotateCcw size={12} aria-hidden="true" />
            重新生成回复
          </Button>
        )}
        <Actions
          items={turnItems}
          label="本轮操作"
          disabled={busy || Boolean(snapshot.activeTurnId)}
          triggerClassName="history-turn-menu-trigger"
        />
      </footer>
      {intent && !editing && (
        <TurnConfirmCard
          action={intent.action}
          remaining={remaining}
          busy={busy}
          canConfirm={canConfirmTurn}
          onCancel={cancelIntent}
          onConfirm={confirm}
        />
      )}
      {error && (
        <p role="alert" className="home-inline-problem">
          {error}
        </p>
      )}
    </section>
  )
}
