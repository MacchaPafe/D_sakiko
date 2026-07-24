export const initialConversationState = {
  connection: 'connecting',
  runtimeMode: 'mock',
  chatSummaries: [],
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
  displayLanguage: 'translation',
  pendingChatId: null,
  error: null,
}

function appendUniqueMessage(messages, message) {
  if (!message || messages.some((item) => item.id === message.id)) return messages
  return [...messages, message]
}

export function conversationReducer(state, action) {
  switch (action.type) {
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
        case 'runtime_ready':
          return {
            ...state,
            connection: 'ready',
            runtimeMode: event.data.mode || 'mock',
          }

        case 'chat_list_snapshot':
          return {
            ...state,
            chatSummaries: event.data.chats,
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
            error: {
              code: event.data.code,
              message: event.data.message,
            },
          }

        default:
          return state
      }
    }

    case 'connection_lost':
      return {
        ...state,
        connection: 'offline',
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
