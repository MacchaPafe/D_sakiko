import { describe, expect, it } from 'vitest'
import {
  conversationReducer,
  initialConversationState,
} from './conversationReducer'

describe('conversationReducer Live2D presentation', () => {
  it('hydrates conversation-level presentation from state snapshot', () => {
    const live2d = { resolution: 'resolved', target_id: 'live2d_one' }
    const state = conversationReducer(initialConversationState, {
      type: 'runtime_event',
      event: {
        type: 'state_snapshot',
        data: {
          current_chat_id: 'chat_one',
          character: { id: 'anon' },
          live2d,
          messages: [],
          phase: 'idle',
          background: null,
          backgrounds: [],
        },
      },
    })

    expect(state.live2d).toBe(live2d)
    expect(state.live2dReason).toBe('snapshot')
  })

  it('applies an attributed semantic target change', () => {
    const current = {
      ...initialConversationState,
      currentChatId: 'chat_one',
    }
    const presentation = { resolution: 'resolved', target_id: 'live2d_costume' }
    const state = conversationReducer(current, {
      type: 'runtime_event',
      event: {
        type: 'live2d_presentation_changed',
        chat_id: 'chat_one',
        data: { presentation, reason: 'semantic_target_change' },
      },
    })

    expect(state.live2d).toBe(presentation)
    expect(state.live2dReason).toBe('semantic_target_change')
  })

  it('ignores presentation changes for an inactive conversation', () => {
    const current = {
      ...initialConversationState,
      currentChatId: 'chat_one',
    }
    const state = conversationReducer(current, {
      type: 'runtime_event',
      event: {
        type: 'live2d_presentation_changed',
        chat_id: 'chat_two',
        data: { presentation: { resolution: 'absent' } },
      },
    })

    expect(state).toBe(current)
  })

  it('waits for the state snapshot before changing to a pending chat', () => {
    const current = {
      ...initialConversationState,
      currentChatId: 'chat_one',
      pendingChatId: 'chat_two',
      messages: [{ id: 'old_message' }],
    }
    const state = conversationReducer(current, {
      type: 'runtime_event',
      event: {
        type: 'chat_list_snapshot',
        data: { current_chat_id: 'chat_two', chats: [] },
      },
    })

    expect(state.currentChatId).toBe('chat_one')
    expect(state.messages).toBe(current.messages)
  })

  it('ends the current thinking state as soon as the turn reports an error', () => {
    const current = {
      ...initialConversationState,
      currentChatId: 'chat_one',
      phase: 'thinking',
      turnId: 'turn_one',
    }
    const state = conversationReducer(current, {
      type: 'runtime_event',
      event: {
        type: 'error',
        chat_id: 'chat_one',
        turn_id: 'turn_one',
        data: { error: { code: 'LLM_FAILED', message: '请求失败' } },
      },
    })

    expect(state.phase).toBe('idle')
    expect(state.turnId).toBeNull()
  })
})
