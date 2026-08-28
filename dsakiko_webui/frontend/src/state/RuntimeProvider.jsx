import {
  useCallback,
  useEffect,
  useMemo,
  useReducer,
  useRef,
  useState,
} from 'react'
import { randomId } from '../runtime/ids'
import {
  createPairingSession,
  createSession,
  deleteUploadedImage,
  getSettings,
  getHealth,
  updateSettings,
  uploadImage,
} from '../runtime/sessionApi'
import {
  nextAuthenticationStep,
  readAndClearPairingToken,
} from '../runtime/pairingBootstrap'
import { WebSocketRuntimeClient } from '../runtime/webSocketRuntimeClient'
import {
  conversationReducer,
  initialConversationState,
} from './conversationReducer'
import { RuntimeContext } from './runtimeContext'

const DRAFTS_STORAGE_KEY = 'dsakiko-webui-drafts'
const VIEW_STORAGE_KEY = 'dsakiko-webui-preferred-view'
const LANGUAGE_STORAGE_KEY = 'dsakiko-webui-display-language'
const MAX_IMAGES_PER_MESSAGE = 4
const SUPERSESSION_CONFIRM_DELAY_MS = 400

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
  const [initialPairingToken] = useState(() => readAndClearPairingToken())
  const pairingTokenRef = useRef(initialPairingToken)
  const [client] = useState(() => new WebSocketRuntimeClient())
  const connectedRef = useRef(false)
  const supersessionCheckRef = useRef(null)
  const mountedRef = useRef(true)
  const removedImageIdsRef = useRef(new Set())

  useEffect(() => {
    stateRef.current = state
  }, [state])

  const checkSupersededSession = useCallback(() => {
    if (supersessionCheckRef.current) return supersessionCheckRef.current

    const check = (async () => {
      try {
        let health = await getHealth()
        if (!health.authenticated) {
          await new Promise((resolve) => {
            window.setTimeout(resolve, SUPERSESSION_CONFIRM_DELAY_MS)
          })
          health = await getHealth()
        }
        if (!mountedRef.current) return
        if (health.authenticated) {
          dispatch({ type: 'clear_error' })
          dispatch({ type: 'connection_state', connection: 'superseded' })
          return
        }
        dispatch({
          type: 'connection_state',
          connection: 'needs_auth',
          code: 'AUTH_REQUIRED',
          message: '控制权被其他设备接管，可重新输入访问码。',
        })
      } catch (error) {
        if (!mountedRef.current) return
        dispatch({
          type: 'connection_state',
          connection: 'offline',
          message: error.message,
        })
      } finally {
        supersessionCheckRef.current = null
      }
    })()
    supersessionCheckRef.current = check
    return check
  }, [])

  const connectClient = useCallback(() => {
    if (connectedRef.current) return
    connectedRef.current = true
    client.connect(
      (event) => dispatch({ type: 'runtime_event', event }),
      (status) => {
        if (status.type === 'session_superseded') {
          connectedRef.current = false
          void checkSupersededSession()
          return
        }
        if (status.type === 'background_suspended') {
          connectedRef.current = false
          return
        }
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
  }, [checkSupersededSession, client])

  const checkConnection = useCallback(async () => {
    dispatch({ type: 'clear_error' })
    dispatch({ type: 'connection_state', connection: 'checking_auth' })
    try {
      const health = await getHealth()
      const pairingToken = pairingTokenRef.current
      const nextStep = nextAuthenticationStep(health.authenticated, pairingToken)
      if (nextStep === 'connect') {
        connectClient()
        return
      }
      pairingTokenRef.current = null
      if (nextStep === 'redeem_pairing') {
        try {
          const result = await createPairingSession(pairingToken)
          dispatch({
            type: 'set_notice',
            message: result.replaced_existing_controller
              ? '已连接到电脑端，并接管控制权。'
              : '已连接到电脑端。',
          })
          window.setTimeout(() => dispatch({ type: 'clear_notice' }), 3500)
          connectClient()
          return
        } catch (error) {
          dispatch({
            type: 'connection_state',
            connection: 'needs_auth',
            code: error.code,
            message: error.message,
          })
          return
        }
      }
      dispatch({ type: 'connection_state', connection: 'needs_auth' })
    } catch (error) {
      dispatch({
        type: 'connection_state',
        connection: 'offline',
        message: error.message,
      })
    }
  }, [connectClient])

  useEffect(() => {
    mountedRef.current = true
    checkConnection()
    return () => {
      mountedRef.current = false
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

  const closeChatList = useCallback(() => {
    dispatch({ type: 'close_chat_list' })
  }, [])

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

  const addImages = useCallback(async (files) => {
    const current = stateRef.current
    const chatId = current.currentChatId
    if (!chatId || current.phase !== 'idle') return false
    if (!current.capabilities.image_input) {
      const error = new Error('当前模型不支持图片输入，请在电脑端切换支持视觉的模型。')
      error.code = 'IMAGE_INPUT_UNSUPPORTED'
      dispatch({ type: 'command_error', error })
      return false
    }

    const existing = current.pendingImagesByChatId[chatId] || []
    const selected = Array.from(files)
      .filter((file) => file.type.startsWith('image/'))
      .slice(0, Math.max(0, MAX_IMAGES_PER_MESSAGE - existing.length))
    if (selected.length === 0) return false

    const images = selected.map((file) => ({
      localId: randomId('draft_image'),
      name: file.name || '图片',
      previewUrl: URL.createObjectURL(file),
      status: 'uploading',
      uploadId: null,
      file,
    }))
    dispatch({ type: 'add_pending_images', chatId, images })

    for (const image of images) {
      try {
        const result = await uploadImage(image.file)
        if (removedImageIdsRef.current.delete(image.localId)) {
          deleteUploadedImage(result.upload_id).catch(() => {})
          continue
        }
        dispatch({
          type: 'pending_image_uploaded',
          chatId,
          localId: image.localId,
          uploadId: result.upload_id,
        })
      } catch (error) {
        URL.revokeObjectURL(image.previewUrl)
        dispatch({ type: 'remove_pending_image', chatId, localId: image.localId })
        dispatch({ type: 'command_error', error })
      }
    }
    return true
  }, [])

  const removePendingImage = useCallback((localId) => {
    const current = stateRef.current
    const chatId = current.currentChatId
    const image = (current.pendingImagesByChatId[chatId] || []).find(
      (item) => item.localId === localId,
    )
    if (!chatId || !image) return
    URL.revokeObjectURL(image.previewUrl)
    if (image.uploadId) deleteUploadedImage(image.uploadId).catch(() => {})
    else removedImageIdsRef.current.add(localId)
    dispatch({ type: 'remove_pending_image', chatId, localId })
  }, [])

  const sendMessage = useCallback(async () => {
    const current = stateRef.current
    const chatId = current.currentChatId
    const text = (current.draftsByChatId[chatId] || '').trim()
    const images = current.pendingImagesByChatId[chatId] || []
    if (
      !chatId
      || (!text && images.length === 0)
      || images.some((image) => image.status !== 'ready')
      || current.phase !== 'idle'
    ) return false

    try {
      await client.sendMessage(
        chatId,
        text,
        randomId('client_msg'),
        images.map((image) => image.uploadId),
      )
      for (const image of images) URL.revokeObjectURL(image.previewUrl)
      dispatch({ type: 'clear_draft', chatId })
      dispatch({ type: 'clear_pending_images', chatId })
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

  const retryLive2D = useCallback(async () => {
    const chatId = stateRef.current.currentChatId
    if (!chatId) return false
    try {
      await client.retryLive2D(chatId)
      return true
    } catch (error) {
      dispatch({ type: 'command_error', error })
      return false
    }
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
        retryUntil: error.retryAfterSeconds
          ? Date.now() + error.retryAfterSeconds * 1000
          : null,
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

  const loadSettings = useCallback(async () => {
    try {
      return await getSettings()
    } catch (error) {
      dispatch({ type: 'command_error', error })
      throw error
    }
  }, [])

  const saveSettings = useCallback(async (settings) => {
    try {
      const result = await updateSettings(settings)
      dispatch({ type: 'capabilities_updated', capabilities: result.capabilities })
      return result
    } catch (error) {
      dispatch({ type: 'command_error', error })
      throw error
    }
  }, [])

  const value = useMemo(() => ({
    state,
    actions: {
      openChatList,
      closeChatList,
      selectChat,
      createChat,
      setView,
      updateDraft,
      addImages,
      removePendingImage,
      sendMessage,
      cancelTurn,
      nextBackground,
      retryLive2D,
      setDisplayLanguage,
      clearError,
      loadSettings,
      saveSettings,
      authenticate,
      retryConnection: checkConnection,
    },
  }), [
    authenticate,
    addImages,
    cancelTurn,
    checkConnection,
    clearError,
    closeChatList,
    createChat,
    nextBackground,
    retryLive2D,
    openChatList,
    removePendingImage,
    loadSettings,
    saveSettings,
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
