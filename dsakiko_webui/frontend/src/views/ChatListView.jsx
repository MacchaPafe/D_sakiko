import { ChevronRight, Plus } from 'lucide-react'
import { useMemo, useState } from 'react'
import chatListIcon from '../../../../GPT_SoVITS/icons/chat_list.svg?url'
import { Avatar } from '../components/Avatar'
import { CreateChatSheet } from '../components/CreateChatSheet'
import { IconButton } from '../components/IconButton'
import { RuntimeIndicator } from '../components/RuntimeIndicator'

function relativeTime(timestamp) {
  const elapsed = Math.max(0, Math.floor(Date.now() / 1000) - timestamp)
  if (elapsed < 60) return '刚刚'
  if (elapsed < 3600) return `${Math.floor(elapsed / 60)} 分钟前`
  if (elapsed < 86400) return `${Math.floor(elapsed / 3600)} 小时前`
  return new Intl.DateTimeFormat('zh-CN', {
    month: 'numeric',
    day: 'numeric',
  }).format(timestamp * 1000)
}

function activityText(status) {
  if (status === 'thinking') return '正在思考'
  if (status === 'tts') return '正在回复'
  return ''
}

export function ChatListView({ state, actions }) {
  const [createOpen, setCreateOpen] = useState(false)
  const busy = state.phase !== 'idle'
  const chats = useMemo(() => state.chatSummaries, [state.chatSummaries])

  return (
    <section className="screen chat-list-screen" aria-label="会话列表">
      <header className="list-header">
        <div className="list-heading">
          <img src={chatListIcon} alt="" aria-hidden="true" />
          <div>
            <p className="eyebrow">D_SAKIKO</p>
            <h1>会话</h1>
          </div>
        </div>
        <IconButton
          label="新建会话"
          variant="accent"
          disabled={busy}
          onClick={() => setCreateOpen(true)}
        >
          <Plus size={22} />
        </IconButton>
      </header>

      <div className="chat-list" role="list">
        {chats.map((chat) => {
          const isCurrent = chat.chat_id === state.currentChatId
          const isPending = chat.chat_id === state.pendingChatId
          const disabled = Boolean(state.pendingChatId) || (busy && !isCurrent)
          const runningText = activityText(chat.status)

          return (
            <button
              key={chat.chat_id}
              type="button"
              role="listitem"
              className={`chat-list-item ${isCurrent ? 'is-current' : ''}`}
              style={{ '--item-accent': chat.character.accent }}
              disabled={disabled}
              aria-current={isCurrent ? 'true' : undefined}
              onClick={() => actions.selectChat(chat.chat_id)}
            >
              <Avatar character={chat.character} size="large" />
              <span className="chat-list-item__body">
                <span className="chat-list-item__topline">
                  <strong>{chat.name}</strong>
                  <time>{relativeTime(chat.last_active_at)}</time>
                </span>
                <span className="chat-list-item__meta">
                  <span>{chat.character.name}</span>
                  {runningText && <span className="activity-label">{runningText}</span>}
                </span>
                <span className="chat-list-item__preview">
                  {chat.last_message_preview}
                </span>
              </span>
              <span className="chat-list-item__end" aria-hidden="true">
                {isPending ? <span className="loading-spinner" /> : <ChevronRight size={19} />}
              </span>
            </button>
          )
        })}

        {chats.length === 0 && (
          <div className="empty-state">
            <img src={chatListIcon} alt="" />
            <strong>还没有会话</strong>
            <button type="button" onClick={() => setCreateOpen(true)}>
              新建会话
            </button>
          </div>
        )}
      </div>

      <footer className="list-footer">
        <RuntimeIndicator connection={state.connection} phase={state.phase} />
        {busy && <span>当前回复完成后可切换</span>}
      </footer>

      <CreateChatSheet
        open={createOpen}
        busy={busy}
        onClose={() => setCreateOpen(false)}
        onCreate={actions.createChat}
      />
    </section>
  )
}
