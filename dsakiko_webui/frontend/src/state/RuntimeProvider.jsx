import {
  useCallback,
  useEffect,
  useMemo,
  useReducer,
  useRef,
  useState,
} from 'react'
import { randomId } from '../runtime/ids'
import { createSession, getHealth } from '../runtime/sessionApi'
import { WebSocketRuntimeClient } from '../runtime/webSocketRuntimeClient'
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
  const [client] = useState(() => new WebSocketRuntimeClient())
  const connectedRef = useRef(false)

  useEffect(() => {
    stateRef.current = state
  }, [state])

  const connectClient = useCallback(() => {
    if (connectedRef.current) return
    connectedRef.current = true
    client.connect(
      (event) => dispatch({ type: 'runtime_event', event }),
      (status) => {
        if (status.type === 'auth_required') {
          connectedRef.current = false
          dispatch({
            type: 'connection_state',
            connection: 'needs_auth',
            code: 'AUTH_REQUIRED',
            message: status.message,
          })
          return
        }
        if (status.type === 'command_error') {
          dispatch({ type: 'command_error', error: status.error })
          return
        }
        const connection = {
          connecting: 'connecting',
          connected: 'connecting',
          offline: 'offline',
        }[status.type]
        if (connection) dispatch({ type: 'connection_state', connection })
      },
    )
  }, [client])

  const checkConnection = useCallback(async () => {
    dispatch({ type: 'clear_error' })
    dispatch({ type: 'connection_state', connection: 'checking_auth' })
    try {
      const health = await getHealth()
      if (health.authenticated) {
        connectClient()
      } else {
        dispatch({ type: 'connection_state', connection: 'needs_auth' })
      }
    } catch (error) {
      dispatch({
        type: 'connection_state',
        connection: 'offline',
        message: error.message,
      })
    }
  }, [connectClient])

  useEffect(() => {
    checkConnection()
    return () => {
      connectedRef.current = false
      client.disconnect()
    }
  }, [checkConnection, client])

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
    client.getChatList().catch((error) => {
      dispatch({ type: 'command_error', error })
    })
  }, [client])

  const selectChat = useCallback(async (chatId) => {
    const current = stateRef.current
    const targetView = current.chatListReturnView || current.preferredSessionView
    if (chatId === current.currentChatId) {
      dispatch({ type: 'open_current_chat', view: targetView })
      return
    }
    dispatch({ type: 'switch_chat_requested', chatId })
    try {
      await client.switchChat(chatId)
      return true
    } catch (error) {
      dispatch({ type: 'command_error', error })
      return false
    }
  }, [client])

  const createChat = useCallback(async (input) => {
    const current = stateRef.current
    if (current.phase !== 'idle') return false
    dispatch({ type: 'switch_chat_requested', chatId: '__new__' })
    try {
      await client.createChat(input)
      return true
    } catch (error) {
      dispatch({ type: 'command_error', error })
      return false
    }
  }, [client])

  const setView = useCallback((view) => {
    dispatch({ type: 'set_view', view })
  }, [])

  const updateDraft = useCallback((chatId, value) => {
    if (!chatId) return
    dispatch({ type: 'set_draft', chatId, value })
  }, [])

  const sendMessage = useCallback(async () => {
    const current = stateRef.current
    const chatId = current.currentChatId
    const text = (current.draftsByChatId[chatId] || '').trim()
    if (!chatId || !text || current.phase !== 'idle') return false

    try {
      await client.sendMessage(chatId, text, randomId('client_msg'))
      dispatch({ type: 'clear_draft', chatId })
      return true
    } catch (error) {
      dispatch({ type: 'command_error', error })
      return false
    }
  }, [client])

  const cancelTurn = useCallback(async () => {
    const current = stateRef.current
    if (!current.currentChatId || !current.turnId) return false
    try {
      await client.cancelTurn(current.currentChatId, current.turnId)
      return true
    } catch (error) {
      dispatch({ type: 'command_error', error })
      return false
    }
  }, [client])

  const nextBackground = useCallback(() => {
    client.nextBackground().catch((error) => {
      dispatch({ type: 'command_error', error })
    })
  }, [client])

  const authenticate = useCallback(async (accessCode) => {
    dispatch({ type: 'connection_state', connection: 'checking_auth' })
    try {
      await createSession(accessCode)
      dispatch({ type: 'clear_error' })
      connectClient()
      return true
    } catch (error) {
      dispatch({
        type: 'connection_state',
        connection: error.code === 'AUTH_REQUIRED' ? 'needs_auth' : 'offline',
        code: error.code,
        message: error.message,
      })
      return false
    }
  }, [connectClient])

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
      authenticate,
      retryConnection: checkConnection,
    },
  }), [
    authenticate,
    cancelTurn,
    checkConnection,
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
