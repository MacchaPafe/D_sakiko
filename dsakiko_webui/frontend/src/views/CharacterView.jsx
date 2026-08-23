import { Image, List, MessageCircle } from 'lucide-react'
import { useMemo, useState } from 'react'
import { IconButton } from '../components/IconButton'
import { MessageComposer } from '../components/MessageComposer'
import { PlaybackButton } from '../components/PlaybackButton'
import { Live2DStage } from '../live2d/Live2DStage'

function visibleText(message, displayLanguage) {
  if (displayLanguage === 'translation' && message?.translation) return message.translation
  return message?.text || ''
}

export function CharacterView({ state, actions, audio, active, motionGroup }) {
  const [expandedMessageId, setExpandedMessageId] = useState(null)
  const assistantMessages = useMemo(
    () => state.messages.filter((message) => message.role === 'assistant'),
    [state.messages],
  )
  const latestMessage = assistantMessages.at(-1)
  const draft = state.draftsByChatId[state.currentChatId] || ''
  const pendingImages = state.pendingImagesByChatId[state.currentChatId] || []
  const busy = state.phase !== 'idle'
  const expanded = latestMessage?.id === expandedMessageId

  const backgroundStyle = {
    '--scene-background-color': state.background?.color || '#d7dde0',
    '--scene-background-image': state.background?.image_url
      ? `url("${state.background.image_url}")`
      : 'none',
    '--character-accent': state.character?.accent || '#c83f5c',
  }

  return (
    <section
      className={`character-screen ${active ? 'is-active' : 'is-inactive'}`}
      style={backgroundStyle}
      aria-hidden={!active}
    >
      <div className="character-stage">
        <Live2DStage
          modelUrl={state.character?.model_url}
          active={active}
          motionGroup={motionGroup}
          mouthOpenRef={audio.volumeRef}
        />
      </div>

      <header className="top-bar character-header">
        <IconButton label="打开对话列表" onClick={actions.openChatList}>
          <List size={24} />
        </IconButton>
        <div className="top-bar__identity">
          <strong>{state.character?.name || '加载中'}</strong>
          <span className="online-state"><i />在线</span>
        </div>
        <div className="character-header__actions">
          <IconButton label="切换背景" onClick={actions.nextBackground}>
            <Image size={22} />
          </IconButton>
          <IconButton label="切换到聊天模式" onClick={() => actions.setView('chat')}>
            <MessageCircle size={23} />
          </IconButton>
        </div>
      </header>

      <div className="character-bottom">
        {(latestMessage || busy) && (
          <section
            className={`dialogue-overlay ${expanded ? 'is-expanded' : ''}`}
            aria-live="polite"
            aria-expanded={expanded}
            onClick={() => setExpandedMessageId(expanded ? null : latestMessage?.id)}
          >
            <span className="dialogue-speaker">{state.character?.name}</span>
            {latestMessage && (
              <span className="dialogue-playback">
                <PlaybackButton
                  message={latestMessage}
                  playback={audio.playback}
                  onToggle={audio.toggleMessage}
                />
              </span>
            )}
            {busy && !latestMessage ? (
              <p className="thinking-text">正在思考</p>
            ) : (
              <p>{visibleText(latestMessage, state.displayLanguage)}</p>
            )}
          </section>
        )}

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
      </div>

    </section>
  )
}
