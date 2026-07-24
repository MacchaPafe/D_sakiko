import {
  cloneMockValue,
  createInitialMockChats,
  MOCK_BACKGROUNDS,
  MOCK_CHARACTERS,
  mockResponseFor,
} from './mockData'

const nowSeconds = () => Math.floor(Date.now() / 1000)

export class MockRuntimeClient {
  constructor() {
    this.chats = createInitialMockChats()
    this.currentChatId = this.chats[0].chat_id
    this.phase = 'idle'
    this.turnId = null
    this.backgroundIndex = 0
    this.listener = null
    this.timers = new Set()
    this.turnTimers = new Set()
  }

  connect(listener) {
    this.listener = listener
    this.schedule(() => this.emit('runtime_ready', { mode: 'mock' }), 80)
    this.schedule(() => this.publishChatList(), 140)
    this.schedule(() => this.publishState(), 200)
  }

  disconnect() {
    for (const timer of this.timers) clearTimeout(timer)
    this.timers.clear()
    this.turnTimers.clear()
    this.listener = null
  }

  getState() {
    this.publishState()
  }

  getChatList() {
    this.publishChatList()
  }

  switchChat(chatId) {
    if (chatId === this.currentChatId) {
      this.publishState()
      return true
    }
    if (this.phase !== 'idle') {
      this.emitError('CHAT_BUSY', '当前回复完成后才能切换会话。', chatId)
      return false
    }
    if (!this.chatById(chatId)) {
      this.emitError('CHAT_NOT_FOUND', '这条会话已经不存在。', chatId)
      return false
    }

    this.currentChatId = chatId
    this.schedule(() => {
      this.publishChatList()
      this.publishState()
    }, 220)
    return true
  }

  createChat({ characterId, name }) {
    if (this.phase !== 'idle') {
      this.emitError('CHAT_BUSY', '当前回复完成后才能新建会话。')
      return false
    }
    const character = MOCK_CHARACTERS[characterId]
    if (!character) {
      this.emitError('CHARACTER_NOT_FOUND', '没有找到这个角色。')
      return false
    }

    const chatId = `chat_${characterId}_${Date.now()}`
    this.chats.push({
      chat_id: chatId,
      name: name.trim() || `${character.name}的新对话`,
      character_id: characterId,
      last_active_at: nowSeconds(),
      messages: [],
    })
    this.currentChatId = chatId
    this.schedule(() => {
      this.publishChatList()
      this.publishState()
    }, 180)
    return true
  }

  sendMessage(chatId, text, clientMessageId) {
    const chat = this.chatById(chatId)
    if (!chat || chatId !== this.currentChatId) {
      this.emitError('CHAT_MISMATCH', '当前会话已经变化，请重新发送。', chatId)
      return false
    }
    if (this.phase !== 'idle') {
      this.emitError('CHAT_BUSY', '请等待当前回复完成。', chatId)
      return false
    }

    const turnId = `turn_${Date.now()}`
    const userMessage = {
      id: `user_${clientMessageId}`,
      role: 'user',
      text,
      translation: '',
      created_at: nowSeconds(),
      turn_id: turnId,
      sequence: 0,
      emotion: 'neutral',
      audio_url: '',
      status: 'ready',
    }

    chat.messages.push(userMessage)
    chat.last_active_at = nowSeconds()
    this.phase = 'thinking'
    this.turnId = turnId
    this.emit('user_message_ack', { message: cloneMockValue(userMessage) }, chatId, turnId)
    this.emitPhase('thinking', chatId, turnId)
    this.publishChatList()

    const character = MOCK_CHARACTERS[chat.character_id]
    const segments = mockResponseFor(chat.character_id)
    segments.forEach((segment, index) => {
      this.scheduleTurn(() => {
        if (this.turnId !== turnId) return
        const assistantMessage = {
          id: `${turnId}_segment_${index}`,
          role: 'assistant',
          text: segment.text,
          translation: segment.translation,
          created_at: nowSeconds(),
          turn_id: turnId,
          sequence: index,
          emotion: segment.emotion,
          audio_url: character.audio_url,
          status: 'ready',
        }
        chat.messages.push(assistantMessage)
        chat.last_active_at = nowSeconds()
        this.phase = 'tts'
        this.emitPhase('tts', chatId, turnId)
        this.emit(
          'assistant_segment_ready',
          { message: cloneMockValue(assistantMessage) },
          chatId,
          turnId,
        )
        this.publishChatList()
      }, 760 + index * 820)
    })

    this.scheduleTurn(() => {
      if (this.turnId !== turnId) return
      this.phase = 'idle'
      this.turnId = null
      this.emit(
        'assistant_turn_complete',
        { status: 'success', segment_count: segments.length },
        chatId,
        turnId,
      )
      this.publishChatList()
    }, 980 + segments.length * 820)
    return true
  }

  cancelTurn(turnId) {
    if (!turnId || this.turnId !== turnId) return false
    for (const timer of this.turnTimers) {
      clearTimeout(timer)
      this.timers.delete(timer)
    }
    this.turnTimers.clear()

    const chatId = this.currentChatId
    this.phase = 'idle'
    this.turnId = null
    this.emit('assistant_turn_complete', { status: 'cancelled' }, chatId, turnId)
    this.publishChatList()
    return true
  }

  nextBackground() {
    this.backgroundIndex = (this.backgroundIndex + 1) % MOCK_BACKGROUNDS.length
    this.emit('background_changed', {
      background: cloneMockValue(MOCK_BACKGROUNDS[this.backgroundIndex]),
      backgrounds: cloneMockValue(MOCK_BACKGROUNDS),
    })
  }

  publishState() {
    const chat = this.chatById(this.currentChatId)
    if (!chat) return
    this.emit('state_snapshot', {
      current_chat_id: chat.chat_id,
      character: cloneMockValue(MOCK_CHARACTERS[chat.character_id]),
      messages: cloneMockValue(chat.messages),
      phase: this.phase,
      turn_id: this.turnId,
      background: cloneMockValue(MOCK_BACKGROUNDS[this.backgroundIndex]),
      backgrounds: cloneMockValue(MOCK_BACKGROUNDS),
    }, chat.chat_id, this.turnId)
  }

  publishChatList() {
    const summaries = this.chats
      .map((chat) => {
        const character = MOCK_CHARACTERS[chat.character_id]
        const lastMessage = chat.messages.at(-1)
        return {
          chat_id: chat.chat_id,
          name: chat.name,
          character: cloneMockValue(character),
          last_message_preview: lastMessage
            ? (lastMessage.translation || lastMessage.text)
            : '暂无消息',
          last_active_at: chat.last_active_at,
          status: chat.chat_id === this.currentChatId ? this.phase : 'idle',
        }
      })
      .sort((left, right) => right.last_active_at - left.last_active_at)

    this.emit('chat_list_snapshot', {
      chats: summaries,
      current_chat_id: this.currentChatId,
    })
  }

  chatById(chatId) {
    return this.chats.find((chat) => chat.chat_id === chatId)
  }

  emitPhase(phase, chatId, turnId) {
    this.emit('assistant_turn_phase', { phase }, chatId, turnId)
  }

  emitError(code, message, chatId = this.currentChatId) {
    this.emit('error', { code, message }, chatId, this.turnId)
  }

  emit(type, data, chatId = this.currentChatId, turnId = this.turnId) {
    this.listener?.({
      protocol_version: 1,
      type,
      event_id: `evt_${Date.now()}_${Math.random().toString(16).slice(2)}`,
      timestamp: nowSeconds(),
      chat_id: chatId,
      turn_id: turnId,
      data,
    })
  }

  schedule(callback, delay) {
    const timer = setTimeout(() => {
      this.timers.delete(timer)
      callback()
    }, delay)
    this.timers.add(timer)
    return timer
  }

  scheduleTurn(callback, delay) {
    const timer = this.schedule(() => {
      this.turnTimers.delete(timer)
      callback()
    }, delay)
    this.turnTimers.add(timer)
  }
}
