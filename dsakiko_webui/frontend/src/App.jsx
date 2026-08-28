import { AlertTriangle, CheckCircle2, X } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { useAudioController } from './audio/useAudioController'
import { IconButton } from './components/IconButton'
import { AccessGate } from './components/AccessGate'
import { useVisualViewport } from './hooks/useVisualViewport'
import { live2dCueFromState } from './live2d/cuePolicy'
import { RuntimeProvider } from './state/RuntimeProvider'
import { useRuntime } from './state/runtimeContext'
import { CharacterView } from './views/CharacterView'
import { ChatListView } from './views/ChatListView'
import { ChatView } from './views/ChatView'
import './App.css'

function AppExperience() {
  const { state, actions } = useRuntime()
  const audio = useAudioController()
  const [cancelledCueTurnId, setCancelledCueTurnId] = useState(null)
  const knownAssistantMessagesRef = useRef(new Set())
  const previousChatIdRef = useRef(null)
  const {
    enqueue,
    playback,
    stop,
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
        enqueue(message)
      }
      knownAssistantMessagesRef.current.add(message.id)
    }
  }, [
    enqueue,
    state.messages,
    shouldAutoPlay,
  ])

  const playingMessage = useMemo(
    () => state.messages.find((message) => message.id === playback.messageId),
    [playback.messageId, state.messages],
  )
  const live2dCue = useMemo(() => {
    return live2dCueFromState({
      phase: state.phase,
      turnId: state.turnId,
      currentChatId: state.currentChatId,
      playback,
      playingMessage,
      cancelledTurnId: cancelledCueTurnId,
    })
  }, [
    cancelledCueTurnId,
    playback,
    playingMessage,
    state.currentChatId,
    state.phase,
    state.turnId,
  ])
  const experienceActions = useMemo(() => ({
    ...actions,
    cancelTurn: async () => {
      const cancelledTurnId = state.turnId
      setCancelledCueTurnId(cancelledTurnId)
      stop()
      const cancelled = await actions.cancelTurn()
      if (!cancelled) setCancelledCueTurnId(null)
      return cancelled
    },
  }), [actions, state.turnId, stop])
  const visibleView = state.activeView === 'chat_list'
    ? (state.chatListReturnView || state.preferredSessionView)
    : state.activeView

  return (
    <main
      className="app-frame"
      data-phase={state.phase}
      data-playback={playback.status}
      style={{ '--active-accent': state.character?.accent || '#168779' }}
    >
      <CharacterView
        state={state}
        actions={experienceActions}
        audio={audio}
        active={visibleView === 'character'}
        live2dCue={live2dCue}
      />

      {visibleView === 'chat' && (
        <ChatView state={state} actions={experienceActions} audio={audio} />
      )}
      {state.activeView === 'chat_list' && (
        <ChatListView state={state} actions={experienceActions} />
      )}

      <div key={visibleView} className="view-transition-wash" aria-hidden="true" />

      {state.error && (
        <div className="error-toast" role="alert">
          <AlertTriangle size={18} />
          <span>{state.error.message}</span>
          <IconButton label="关闭" onClick={actions.clearError}>
            <X size={17} />
          </IconButton>
        </div>
      )}
      {state.notice && (
        <div className="connection-toast" role="status">
          <CheckCircle2 size={18} />
          <span>{state.notice}</span>
        </div>
      )}
    </main>
  )
}

function App() {
  useVisualViewport()

  return (
    <RuntimeProvider>
      <AppContent />
    </RuntimeProvider>
  )
}

function AppContent() {
  const { state, actions } = useRuntime()
  const blocked = (
    state.connection === 'needs_auth'
    || state.connection === 'checking_auth'
    || ((state.connection === 'connecting' || state.connection === 'offline')
      && !state.currentChatId)
  )

  return blocked ? <AccessGate state={state} actions={actions} /> : <AppExperience />
}

export default App
