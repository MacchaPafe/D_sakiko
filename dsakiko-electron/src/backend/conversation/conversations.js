import { randomUUID } from 'node:crypto'
import { SerialQueue } from '../storage/files.js'
import { problemError, toProblem } from '../../shared/contracts/desktop.js'

function message(kind, text, fields = {}) {
  return {
    id: randomUUID(),
    kind,
    speakerId: null,
    text,
    translation: null,
    attachments: [],
    audio: null,
    emotion: null,
    performance: null,
    toolCall: null,
    metadata: {},
    ...fields
  }
}

/**
 * @typedef {object} RunningConversation
 * @property {import('../../shared/contracts/conversation.js').Chat} chat 唯一可修改的对话快照。
 * @property {'idle'|'generating'|'presenting'} activity 当前活动。
 * @property {string|null} sequenceId 当前运行环境的演出编号。
 * @property {object|null} execution 当前执行的私有标记。
 * @property {number} revision 运行态快照版本。
 */

/** 每个 Chat 只由本模块修改；异步结果必须重新验证执行身份。 */
export class Conversations {
  runtimes = new Map()
  queue = new SerialQueue()
  admissions = new SerialQueue()
  selected = null
  listeners = new Set()
  constructor({ store, catalog, agent, speech, performance, getSettings }) {
    Object.assign(this, { store, catalog, agent, speech, performance, getSettings })
    performance.onUpdate = (update) => this.handleProgress(update)
    performance.onDisconnect = () => {
      void this.interruptAll()
    }
  }
  async initialize() {
    for (const chat of await this.store.list()) {
      let changed = false
      for (const turn of chat.turns) {
        for (const entry of turn.messages)
          if (entry.toolCall?.status === 'running') {
            entry.toolCall.status = 'interrupted'
            changed = true
          }
        if (turn.metadata.state === 'running') {
          turn.metadata.state = 'interrupted'
          changed = true
        }
      }
      this.addRuntime(changed ? await this.store.save(chat) : chat)
    }
    return this
  }
  addRuntime(chat) {
    this.runtimes.set(chat.id, {
      chat,
      activity: 'idle',
      activeTurnId: null,
      execution: null,
      sequenceId: null,
      revision: 0,
      problem: null,
      display: new Map(),
      guideMessages: new Map(),
      observers: new Set()
    })
  }
  require(chatId) {
    const state = this.runtimes.get(chatId)
    if (!state) throw problemError('chat_unavailable', '对话不存在。')
    return state
  }
  snapshot(state) {
    return structuredClone({
      chat: state.chat,
      revision: state.revision,
      activity: state.activity,
      activeTurnId: state.activeTurnId,
      selected: state.chat.id === this.selected,
      display: [...state.display.values()],
      problem: state.problem
    })
  }
  publish(state) {
    state.revision += 1
    const snapshot = this.snapshot(state)
    for (const listener of [...this.listeners, ...state.observers]) {
      try {
        listener(snapshot)
      } catch {
        /* 观察者不能阻塞业务推进。 */
      }
    }
  }
  observe(chatId, listener) {
    const state = this.require(chatId)
    listener(this.snapshot(state))
    state.observers.add(listener)
    return () => state.observers.delete(listener)
  }
  observeAll(listener) {
    this.listeners.add(listener)
    return () => this.listeners.delete(listener)
  }
  async listChats() {
    return [...this.runtimes.values()].map((state) => ({
      id: state.chat.id,
      title: state.chat.title,
      characterIds: state.chat.characterIds,
      mode: state.chat.mode,
      activity: state.activity
    }))
  }
  allSnapshots() {
    return [...this.runtimes.values()].map((state) => this.snapshot(state))
  }
  isBusy() {
    return [...this.runtimes.values()].some((state) => state.activity !== 'idle')
  }
  // 只串行化受理和配置，不把模型生成、语音合成或演出放进全局队列。
  configure(operation) {
    return this.admissions.run('configuration', operation)
  }
  /**
   * 空闲时换装并刷新涉及该角色的舞台，其他角色的执行继续推进。
   * @param {string} characterId 角色身份。
   * @param {string} modelId 目录中的模型身份。
   * @returns {Promise<void>} 设置和已存在的空闲场景已更新。
   */
  setCharacterModel(characterId, modelId) {
    return this.configure(() =>
      this.queue.run('selection', async () => {
        const affected = [...this.runtimes.values()].filter((state) =>
          state.chat.characterIds.includes(characterId)
        )
        if (affected.some((state) => state.activity !== 'idle'))
          throw problemError('chat_busy', '该角色正在生成或演出，请等本轮结束或停止后再换装。')
        await this.catalog.setModel(characterId, modelId)
        if (affected.some((state) => state.chat.id === this.selected)) {
          await this.closeReplay()
          const selected = this.require(this.selected)
          await this.performance.setForeground(await this.ensureSequence(selected))
        }
        for (const state of affected) {
          if (state.sequenceId)
            await this.performance.setScene(state.sequenceId, await this.sceneFor(state))
        }
      })
    )
  }
  async createChat(options) {
    const ids = options?.characterIds
    if (!Array.isArray(ids) || ids.length < 1 || ids.length > 2 || new Set(ids).size !== ids.length)
      throw problemError('invalid_chat', '请选择一名或两名不同角色。')
    const characters = await Promise.all(ids.map((id) => this.catalog.resolveCapabilities(id)))
    const userPersona = options.userPersonaId
      ? await this.catalog.resolvePersona(options.userPersonaId)
      : null
    const settings = this.getSettings()
    const chat = await this.store.save({
      id: randomUUID(),
      revision: 0,
      title: options.title?.trim() || characters.map((item) => item.displayName).join(' · '),
      characterIds: ids,
      userPersona,
      mode: ids.length === 2 ? 'scripted-dialogue' : 'single-character',
      meta: { defaults: { language: settings.language }, reminders: [], extensions: {} },
      turns: []
    })
    this.addRuntime(chat)
    this.publish(this.require(chat.id))
    return chat.id
  }
  async sceneFor(state) {
    const characters = await Promise.all(
      state.chat.characterIds.map((id) => this.catalog.resolveCapabilities(id))
    )
    return {
      background: null,
      slots: characters.map((character, index) => ({
        id: character.id,
        displayName: character.displayName,
        presentation: character.presentation,
        layout: {
          x: characters.length === 1 ? 0.5 : 0.3 + index * 0.4,
          y: 0.55,
          scale: characters.length === 1 ? 1 : 0.8
        }
      }))
    }
  }
  async ensureSequence(state) {
    if (state.sequenceId) return state.sequenceId
    // 选中聊天和受理输入可能重叠，但同一聊天只能创建一个运行序列。
    if (!state.sequencePromise) {
      state.sequencePromise = this.sceneFor(state)
        .then((scene) => this.performance.createSequence(scene))
        .then((id) => {
          state.sequenceId = id
          return id
        })
        .finally(() => {
          state.sequencePromise = null
        })
    }
    return state.sequencePromise
  }
  async closeReplay() {
    if (!this.replaySequence) return
    const id = this.replaySequence
    this.replaySequence = null
    await this.performance.closeSequence(id)
  }
  createTurn(chatId, submission, { requestId }) {
    return this.configure(() =>
      this.queue.run(chatId, async () => {
        const state = this.require(chatId)
        if (typeof requestId !== 'string' || !requestId || requestId.length > 100)
          throw problemError('invalid_request', '发送编号无效。')
        const existing = state.chat.turns.find((turn) => turn.metadata.requestId === requestId)
        if (existing) return existing.id
        if (state.activity !== 'idle')
          throw problemError('chat_busy', '请等待本轮演出结束，或停止后再发送。')
        if (
          typeof submission?.text !== 'string' ||
          !submission.text.trim() ||
          submission.text.length > 20000 ||
          submission.attachments?.length
        )
          throw problemError('invalid_input', '请输入不超过 20000 字的文本。原型暂不支持附件。')
        await this.ensureSequence(state)
        if (this.selected === chatId) await this.selectConversation(chatId)
        const settings = this.getSettings()
        if (!settings.baseUrl || !settings.model)
          throw problemError('missing_connection', '请先在设置中配置模型连接。')
        const turn = {
          id: randomUUID(),
          trigger: { kind: 'user', sourceId: requestId },
          metadata: {
            requestId,
            state: 'running',
            language: settings.language,
            model: settings.model
          },
          messages: [message('user', submission.text.trim())]
        }
        const saved = await this.store.save({ ...state.chat, turns: [...state.chat.turns, turn] })
        state.chat = saved
        const execution = {
          id: randomUUID(),
          turnId: turn.id,
          controller: new AbortController(),
          guides: new Set(),
          tasks: new Map(),
          generating: true,
          stopped: false,
          settings
        }
        state.execution = execution
        state.activeTurnId = turn.id
        state.activity = 'generating'
        state.problem = null
        this.publish(state)
        void this.generate(state, execution).catch((error) => this.fail(state, execution, error))
        return turn.id
      })
    )
  }
  valid(state, execution) {
    return state.execution === execution && !execution.stopped && this.runtimes.has(state.chat.id)
  }
  async mutate(state, execution, operation, onCommit) {
    return this.queue.run(state.chat.id, async () => {
      if (!this.valid(state, execution)) return false
      const copy = structuredClone(state.chat)
      operation(copy.turns.find((turn) => turn.id === execution.turnId))
      state.chat = await this.store.save(copy)
      onCommit?.()
      this.publish(state)
      return true
    })
  }
  async generate(state, execution) {
    const characters = await Promise.all(
      state.chat.characterIds.map((id) => this.catalog.resolveCapabilities(id))
    )
    const turn = state.chat.turns.find((item) => item.id === execution.turnId)
    const request = {
      mode: state.chat.mode,
      characters: characters.map(({ id, displayName, description, performances }) => ({
        id,
        displayName,
        description,
        performances
      })),
      userPersona: state.chat.userPersona
        ? {
            displayName: state.chat.userPersona.displayName,
            description: state.chat.userPersona.description
          }
        : null,
      history: state.chat.turns
        .filter((item) => item.id !== turn.id)
        .flatMap((item) => item.messages)
        .map(({ kind, speakerId, text }) => ({ kind, speakerId, text, attachments: [] })),
      input: {
        kind: 'user',
        message: { kind: 'user', speakerId: null, text: turn.messages[0].text, attachments: [] }
      },
      summary: null,
      supplementalContext: [],
      tools: [],
      model: {
        connectionId: 'default',
        model: execution.settings.model,
        temperature: execution.settings.temperature,
        contextTokenBudget: execution.settings.contextTokenBudget
      }
    }
    const result = await this.agent.run(
      request,
      {
        onIntermediate: async (entries) => {
          const accepted = await this.mutate(state, execution, (target) => {
            target.messages.push(...entries.map((entry) => message(entry.kind, entry.text)))
          })
          if (!accepted) throw new DOMException('执行已结束', 'AbortError')
        },
        onToolActivity: async (record) => {
          const accepted = await this.mutate(state, execution, (target) => {
            const existing = target.messages.find(
              (entry) => entry.toolCall?.callId === record.callId
            )
            if (existing) existing.toolCall = record
            else target.messages.push(message('tool', '查看当前时间', { toolCall: record }))
          })
          if (!accepted) throw new DOMException('执行已结束', 'AbortError')
        }
      },
      execution.controller.signal
    )
    if (!this.valid(state, execution) || result.status === 'cancelled') return
    const lines = result.lines.map((line) => message('character', line.text, line))
    if (
      !(await this.mutate(
        state,
        execution,
        (target) => {
          target.messages.push(...lines)
        },
        () => {
          // 首次发布台词就带等待状态，避免登记 guide 之前完整文本闪现。
          for (const line of lines)
            state.display.set(line.id, {
              messageId: line.id,
              mode: 'pending',
              revealedCharacters: 0
            })
        }
      ))
    )
      return
    const guides = lines.map((line) => ({
      slotId: line.speakerId,
      text: line.text,
      translation: line.translation,
      emotion: line.emotion,
      performance: line.performance,
      audio: null,
      ready: false
    }))
    const guideIds = await this.performance.appendGuides(state.sequenceId, guides)
    if (!this.valid(state, execution)) {
      await this.performance.removeGuides(guideIds)
      return
    }
    guideIds.forEach((id, index) => {
      execution.guides.add(id)
      state.guideMessages.set(id, lines[index].id)
    })
    execution.generating = false
    state.activity = 'presenting'
    this.publish(state)
    // 受理保持句子顺序；等待结果可以并行，不把语音队列复制到 Node。
    for (let index = 0; index < lines.length; index += 1) {
      if (!this.valid(state, execution)) break
      const line = lines[index],
        guideId = guideIds[index]
      const character = characters.find((item) => item.id === line.speakerId)
      if (!execution.settings.voiceEnabled || !character.voice) {
        await this.performance.resolveGuide(guideId, { audio: null })
        continue
      }
      try {
        const priority = this.selected === state.chat.id ? 1 : 2
        const taskId = await this.speech.submit({
          text: line.text,
          language: execution.settings.language,
          voice: character.voice,
          priority
        })
        if (!this.valid(state, execution)) {
          await this.speech.cancel([taskId])
          continue
        }
        execution.tasks.set(guideId, taskId)
        const currentPriority = this.selected === state.chat.id ? 1 : 2
        if (priority !== currentPriority) await this.speech.setPriority([taskId], currentPriority)
        void this.completeAudio(state, execution, line, guideId, taskId)
      } catch (error) {
        if (this.valid(state, execution)) {
          state.problem = toProblem(error)
          this.publish(state)
          await this.performance.resolveGuide(guideId, { audio: null })
        }
      }
    }
    this.finishIfIdle(state, execution)
  }
  async completeAudio(state, execution, line, guideId, taskId) {
    try {
      const result = await this.speech.waitForResult(taskId, execution.controller.signal)
      if (!this.valid(state, execution)) return
      let audio = null
      if (result.status === 'succeeded') {
        audio = result.audio
        if (
          !(await this.mutate(state, execution, (turn) => {
            turn.messages.find((entry) => entry.id === line.id).audio = audio
          }))
        )
          return
      } else if (result.problem) state.problem = result.problem
      await this.performance.resolveGuide(guideId, { audio })
    } catch (error) {
      if (this.valid(state, execution)) {
        state.problem = toProblem(error)
        this.publish(state)
        await this.performance.resolveGuide(guideId, { audio: null }).catch(() => {})
      }
    }
  }
  handleProgress(update) {
    const state = [...this.runtimes.values()].find(
      (item) => item.sequenceId === update.snapshot.sequenceId
    )
    if (!state) return
    const current = update.snapshot.current
    if (current) {
      const id = state.guideMessages.get(current.guideId),
        display = state.display.get(id)
      if (display && display.mode !== 'full') {
        display.mode = 'progress'
        display.revealedCharacters = current.revealedCharacters
      }
    }
    for (const finish of update.finished) {
      const id = state.guideMessages.get(finish.guideId)
      if (id) state.display.set(id, { messageId: id, mode: 'full', revealedCharacters: 0 })
      state.execution?.guides.delete(finish.guideId)
      state.guideMessages.delete(finish.guideId)
    }
    if (update.problem) state.problem = update.problem
    this.publish(state)
    if (state.execution) this.finishIfIdle(state, state.execution)
  }
  finishIfIdle(state, execution) {
    if (state.execution !== execution || execution.generating || execution.guides.size) return
    void this.queue
      .run(state.chat.id, async () => {
        if (state.execution !== execution || execution.generating || execution.guides.size) return
        const copy = structuredClone(state.chat)
        const turn = copy.turns.find((item) => item.id === execution.turnId)
        if (turn) turn.metadata.state = execution.stopped ? 'interrupted' : 'completed'
        state.chat = await this.store.save(copy)
        state.execution = null
        state.activity = 'idle'
        state.activeTurnId = null
        this.publish(state)
      })
      .catch((error) => {
        state.problem = toProblem(error)
        state.execution = null
        state.activity = 'idle'
        state.activeTurnId = null
        this.publish(state)
      })
  }
  async fail(state, execution, error) {
    if (!this.valid(state, execution)) return
    state.problem = toProblem(error)
    await this.stopTurn(state.chat.id, execution.turnId).catch(() => {})
  }
  stopTurn(chatId, turnId) {
    return this.queue.run(chatId, async () => {
      const state = this.require(chatId),
        execution = state.execution
      if (!execution || execution.turnId !== turnId) return
      execution.stopped = true
      execution.generating = false
      execution.controller.abort()
      const removed = await this.performance
        .discardPendingGuides([...execution.guides])
        .catch(() => [...execution.guides])
      for (const id of removed) execution.guides.delete(id)
      const taskIds = removed.map((id) => execution.tasks.get(id)).filter(Boolean)
      void this.speech.cancel(taskIds).catch(() => {})
      const copy = structuredClone(state.chat),
        turn = copy.turns.find((item) => item.id === turnId)
      for (const entry of turn.messages) {
        if (entry.kind === 'character')
          state.display.set(entry.id, { messageId: entry.id, mode: 'full', revealedCharacters: 0 })
        if (entry.toolCall?.status === 'running') entry.toolCall.status = 'interrupted'
      }
      turn.metadata.state = 'interrupted'
      state.chat = await this.store.save(copy)
      if (!execution.guides.size) {
        state.execution = null
        state.activeTurnId = null
        state.activity = 'idle'
      } else state.activity = 'presenting'
      this.publish(state)
    })
  }
  selectConversation(chatId) {
    return this.queue.run('selection', async () => {
      await this.closeReplay()
      const state = chatId ? this.require(chatId) : null
      const sequenceId = state ? await this.ensureSequence(state) : null
      // 模型设置已保存但窗口曾断连时，重新进入舞台也应恢复最新装扮。
      if (state?.activity === 'idle')
        await this.performance.setScene(sequenceId, await this.sceneFor(state))
      await this.performance.setForeground(sequenceId)
      this.selected = chatId
      for (const runtime of this.runtimes.values()) {
        const tasks = [...(runtime.execution?.tasks.values() || [])]
        void this.speech.setPriority(tasks, runtime === state ? 1 : 2).catch(() => {})
        this.publish(runtime)
      }
    })
  }
  replayMessage(chatId, messageId) {
    return this.queue.run('selection', async () => {
      const state = this.require(chatId)
      if (state.activity !== 'idle') throw problemError('chat_busy', '请等本轮结束后再回放。')
      const line = state.chat.turns
        .flatMap((turn) => turn.messages)
        .find((item) => item.id === messageId && item.kind === 'character')
      if (!line) throw problemError('invalid_message', '只能回放角色台词。')
      await this.closeReplay()
      this.replaySequence = await this.performance.createSequence(await this.sceneFor(state))
      await this.performance.setForeground(this.replaySequence)
      await this.performance.appendGuides(this.replaySequence, [
        {
          slotId: line.speakerId,
          text: line.text,
          translation: line.translation,
          emotion: line.emotion,
          performance: line.performance,
          audio: line.audio,
          ready: true
        }
      ])
    })
  }
  deleteChat(chatId) {
    return this.queue.run(chatId, async () => {
      const state = this.require(chatId)
      if (state.activity !== 'idle') throw problemError('chat_busy', '请先停止本轮，再删除对话。')
      if (this.selected === chatId) await this.closeReplay()
      if (state.sequenceId) await this.performance.closeSequence(state.sequenceId)
      await this.store.delete(chatId, state.chat.revision)
      this.runtimes.delete(chatId)
      if (this.selected === chatId) this.selected = null
    })
  }
  async interruptAll() {
    await this.closeReplay().catch(() => {})
    for (const state of this.runtimes.values()) {
      if (state.execution) await this.stopTurn(state.chat.id, state.execution.turnId)
      if (state.sequenceId) await this.performance.closeSequence(state.sequenceId).catch(() => {})
      state.sequenceId = null
      state.guideMessages.clear()
      state.execution = null
      state.activity = 'idle'
      state.activeTurnId = null
      this.publish(state)
    }
  }
}
