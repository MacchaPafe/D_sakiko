import { List, UserRound } from 'lucide-react'
import { useEffect, useLayoutEffect, useMemo, useRef } from 'react'
import { Avatar } from '../components/Avatar'
import { IconButton } from '../components/IconButton'
import { LanguageSwitcher } from '../components/LanguageSwitcher'
import { MessageComposer } from '../components/MessageComposer'
import { PlaybackButton } from '../components/PlaybackButton'

function visibleText(message, displayLanguage) {
  if (displayLanguage === 'translation' && message.translation) return message.translation
  return message.text
}

export function ChatView({ state, actions, audio }) {
  const listRef = useRef(null)
  const hasTranslation = useMemo(
    () => state.messages.some((message) => message.role === 'assistant' && message.translation),
    [state.messages],
  )
  const draft = state.draftsByChatId[state.currentChatId] || ''
  const pendingImages = state.pendingImagesByChatId[state.currentChatId] || []
  const busy = state.phase !== 'idle'

  // Position a newly opened/switched chat at the end before the first paint.
  useLayoutEffect(() => {
    const list = listRef.current
    if (!list) return
    list.scrollTop = list.scrollHeight
  }, [state.currentChatId])

  useEffect(() => {
    const list = listRef.current
    if (!list) return
    list.scrollTo({ top: list.scrollHeight, behavior: 'smooth' })
  }, [state.messages, state.phase])

  return (
    <section
      className="screen chat-screen"
      style={{ '--character-accent': state.character?.accent || '#c83f5c' }}
      aria-label={`${state.character?.name || ''}的消息`}
    >
      <header className="top-bar chat-header">
        <IconButton label="打开对话列表" onClick={actions.openChatList}>
          <List size={24} />
        </IconButton>
        <div className="chat-header__identity">
          <strong>{state.character?.name || '加载中'}</strong>
          <span className="online-state"><i />在线</span>
        </div>
        <div className="chat-header__actions">
          <LanguageSwitcher
            value={state.displayLanguage}
            disabled={!hasTranslation}
            onChange={actions.setDisplayLanguage}
          />
          <IconButton label="切换到角色模式" onClick={() => actions.setView('character')}>
            <UserRound size={22} />
          </IconButton>
        </div>
      </header>

      <div ref={listRef} className="message-list" aria-live="polite">
        {state.messages.length > 0 && <p className="message-time">最近消息</p>}
        {state.messages.map((message) => {
          const isPlaying = (
            message.id === audio.playback.messageId
            && audio.playback.status === 'playing'
          )
          return (
            <div
              key={message.id}
              className={`message-row message-row--${message.role} ${isPlaying ? 'is-playing' : ''}`}
              data-emotion={message.emotion || undefined}
            >
              {message.role === 'assistant' && (
                <Avatar character={state.character} size="message" />
              )}
              <div className="message-bubble">
                {message.attachments?.length > 0 && (
                  <div className="message-images">
                    {message.attachments.map((attachment, index) => (
                      attachment.image_url && (
                        <img
                          key={`${message.id}-image-${index}`}
                          src={attachment.image_url}
                          alt={attachment.original_name || '消息图片'}
                        />
                      )
                    ))}
                  </div>
                )}
                {visibleText(message, state.displayLanguage) && (
                  <p>{visibleText(message, state.displayLanguage)}</p>
                )}
                {message.role === 'assistant' && message.audio_url && (
                  <div className="message-audio-row">
                    <PlaybackButton
                      message={message}
                      playback={audio.playback}
                      onToggle={audio.toggleMessage}
                    />
                  </div>
                )}
              </div>
            </div>
          )
        })}

        {busy && (
          <div className="typing-row" role="status">
            <Avatar character={state.character} size="message" />
            <span className="typing-bubble"><i /><i /><i /></span>
          </div>
        )}
      </div>

      <MessageComposer
        value={draft}
        busy={busy}
        characterName={state.character?.name}
        onChange={(value) => actions.updateDraft(state.currentChatId, value)}
        attachments={pendingImages}
        imageInputSupported={Boolean(state.capabilities.image_input)}
        onAddImages={actions.addImages}
        onRemoveAttachment={actions.removePendingImage}
        onSend={actions.sendMessage}
        onCancel={actions.cancelTurn}
      />
    </section>
  )
}
