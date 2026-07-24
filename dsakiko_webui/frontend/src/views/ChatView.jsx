import { ArrowLeft } from 'lucide-react'
import { useEffect, useMemo, useRef } from 'react'
import { Avatar } from '../components/Avatar'
import { IconButton } from '../components/IconButton'
import { LanguageSwitcher } from '../components/LanguageSwitcher'
import { MessageComposer } from '../components/MessageComposer'
import { ModeSwitcher } from '../components/ModeSwitcher'
import { PlaybackButton } from '../components/PlaybackButton'
import { RuntimeIndicator } from '../components/RuntimeIndicator'

function visibleText(message, displayLanguage) {
  if (displayLanguage === 'translation' && message.translation) {
    return message.translation
  }
  return message.text
}

export function ChatView({ state, actions, audio }) {
  const listRef = useRef(null)
  const hasTranslation = useMemo(
    () => state.messages.some((message) => message.role === 'assistant' && message.translation),
    [state.messages],
  )
  const draft = state.draftsByChatId[state.currentChatId] || ''
  const busy = state.phase !== 'idle'

  useEffect(() => {
    const list = listRef.current
    if (!list) return
    list.scrollTo({ top: list.scrollHeight, behavior: 'smooth' })
  }, [state.messages, state.phase])

  const playBubble = (message) => {
    if (!message.audio_url) return
    const selection = window.getSelection()?.toString()
    if (!selection) audio.toggleMessage(message)
  }

  return (
    <section
      className="screen chat-screen"
      style={{ '--character-accent': state.character?.accent || '#168779' }}
      aria-label={`${state.character?.name || ''}的消息`}
    >
      <header className="chat-header">
        <IconButton label="返回会话列表" onClick={actions.openChatList}>
          <ArrowLeft size={22} />
        </IconButton>
        <Avatar character={state.character} size="small" />
        <div className="chat-header__identity">
          <strong>{state.character?.name || '加载中'}</strong>
          <RuntimeIndicator
            connection={state.connection}
            phase={state.phase}
            compact={false}
          />
        </div>
        <ModeSwitcher value="chat" onChange={actions.setView} />
      </header>

      <div className="chat-subbar">
        <LanguageSwitcher
          value={state.displayLanguage}
          disabled={!hasTranslation}
          onChange={actions.setDisplayLanguage}
        />
      </div>

      <div ref={listRef} className="message-list" aria-live="polite">
        {state.messages.map((message, index) => {
          const previous = state.messages[index - 1]
          const isContinuedSegment = (
            message.role === 'assistant'
            && previous?.role === 'assistant'
            && previous.turn_id === message.turn_id
          )
          return (
            <div
              key={message.id}
              className={`message-row message-row--${message.role} ${isContinuedSegment ? 'is-continued' : ''}`}
            >
              {message.role === 'assistant' && !isContinuedSegment && (
                <Avatar character={state.character} size="tiny" />
              )}
              <div
                className="message-bubble"
                role={message.audio_url ? 'button' : undefined}
                tabIndex={message.audio_url ? 0 : undefined}
                onClick={() => playBubble(message)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault()
                    playBubble(message)
                  }
                }}
              >
                <p>{visibleText(message, state.displayLanguage)}</p>
                {message.role === 'assistant' && (
                  <PlaybackButton
                    message={message}
                    playback={audio.playback}
                    onToggle={audio.toggleMessage}
                  />
                )}
              </div>
            </div>
          )
        })}

        {busy && (
          <div className="typing-row" role="status">
            <Avatar character={state.character} size="tiny" />
            <span className="typing-bubble">
              <i />
              <i />
              <i />
            </span>
          </div>
        )}
      </div>

      <MessageComposer
        value={draft}
        busy={busy}
        characterName={state.character?.name}
        onChange={(value) => actions.updateDraft(state.currentChatId, value)}
        onSend={actions.sendMessage}
        onCancel={actions.cancelTurn}
      />
    </section>
  )
}
