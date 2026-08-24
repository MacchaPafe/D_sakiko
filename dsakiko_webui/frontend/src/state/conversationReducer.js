export const initialConversationState = {
  connection: 'checking_auth',
  runtimeMode: 'web',
  runtimeStatus: null,
  chatSummaries: [],
  characters: [],
  userPersonas: [],
  currentChatId: null,
  character: null,
  messages: [],
  phase: 'idle',
  turnId: null,
  background: null,
  backgrounds: [],
  activeView: 'chat_list',
  chatListReturnView: null,
  preferredSessionView: 'chat',
  draftsByChatId: {},
  pendingImagesByChatId: {},
  capabilities: {},
  displayLanguage: 'translation',
  pendingChatId: null,
  error: null,
  notice: null,
  authRetryUntil: null,
}

function appendUniqueMessage(messages, message) {
  if (!message || messages.some((item) => item.id === message.id)) return messages
  return [...messages, message]
}

export function conversationReducer(state, action) {
  switch (action.type) {
    case 'capabilities_updated':
      return {
        ...state,
        capabilities: { ...state.capabilities, ...action.capabilities },
      }
    case 'hydrate_local_preferences':
      return {
        ...state,
        draftsByChatId: action.draftsByChatId,
        preferredSessionView: action.preferredSessionView,
        displayLanguage: action.displayLanguage,
      }

    case 'runtime_event': {
      const event = action.event
      switch (event.type) {
        case 'runtime_status':
          return {
            ...state,
            runtimeStatus: event.data,
            connection: event.data.state === 'error'
              ? 'offline'
              : state.connection,
            error: event.data.state === 'error'
              ? { code: 'RUNTIME_ERROR', message: event.data.message }
              : state.error,
          }

        case 'runtime_ready':
          return {
            ...state,
            connection: 'ready',
            runtimeMode: event.data.mode || state.runtimeMode,
            capabilities: event.data.capabilities || {},
            runtimeStatus: {
              state: 'ready',
              message: '角色已准备好。',
              progress: 1,
            },
          }

        case 'chat_list_snapshot':
          return {
            ...state,
            chatSummaries: event.data.chats,
            characters: event.data.characters || state.characters,
            userPersonas: event.data.user_personas || state.userPersonas,
            currentChatId: event.data.current_chat_id || state.currentChatId,
          }

        case 'state_snapshot': {
          const switched = (
            state.pendingChatId === '__new__'
            || state.pendingChatId === event.data.current_chat_id
          )
          return {
            ...state,
            currentChatId: event.data.current_chat_id,
            character: event.data.character,
            messages: event.data.messages,
            phase: event.data.phase || 'idle',
            turnId: event.data.turn_id || null,
            background: event.data.background,
            backgrounds: event.data.backgrounds || [],
            activeView: switched
              ? (state.chatListReturnView || state.preferredSessionView)
              : state.activeView,
            chatListReturnView: switched ? null : state.chatListReturnView,
            pendingChatId: null,
            error: null,
          }
        }

        case 'user_message_ack':
          if (event.chat_id !== state.currentChatId) return state
          return {
            ...state,
            messages: appendUniqueMessage(state.messages, event.data.message),
          }

        case 'assistant_turn_phase':
          if (event.chat_id !== state.currentChatId) return state
          return {
            ...state,
            phase: event.data.phase,
            turnId: event.turn_id || state.turnId,
          }

        case 'assistant_segment_ready':
          if (event.chat_id !== state.currentChatId) return state
          return {
            ...state,
            messages: appendUniqueMessage(state.messages, event.data.message),
          }

        case 'assistant_turn_complete':
          if (event.chat_id !== state.currentChatId) return state
          return {
            ...state,
            phase: 'idle',
            turnId: null,
          }

        case 'background_changed':
          return {
            ...state,
            background: event.data.background,
            backgrounds: event.data.backgrounds || state.backgrounds,
          }

        case 'error':
          return {
            ...state,
            pendingChatId: null,
            error: event.data.error || event.data,
          }

        default:
          return state
      }
    }

    case 'connection_state':
      return {
        ...state,
        connection: action.connection,
        authRetryUntil: Object.hasOwn(action, 'retryUntil')
          ? action.retryUntil
          : (action.connection === 'needs_auth' ? state.authRetryUntil : null),
        error: action.message
          ? { code: action.code || 'CONNECTION', message: action.message }
          : state.error,
      }

    case 'set_notice':
      return { ...state, notice: action.message }

    case 'clear_notice':
      return { ...state, notice: null }

    case 'command_error':
      return {
        ...state,
        pendingChatId: null,
        error: {
          code: action.error.code || 'COMMAND_FAILED',
          message: action.error.message,
        },
      }

    case 'open_chat_list':
      return {
        ...state,
        activeView: 'chat_list',
        chatListReturnView: action.returnView,
      }

    case 'open_current_chat':
      return {
        ...state,
        activeView: action.view,
        chatListReturnView: null,
        preferredSessionView: action.view,
      }

    case 'close_chat_list':
      return {
        ...state,
        activeView: state.chatListReturnView || state.preferredSessionView,
        chatListReturnView: null,
      }

    case 'switch_chat_requested':
      return {
        ...state,
        pendingChatId: action.chatId,
        error: null,
      }

    case 'set_view':
      return {
        ...state,
        activeView: action.view,
        preferredSessionView: action.view,
      }

    case 'set_draft':
      return {
        ...state,
        draftsByChatId: {
          ...state.draftsByChatId,
          [action.chatId]: action.value,
        },
      }

    case 'clear_draft':
      return {
        ...state,
        draftsByChatId: {
          ...state.draftsByChatId,
          [action.chatId]: '',
        },
      }

    case 'add_pending_images':
      return {
        ...state,
        pendingImagesByChatId: {
          ...state.pendingImagesByChatId,
          [action.chatId]: [
            ...(state.pendingImagesByChatId[action.chatId] || []),
            ...action.images,
          ],
        },
      }

    case 'pending_image_uploaded':
      return {
        ...state,
        pendingImagesByChatId: {
          ...state.pendingImagesByChatId,
          [action.chatId]: (state.pendingImagesByChatId[action.chatId] || []).map((image) => (
            image.localId === action.localId
              ? { ...image, status: 'ready', uploadId: action.uploadId }
              : image
          )),
        },
      }

    case 'remove_pending_image':
      return {
        ...state,
        pendingImagesByChatId: {
          ...state.pendingImagesByChatId,
          [action.chatId]: (state.pendingImagesByChatId[action.chatId] || []).filter(
            (image) => image.localId !== action.localId,
          ),
        },
      }

    case 'clear_pending_images':
      return {
        ...state,
        pendingImagesByChatId: {
          ...state.pendingImagesByChatId,
          [action.chatId]: [],
        },
      }

    case 'set_display_language':
      return {
        ...state,
        displayLanguage: action.value,
      }

    case 'clear_error':
      return {
        ...state,
        error: null,
      }

    default:
      return state
  }
}
