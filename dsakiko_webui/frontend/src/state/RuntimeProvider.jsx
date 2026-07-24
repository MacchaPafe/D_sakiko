import {
  useCallback,
  useEffect,
  useMemo,
  useReducer,
  useRef,
  useState,
} from 'react'
import { MockRuntimeClient } from '../runtime/mockRuntimeClient'
import {
  conversationReducer,
  initialConversationState,
} from './conversationReducer'
import { RuntimeContext } from './runtimeContext'

const DRAFTS_STORAGE_KEY = 'dsakiko-webui-drafts'
const VIEW_STORAGE_KEY = 'dsakiko-webui-preferred-view'
const LANGUAGE_STORAGE_KEY = 'dsakiko-webui-display-language'

function readStoredJson(key, fallback) {
  try {
    const value = window.localStorage.getItem(key)
    return value ? JSON.parse(value) : fallback
  } catch {
    return fallback
  }
}

function readStoredText(key, allowed, fallback) {
  try {
    const value = window.localStorage.getItem(key)
    return allowed.includes(value) ? value : fallback
  } catch {
    return fallback
  }
}

function createInitialState() {
  return {
    ...initialConversationState,
    draftsByChatId: readStoredJson(DRAFTS_STORAGE_KEY, {}),
    preferredSessionView: readStoredText(
      VIEW_STORAGE_KEY,
      ['character', 'chat'],
      'chat',
    ),
    displayLanguage: readStoredText(
      LANGUAGE_STORAGE_KEY,
      ['original', 'translation'],
      'translation',
    ),
  }
}

function writeStoredValue(key, value) {
  try {
    window.localStorage.setItem(key, value)
  } catch {
    // Private browsing or storage pressure must not break the chat UI.
  }
}

export function RuntimeProvider({ children }) {
  const [state, dispatch] = useReducer(
    conversationReducer,
    undefined,
    createInitialState,
  )
  const stateRef = useRef(state)
  const [client] = useState(() => new MockRuntimeClient())

  useEffect(() => {
    stateRef.current = state
  }, [state])

  useEffect(() => {
    client.connect((event) => dispatch({ type: 'runtime_event', event }))
    return () => client.disconnect()
  }, [client])

  useEffect(() => {
    writeStoredValue(
      DRAFTS_STORAGE_KEY,
      JSON.stringify(state.draftsByChatId),
    )
  }, [state.draftsByChatId])

  useEffect(() => {
    writeStoredValue(VIEW_STORAGE_KEY, state.preferredSessionView)
  }, [state.preferredSessionView])

  useEffect(() => {
    writeStoredValue(LANGUAGE_STORAGE_KEY, state.displayLanguage)
  }, [state.displayLanguage])

  const openChatList = useCallback(() => {
    const current = stateRef.current
    const returnView = current.activeView === 'chat_list'
      ? (current.chatListReturnView || current.preferredSessionView)
      : current.activeView
    dispatch({ type: 'open_chat_list', returnView })
    client.getChatList()
  }, [client])

  const selectChat = useCallback((chatId) => {
    const current = stateRef.current
    const targetView = current.chatListReturnView || current.preferredSessionView
    if (chatId === current.currentChatId) {
      dispatch({ type: 'open_current_chat', view: targetView })
      return
    }
    dispatch({ type: 'switch_chat_requested', chatId })
    client.switchChat(chatId)
  }, [client])

  const createChat = useCallback((input) => {
    const current = stateRef.current
    if (current.phase !== 'idle') return false
    dispatch({ type: 'switch_chat_requested', chatId: '__new__' })
    return client.createChat(input)
  }, [client])

  const setView = useCallback((view) => {
    dispatch({ type: 'set_view', view })
  }, [])

  const updateDraft = useCallback((chatId, value) => {
    if (!chatId) return
    dispatch({ type: 'set_draft', chatId, value })
  }, [])

  const sendMessage = useCallback(() => {
    const current = stateRef.current
    const chatId = current.currentChatId
    const text = (current.draftsByChatId[chatId] || '').trim()
    if (!chatId || !text || current.phase !== 'idle') return false

    const accepted = client.sendMessage(
      chatId,
      text,
      `${Date.now()}_${Math.random().toString(16).slice(2)}`,
    )
    if (accepted) dispatch({ type: 'clear_draft', chatId })
    return accepted
  }, [client])

  const cancelTurn = useCallback(() => {
    const current = stateRef.current
    if (current.turnId) client.cancelTurn(current.turnId)
  }, [client])

  const nextBackground = useCallback(() => {
    client.nextBackground()
  }, [client])

  const setDisplayLanguage = useCallback((value) => {
    dispatch({ type: 'set_display_language', value })
  }, [])

  const clearError = useCallback(() => {
    dispatch({ type: 'clear_error' })
  }, [])

  const value = useMemo(() => ({
    state,
    actions: {
      openChatList,
      selectChat,
      createChat,
      setView,
      updateDraft,
      sendMessage,
      cancelTurn,
      nextBackground,
      setDisplayLanguage,
      clearError,
    },
  }), [
    cancelTurn,
    clearError,
    createChat,
    nextBackground,
    openChatList,
    selectChat,
    sendMessage,
    setDisplayLanguage,
    setView,
    state,
    updateDraft,
  ])

  return (
    <RuntimeContext.Provider value={value}>
      {children}
    </RuntimeContext.Provider>
  )
}
