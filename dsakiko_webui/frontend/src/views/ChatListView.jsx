import { ChevronDown, Search, Settings, X } from 'lucide-react'
import { useMemo, useState } from 'react'
import editChatIcon from '../assets/edit-chat.svg?url'
import { Avatar } from '../components/Avatar'
import { CreateChatSheet } from '../components/CreateChatSheet'
import { IconButton } from '../components/IconButton'
import { SettingsSheet } from '../components/SettingsSheet'

export function ChatListView({ state, actions }) {
  const [createOpen, setCreateOpen] = useState(false)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [expandedCharacters, setExpandedCharacters] = useState(null)
  const [closing, setClosing] = useState(false)
  const busy = state.phase !== 'idle'
  const normalizedQuery = query.trim().toLocaleLowerCase()
  const chats = useMemo(() => {
    if (!normalizedQuery) return state.chatSummaries
    return state.chatSummaries.filter((chat) => (
      chat.name.toLocaleLowerCase().includes(normalizedQuery)
      || chat.character.name.toLocaleLowerCase().includes(normalizedQuery)
    ))
  }, [normalizedQuery, state.chatSummaries])
  const characterGroups = useMemo(() => {
    const groups = new Map()
    chats.forEach((chat) => {
      const characterId = chat.character.id || chat.character.name
      if (!groups.has(characterId)) {
        groups.set(characterId, { character: chat.character, chats: [] })
      }
      groups.get(characterId).chats.push(chat)
    })
    return [...groups.values()]
  }, [chats])

  const toggleCharacter = (characterId) => {
    setExpandedCharacters((current) => {
      const expanded = current || new Set(
        characterGroups
          .filter((group) => group.chats.some((chat) => chat.chat_id === state.currentChatId))
          .map((group) => group.character.id || group.character.name),
      )
      const next = new Set(expanded)
      if (next.has(characterId)) next.delete(characterId)
      else next.add(characterId)
      return next
    })
  }

  const requestClose = () => setClosing(true)

  return (
    <div className="session-drawer-layer" role="presentation">
      <button
        type="button"
        className={`session-drawer-backdrop ${closing ? 'is-closing' : ''}`}
        aria-label="关闭对话列表"
        onClick={requestClose}
      />

      <aside
        className={`session-drawer ${closing ? 'is-closing' : ''}`}
        aria-label="对话列表"
        onAnimationEnd={(event) => {
          if (closing && event.animationName === 'drawer-out') actions.closeChatList()
        }}
      >
        <header className="session-drawer__header">
          <h1>所有消息</h1>
          <IconButton
            label="新建对话"
            className="new-chat-button"
            disabled={busy}
            onClick={() => setCreateOpen(true)}
          >
            <img src={editChatIcon} alt="" />
          </IconButton>
        </header>

        <label className="session-search">
          <Search size={16} aria-hidden="true" />
          <input
            value={query}
            type="search"
            placeholder="搜索对话"
            aria-label="搜索对话"
            onChange={(event) => setQuery(event.target.value)}
          />
          {query && (
            <button type="button" aria-label="清除搜索" onClick={() => setQuery('')}>
              <X size={15} />
            </button>
          )}
        </label>

        <p className="session-drawer__section-label"></p>
        <div className="session-list" role="list">
          {characterGroups.map((group) => {
            const characterId = group.character.id || group.character.name
            const expanded = normalizedQuery
              || (expandedCharacters
                ? expandedCharacters.has(characterId)
                : group.chats.some((chat) => chat.chat_id === state.currentChatId))
            return (
              <section className="character-chat-group" key={characterId}>
                <button
                  type="button"
                  className="character-group-row"
                  aria-expanded={expanded}
                  onClick={() => toggleCharacter(characterId)}
                >
                  <Avatar character={group.character} size="list" />
                  <span className="character-group-row__name">{group.character.name}</span>
                  <ChevronDown className={expanded ? 'is-expanded' : ''} size={18} aria-hidden="true" />
                </button>
                {expanded && (
                  <div className="character-group-chats" role="list">
                    {group.chats.map((chat) => {
                      const isCurrent = chat.chat_id === state.currentChatId
                      const isPending = chat.chat_id === state.pendingChatId
                      const disabled = Boolean(state.pendingChatId) || (busy && !isCurrent)
                      return (
                        <button
                          key={chat.chat_id}
                          type="button"
                          role="listitem"
                          className={`session-row ${isCurrent ? 'is-current' : ''}`}
                          disabled={disabled}
                          aria-current={isCurrent ? 'true' : undefined}
                          onClick={() => actions.selectChat(chat.chat_id)}
                        >
                          <span className="session-row__title">{chat.name}</span>
                          {isPending && <span className="loading-spinner" aria-label="正在切换" />}
                        </button>
                      )
                    })}
                  </div>
                )}
              </section>
            )
          })}

          {characterGroups.length === 0 && (
            <div className="session-empty">
              <span>{normalizedQuery ? '没有匹配的对话' : '还没有对话'}</span>
              {!normalizedQuery && (
                <button type="button" disabled={busy} onClick={() => setCreateOpen(true)}>
                  新建对话
                </button>
              )}
            </div>
          )}
        </div>

        <footer className="session-drawer__footer">
          {busy && <p className="session-drawer__busy">回复完成前还不能切换对话</p>}
          <IconButton label="打开设置" className="drawer-settings-button" onClick={() => setSettingsOpen(true)}>
            <Settings size={21} />
          </IconButton>
        </footer>
      </aside>

      <CreateChatSheet
        open={createOpen}
        busy={busy}
        characters={state.characters}
        userPersonas={state.userPersonas}
        onClose={() => setCreateOpen(false)}
        onCreate={actions.createChat}
      />
      <SettingsSheet
        open={settingsOpen}
        busy={busy}
        onClose={() => setSettingsOpen(false)}
        onLoad={actions.loadSettings}
        onSave={actions.saveSettings}
      />
    </div>
  )
}
