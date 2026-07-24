import { AlertTriangle, X } from 'lucide-react'
import { useEffect, useMemo, useRef } from 'react'
import { useAudioController } from './audio/useAudioController'
import { IconButton } from './components/IconButton'
import { useVisualViewport } from './hooks/useVisualViewport'
import { RuntimeProvider } from './state/RuntimeProvider'
import { useRuntime } from './state/runtimeContext'
import { CharacterView } from './views/CharacterView'
import { ChatListView } from './views/ChatListView'
import { ChatView } from './views/ChatView'
import './App.css'

function AppExperience() {
  const { state, actions } = useRuntime()
  const audio = useAudioController()
  const knownAssistantMessagesRef = useRef(new Set())
  const previousChatIdRef = useRef(null)
  const {
    enqueue,
    playback,
    stop,
    unlocked,
  } = audio
  const shouldAutoPlay = (
    state.activeView === 'character'
    || (
      state.activeView === 'chat_list'
      && state.chatListReturnView === 'character'
    )
  )

  useEffect(() => {
    if (previousChatIdRef.current === state.currentChatId) return
    previousChatIdRef.current = state.currentChatId
    stop()
    knownAssistantMessagesRef.current = new Set(
      state.messages
        .filter((message) => message.role === 'assistant')
        .map((message) => message.id),
    )
  }, [state.currentChatId, state.messages, stop])

  useEffect(() => {
    for (const message of state.messages) {
      if (
        message.role !== 'assistant'
        || knownAssistantMessagesRef.current.has(message.id)
      ) {
        continue
      }

      if (shouldAutoPlay) {
        if (!unlocked) continue
        enqueue(message)
      }
      knownAssistantMessagesRef.current.add(message.id)
    }
  }, [
    enqueue,
    state.messages,
    shouldAutoPlay,
    unlocked,
  ])

  const latestAssistant = useMemo(
    () => state.messages.findLast((message) => message.role === 'assistant'),
    [state.messages],
  )
  const playingMessage = useMemo(
    () => state.messages.find((message) => message.id === playback.messageId),
    [playback.messageId, state.messages],
  )
  const motionGroup = state.phase === 'thinking'
    ? 'text_generating'
    : (playingMessage?.emotion || latestAssistant?.emotion || 'idle_motion')

  return (
    <main
      className="app-frame"
      style={{ '--active-accent': state.character?.accent || '#168779' }}
    >
      <CharacterView
        state={state}
        actions={actions}
        audio={audio}
        active={state.activeView === 'character'}
        motionGroup={motionGroup}
      />

      {state.activeView === 'chat_list' && (
        <ChatListView state={state} actions={actions} />
      )}
      {state.activeView === 'chat' && (
        <ChatView state={state} actions={actions} audio={audio} />
      )}

      {state.error && (
        <div className="error-toast" role="alert">
          <AlertTriangle size={18} />
          <span>{state.error.message}</span>
          <IconButton label="关闭" onClick={actions.clearError}>
            <X size={17} />
          </IconButton>
        </div>
      )}
    </main>
  )
}

function App() {
  useVisualViewport()

  return (
    <RuntimeProvider>
      <AppExperience />
    </RuntimeProvider>
  )
}

export default App
