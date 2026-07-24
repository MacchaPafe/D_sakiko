import { Image, MessageCircle } from 'lucide-react'
import { useMemo, useState } from 'react'
import chatListIcon from '../../../../GPT_SoVITS/icons/chat_list.svg?url'
import { IconButton } from '../components/IconButton'
import { LanguageSwitcher } from '../components/LanguageSwitcher'
import { MessageComposer } from '../components/MessageComposer'
import { ModeSwitcher } from '../components/ModeSwitcher'
import { PlaybackButton } from '../components/PlaybackButton'
import { RuntimeIndicator } from '../components/RuntimeIndicator'
import { Live2DStage } from '../live2d/Live2DStage'

function visibleText(message, displayLanguage) {
  if (displayLanguage === 'translation' && message?.translation) {
    return message.translation
  }
  return message?.text || ''
}

export function CharacterView({
  state,
  actions,
  audio,
  active,
  motionGroup,
}) {
  const [expandedMessageId, setExpandedMessageId] = useState(null)
  const assistantMessages = useMemo(
    () => state.messages.filter((message) => message.role === 'assistant'),
    [state.messages],
  )
  const latestMessage = assistantMessages.at(-1)
  const hasTranslation = assistantMessages.some((message) => message.translation)
  const draft = state.draftsByChatId[state.currentChatId] || ''
  const busy = state.phase !== 'idle'

  const expanded = latestMessage?.id === expandedMessageId

  const backgroundStyle = {
    '--scene-background-color': state.background?.color || '#cbd8d4',
    '--scene-background-image': state.background?.image_url
      ? `url("${state.background.image_url}")`
      : 'none',
    '--character-accent': state.character?.accent || '#168779',
  }

  return (
    <section
      className={`character-screen ${active ? 'is-active' : 'is-inactive'}`}
      style={backgroundStyle}
      aria-hidden={!active}
    >
      <Live2DStage
        modelUrl={state.character?.model_url}
        active={active}
        motionGroup={motionGroup}
        mouthOpenRef={audio.volumeRef}
      />

      <header className="character-header">
        <IconButton label="打开会话列表" className="project-icon-button" onClick={actions.openChatList}>
          <img src={chatListIcon} alt="" />
        </IconButton>
        <div className="character-identity">
          <strong>{state.character?.name || '加载中'}</strong>
          <RuntimeIndicator connection={state.connection} phase={state.phase} />
        </div>
        <ModeSwitcher value="character" onChange={actions.setView} />
      </header>

      <div className="character-tools">
        <IconButton label="切换背景" onClick={actions.nextBackground}>
          <Image size={19} />
        </IconButton>
        <LanguageSwitcher
          value={state.displayLanguage}
          disabled={!hasTranslation}
          onChange={actions.setDisplayLanguage}
        />
      </div>

      <div className="character-bottom">
        {(latestMessage || busy) && (
          <section
            className={`dialogue-overlay ${expanded ? 'is-expanded' : ''}`}
            aria-live="polite"
            onClick={() => setExpandedMessageId(expanded ? null : latestMessage?.id)}
          >
            <header>
              <span>{state.character?.name}</span>
              {latestMessage && (
                <PlaybackButton
                  message={latestMessage}
                  playback={audio.playback}
                  onToggle={audio.toggleMessage}
                />
              )}
            </header>
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
          onSend={actions.sendMessage}
          onCancel={actions.cancelTurn}
        />
      </div>

      {active && !audio.unlocked && (
        <div className="audio-unlock">
          <button type="button" onClick={audio.unlock}>
            <MessageCircle size={20} />
            <span>进入角色模式</span>
          </button>
        </div>
      )}
    </section>
  )
}
