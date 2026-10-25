import { extractAssetReferences } from '../resources/references.js'
import { generationOptions, speechOptions, presentationOptions } from '../core/validation.js'
import {
  copy,
  merge,
  fields,
  requireValue,
  uuid,
  fingerprint,
  SerialQueue,
  SnapshotBus,
  problem,
  abortable
} from '../core/support.js'
import { chatSettings } from '../core/defaults.js'

/** @typedef {import('../../shared/contracts/conversation.js').UserSubmission} UserSubmission */
/** @typedef {import('../../shared/contracts/conversation.js').LineRef} LineRef */
/** @typedef {import('../../shared/contracts/settings.js').TurnOptions} TurnOptions */

/**
 * @typedef {object} CreateChatOptions
 * @property {string} title 标题。
 * @property {string[]} characterIds 固定参与角色。已创建的 Chat 不能追加角色。
 * @property {import('../../shared/contracts/conversation.js').DialogueMode} mode 编排方式。
 * @property {import('../../shared/contracts/settings.js').ChatSettings} settings 对话设置，创建时校验角色选择。
 * @property {string | null} [userPersonaId] 省略或 null 表示无自定义人格；否则解析并保存固定快照。
 */

/**
 * @typedef {{ kind: 'user', turnId: string, messageId: string, content: UserSubmission }
 *   | { kind: 'line', line: LineRef, text: string, translation: string | null }} HistoryEdit
 */

/**
 * @typedef {object} PreparedTurn
 * @property {import('../llm/interface.js').GenerationRequest} request 一次固定的模型材料，受理时补齐 turnId。
 * @property {import('../../shared/contracts/settings.js').GenerationOptions} generation 本轮解析后的生成设置。
 * @property {import('../../shared/contracts/settings.js').SpeechOptions} speech 本轮固定合成设置。
 * @property {import('../../shared/contracts/characters.js').CharacterCapabilities[]} capabilities 包含声音与形态的快照，不随目录修改漂移。
 * @property {import('../llm/interface.js').ContextInspection} inspection 受理前的预算与能力检查结果。
 */

/**
 * @typedef {object} TurnRun
 * @property {string} turnId 本次受理身份，与重生成前的 Turn 不同。
 * @property {string} group async 资格所属组；停止或语义编辑可使组失效。
 * @property {import('../../shared/contracts/conversation.js').TurnExecution} execution 可公开的执行状态。
 * @property {'generating' | 'presenting'} activity 当前主要活动。
 * @property {AbortController} controller 取消生成与当前等待，不撤销已发生的外部副作用。
 * @property {string[]} guides 普通演出身份，全部计划结算后才释放受理门禁。
 * @property {string[]} speechTasks 已收到编号的合成任务，迟到编号也必须参与取消。
 * @property {Promise<void>[]} pending 在途音频交接，不包含前端交互等待。
 * @property {PreparedTurn} prepared 已冻结的输入投影。
 * @property {Promise<void>} [done] 整轮收尾，用于宿主退出时等待。
 * @property {import('../llm/interface.js').TokenUsage | null} [usage] 模型报告的消耗。
 */

/**
 * @typedef {object} ConversationState
 * @property {import('../../shared/contracts/conversation.js').Chat} chat 当前权威内容；保存故障时可能领先磁盘。
 * @property {string} resourceOwner 持有当前内存中对话所引用资源的临时持有者 ID。
 * @property {number} revision 观察版本，与 chat.revision 的持久化版本分开。
 * @property {TurnRun | null} active 当前执行；停止后保留句未结束时仍非空。
 * @property {Map<string, import('../../shared/contracts/conversation.js').TurnExecution>} executions 本次运行已知的轮次状态。
 * @property {string | null} group 当前仍有效的 async 组，不持久化。
 * @property {import('../../shared/contracts/conversation.js').AsyncResultItem[]} pending 等待自动轮消费的成功结果。
 * @property {Set<string>} acceptedResults 已接收的交互身份，防止重复入队。
 * @property {Map<string, {line: LineRef, guideId: string}>} guides 台词到计划项的对应关系。
 * @property {Map<string, string>} audioTokens 当前句合成资格，拒绝被新任务覆盖的迟到结果。
 * @property {import('../../shared/contracts/conversation.js').ConversationSnapshot['audioProblems']} audioProblems 单句问题，不终止整个组。
 * @property {import('../../shared/contracts/conversation.js').ConversationSnapshot['persistence']} persistence 保存状态与可公开问题。
 * @property {import('../../shared/contracts/common.js').Problem | null} problem 最近的业务问题。
 * @property {boolean} compressing 是否正修改历史摘要。
 * @property {string} sequenceId 后端运行中的演出序列。
 * @property {import('../../shared/contracts/conversation.js').ConversationSnapshot} [published] 最近一次完整发布的快照；读取者不观察半次修改。
 * @property {boolean} [deleted] 删除后拒绝迟到的音频交接。
 */

/**
 * @typedef {object} InteractionRegistration
 * @property {string} chatId 后端查得的所属对话。
 * @property {string} group 登记时有效的轮次组。
 * @property {string} toolName 原调用声明的工具。
 * @property {import('../../shared/contracts/tools.js').ToolExecutionContext} source 原始执行关联，不由页面重建。
 */

/**
 * 对话模块是每个 Chat 的唯一运行时修改所有者；同一 Chat 的受理、历史修改、保存及 async 消费串行协调。
 * 初始输入校验和持久化完成才算受理，此前失败不消费草稿/async 队列、不替换旧轮、不启动工具。
 * 受理后保留输入、附件、已接受 reasoning/调用/台词和副作用。保存失败保留内存并阻止新生成和历史修改，
 * 仍允许查看、停止、重试保存；已运行任务继续结算，结果不能因采用旧 Chat 快照而覆盖新的内容。
 * 每次执行固定角色、配置、工具和知识范围；向 Agent/Speech/Performance 分别投影材料，不透传完整 Chat。
 * 世界书直接上下文和工具通过 worldbook-query-interface.js 的同一本轮查询函数取得知识；本模块唯一接收独立诊断。
 * 受理前直接上下文诊断只暂存，受理后追加 Turn.meta.worldbookDiagnostics；失效结果拒收，保存故障保留在内存。
 * 准备被放弃、生成结束或执行失效时取消本轮查询函数的信号，不允许后续 Turn 或迟到工具继续使用旧绑定。
 * 正常 Turn 到生成、自动语音及后端计划的普通演出全部结算才 finished；这期间不受理同 Chat 的下一轮或历史修改。
 * 语音失败保留文字并无声 ready；后端演出故障须有界结算，客户端播放故障不影响后端推进。async 交互、BGM、驻留/循环过渡不计入完成条件。
 * 待播普通内容清空且本轮不再生成后，清理本轮 transition；独立回放/重合成不让历史 Turn 重新 running。
 * Agent 不可恢复失败归 terminated 并使组失效；保存错误、单句语音/播放错误不是组终止。
 * 手动新轮和显式重生成开新组；自动轮沿用有效组，无有效组的提醒另开组。组和接收资格只存在内存。
 * 历史语义修改作废本 Chat 全部未消费及尚未返回的 async；背景和音频修改不触发此失效。
 * 重启只恢复已保存记录，对缺结果调用做通用中断结算；不恢复任务、演出、交互资格或组，不重放工具。
 * 停止后保留后端计划时间线的当前句，到该句逻辑结束或被显式移除前阻止下一轮，不依赖客户端实际播放。
 * 历史回放由各客户端独立控制，本模块只提供已保存材料；手动重合成目标句保护的细节仍待定。
 * 本文件实现正式契约；由 create-core-backend.js 装配，不复用旧原型对话对象。
 */
export class Conversations {
  /**
   * 绑定对话所需能力；运行状态只属于此实例，不读取 Electron 或页面状态。
   * @param {Pick<import('../create-core-backend.js').CoreBackend, 'store' | 'settings' | 'characters' | 'agent' | 'speech' | 'performance' | 'assets' | 'tools'> & {access: import('../../shared/contracts/tools.js').ToolAccessScope}} dependencies 可信装配提供的模块及工具授权。
   */
  constructor({ store, settings, characters, agent, speech, performance, assets, tools, access }) {
    Object.assign(this, {
      store,
      settings,
      characters,
      agent,
      speech,
      performance,
      assets,
      tools,
      access
    })
    /** @type {Map<string, ConversationState>} */
    this.states = new Map()
    this.loading = new Map()
    this.queue = new SerialQueue()
    this.bus = new SnapshotBus()
    this.runtimeId = uuid()
    this.closed = false
    this.pendingCompletions = new Map()
    this.synthesizing = new Set()
    this.synthesisControllers = new Set()
  }
  /** 同一 Chat 的并发首次访问共享加载，避免创建两个运行时所有者或两条演出序列。 */
  async state(chatId) {
    requireValue(!this.closed, 'backend_closed')
    if (this.states.has(chatId)) return this.states.get(chatId)
    if (!this.loading.has(chatId))
      this.loading.set(
        chatId,
        this.load(chatId).finally(() => this.loading.delete(chatId))
      )
    return this.loading.get(chatId)
  }
  /** 仅恢复存档内容；补结算缺失工具结果，不续跑重启前的生成与副作用。 */
  async load(chatId) {
    const resourceOwner = await this.assets.createTemporaryOwner('chat-memory')
    let state
    try {
      const loaded = await this.store.loadProtected(chatId, resourceOwner)
      requireValue(loaded.status === 'loaded', 'chat_unavailable', '对话不存在或需要恢复存档。')
      /** @type {ConversationState} */
      state = {
        chat: loaded.chat,
        resourceOwner,
        revision: 0,
        active: null,
        executions: new Map(),
        group: null,
        pending: [],
        acceptedResults: new Set(),
        guides: new Map(),
        audioProblems: [],
        audioTokens: new Map(),
        persistence: { state: 'saved', problem: null },
        problem: null,
        compressing: false
      }
      state.sequenceId = await this.withScene(state.chat, (scene) =>
        this.performance.createSequence(scene)
      )
      if (this.interruptMissing(state.chat)) await this.persist(state)
      this.states.set(chatId, state)
      return state
    } catch (error) {
      if (state?.sequenceId) await this.performance.closeSequence(state.sequenceId)
      await this.assets.releaseWhenPossible(resourceOwner)
      throw error
    }
  }
  /** 在执行准备阶段解析角色选择，后续只使用能力快照。 */
  async capabilities(chat, owner = null) {
    const result = []
    for (const id of chat.characterIds) {
      const selection = chat.meta.settings.characters[id] ?? {}
      result.push(await this.characterCapability(owner, id, selection))
    }
    return result
  }
  /** 内部运行路径在角色目录锁内取得引用；只读预览不延长快照生命周期。 */
  async characterCapability(owner, id, selection) {
    const options = {
      formId: selection.formId,
      modelId: selection.modelOverrides?.[selection.formId]
    }
    return owner
      ? this.characters.acquireCapabilities(owner, id, options)
      : this.characters.resolveCapabilities(id, options)
  }

  /** 场景建立独立 owner 后才释放解析快照的临时保护。 */
  async withScene(chat, operation) {
    return this.assets.using(chat, async (owner) => operation(await this.scene(chat, false, owner)))
  }

  /** 恢复历史时允许缺模型降级；新设置的严格校验不能静默吞掉角色配置错误。 */
  async scene(chat, strict = true, owner = null) {
    const slots = []
    for (const id of chat.characterIds) {
      let capability
      try {
        const selection = chat.meta.settings.characters[id] ?? {}
        capability = await this.characterCapability(owner, id, selection)
      } catch (error) {
        if (strict) throw error
        capability = { displayName: id, presentation: null }
      }
      slots.push({
        id,
        displayName: capability.displayName,
        presentation: capability.presentation,
        layout: chat.meta.settings.presentation.characters[id] ?? {
          x: 0.5,
          y: 0.5,
          scale: 1,
          facing: 'right'
        }
      })
    }
    return { background: chat.meta.settings.presentation.background, slots }
  }
  /** 统一计算受理门禁；terminated 但仍保留当前句时，不能把对话误报为空闲可生成。 */
  snapshot(state) {
    const reasons = []
    if (state.active)
      reasons.push(state.active.execution.state === 'terminated' ? 'retained-guide' : 'running')
    if (state.compressing) reasons.push('compressing')
    if (state.persistence.state === 'failed') reasons.push('save-failed')
    return {
      runtimeId: this.runtimeId,
      chat: copy(state.chat),
      revision: state.revision,
      activity: state.compressing ? 'compressing' : (state.active?.activity ?? 'idle'),
      activeTurnId: state.active?.turnId ?? null,
      executions: [...state.executions.values()].map(copy),
      persistence: copy(state.persistence),
      admission: { canGenerate: !reasons.length, canEditHistory: !reasons.length, reasons },
      presentation: { sequenceId: state.sequenceId, lines: [...state.guides.values()].map(copy) },
      audioProblems: copy(state.audioProblems),
      problem: copy(state.problem)
    }
  }
  /** 发布完成的独立快照，避免订阅者在一次异步修改中读取到半成品。 */
  publish(state) {
    state.revision += 1
    state.published = this.snapshot(state)
    this.bus.emit(state.chat.id, state.published)
  }
  /** 保存失败时内存仍是权威内容；只允许显式 retrySave 再写，不能回滚已发生的工具结果。 */
  async persist(state, retry = false) {
    await this.assets.replaceReferences(state.resourceOwner, extractAssetReferences(state.chat))
    if (state.persistence.state === 'failed' && !retry) {
      this.publish(state)
      return false
    }
    try {
      state.chat = await this.store.save(state.chat)
      state.persistence = { state: 'saved', problem: null }
      this.publish(state)
      return true
    } catch (error) {
      state.persistence = { state: 'failed', problem: problem(error) }
      this.publish(state)
      return false
    }
  }
  /** 生成与历史修改共用门禁；部分编辑允许先移除停止后保留的句子。 */
  assertIdle(state, allowRetained = false) {
    requireValue(state.persistence.state !== 'failed', 'save_failed', '请先重试保存当前内容。')
    requireValue(
      !state.compressing &&
        (!state.active || (allowRetained && state.active.execution.state === 'terminated')),
      'chat_busy',
      '本对话尚未完成。'
    )
  }
  /** 校验手动输入的最小结构，纯附件输入不要求另有非空文本。 */
  validateSubmission(submission) {
    fields(submission, ['text', 'attachments'])
    requireValue(
      typeof submission.text === 'string' &&
        Array.isArray(submission.attachments) &&
        (submission.text.trim() || submission.attachments.length)
    )
  }
  /** 将手动、提醒和 async 输入投影为模型可读材料，保留其来源而不伪造成手动发言。 */
  userProjection(message) {
    let text
    let attachments = []
    if (message.source.kind === 'manual') {
      text = message.content.text
      attachments = message.content.attachments
    } else if (message.source.kind === 'async') text = JSON.stringify(message.results)
    else text = message.text
    return {
      messageId: message.id,
      kind: 'user',
      source: copy(message.source),
      text,
      attachments: copy(attachments),
      unavailableAttachments: []
    }
  }
  /** 从最后有效摘要构造上下文投影；不删除存档原文，也不拆散工具调用与结果。 */
  history(chat) {
    let result = []
    for (const turn of chat.turns) {
      const calls = new Map(
        turn.messages
          .filter((message) => message.kind === 'assistant')
          .flatMap((message) => message.parts)
          .filter((part) => part.kind === 'tool-call')
          .map((part) => [part.callId, part.toolName])
      )
      let messages = []
      for (const message of turn.messages) {
        if (message.kind === 'abstract') {
          result = []
          messages = [{ messageId: message.id, kind: 'abstract', text: message.text }]
        }
        if (message.kind === 'user') messages.push(this.userProjection(message))
        if (message.kind === 'assistant')
          messages.push({
            messageId: message.id,
            kind: 'assistant',
            parts: message.parts.map((part) => {
              if (part.kind !== 'dialogue') return copy(part)
              return {
                kind: 'dialogue',
                lines: part.lines.map((line) => ({
                  id: line.id,
                  speakerId: line.speakerId,
                  formId: line.formId,
                  text: line.text,
                  translation: line.translation,
                  emotion: line.emotion,
                  performance: copy(line.performance)
                }))
              }
            }),
            protocol: copy(message.protocol)
          })
        if (message.kind === 'tool-result')
          messages.push({
            messageId: message.id,
            kind: 'tool-result',
            callId: message.callId,
            toolName: calls.get(message.callId),
            outcome: copy(message.outcome)
          })
      }
      if (messages.length) result.push({ turnId: turn.id, messages })
    }
    return result
  }
  /** 按全局、对话、本轮顺序解析并固定材料；此阶段失败不算受理，也不执行工具。 */
  async prepare(chat, input, options) {
    const resourceOwner = await this.assets.createTemporaryOwner(
      'turn',
      extractAssetReferences({ chat, input })
    )
    try {
      const global = await this.settings.get()
      const generation = merge(global.generation, chat.meta.settings.generation, options.generation)
      const speech = merge(global.speech, chat.meta.settings.speech, options.speech)
      generationOptions(generation)
      speechOptions(speech)
      const connection = await this.settings.resolveConnection(generation)
      const capabilities = await this.capabilities(chat, resourceOwner)
      requireValue(
        !chat.meta.settings.worldbook.enabled,
        'worldbook_adapter_required',
        '世界书查询尚需装配检索适配器。'
      )
      const tools = await this.tools.resolve(chat.id, chat.meta.settings.tools, this.access, null)
      const resolvedOptions = copy(generation)
      delete resolvedOptions.connectionId
      delete resolvedOptions.modelId
      const request = {
        turnId: '',
        mode: chat.mode,
        characters: capabilities.map((character) => ({
          id: character.id,
          formId: character.formId,
          displayName: character.displayName,
          description: character.description,
          performances: {
            motions: character.performances.motions,
            expressions: character.performances.expressions
          }
        })),
        userPersona: chat.userPersona
          ? { displayName: chat.userPersona.displayName, description: chat.userPersona.description }
          : null,
        history: this.history(chat),
        input: input
          ? { kind: 'message', message: this.userProjection(input) }
          : { kind: 'scenario' },
        scenario: copy(chat.meta.settings.scenario),
        options: resolvedOptions,
        connection,
        supplementalContext: [],
        tools
      }

      const inspection = await this.agent.inspectContext(request)
      return { request, speech, generation, capabilities, inspection, resourceOwner }
    } catch (error) {
      await this.assets.releaseWhenPossible(resourceOwner)
      throw error
    }
  }
  /** 在 Chat 串行队列内完成去重、准备和持久受理，再启动执行；调用方负责持有队列。 */
  async admit(
    state,
    input,
    options,
    { replaceId = null, automatic = false, delivery = null } = {}
  ) {
    fields(options, ['requestId', 'generation', 'speech'])
    requireValue(
      typeof options.requestId === 'string' &&
        options.requestId.length > 0 &&
        !['__proto__', 'constructor', 'prototype'].includes(options.requestId)
    )
    const material = fingerprint({
      input: input ? { ...input, id: null } : null,
      options,
      replaceId
    })
    // 独立受理账本不随历史截断消失；否则重试旧 requestId 会重新执行已发生的副作用。
    const receipt = state.chat.meta.extensions.acceptedRequests?.[options.requestId]
    if (receipt) {
      requireValue(receipt.material === material, 'request_conflict')
      return receipt.turnId
    }
    const existing = state.chat.turns.find((turn) => turn.meta.requestId === options.requestId)
    if (existing) {
      requireValue(existing.meta.extensions.requestFingerprint === material, 'request_conflict')
      return existing.id
    }
    this.assertIdle(state)
    const candidate = copy(state.chat)
    if (replaceId) {
      requireValue(candidate.turns.at(-1)?.id === replaceId, 'not_last_turn')
      candidate.turns.pop()
    }
    const prepared = await this.prepare(candidate, input, options)
    let accepted = false
    try {
      const compression = prepared.generation.compression
      if (
        compression.enabled &&
        (prepared.inspection.fit === 'over-budget' ||
          prepared.inspection.estimatedInputTokens > compression.thresholdTokens)
      ) {
        const count = candidate.turns.length - Math.max(1, compression.retainedTurns)
        if (count > 0) {
          const result = await this.agent.compress({
            history: this.history({ ...candidate, turns: candidate.turns.slice(0, count) }),
            options: prepared.generation,
            connection: prepared.request.connection
          })
          const covered = candidate.turns[count - 1]
          covered.messages = covered.messages.filter((message) => message.kind !== 'abstract')
          covered.messages.push({ id: uuid(), kind: 'abstract', text: result.text, extensions: {} })
          prepared.request.history = this.history(candidate)
          prepared.inspection = await this.agent.inspectContext(prepared.request)
        }
      }
      requireValue(
        prepared.inspection.fit !== 'over-budget',
        'context_over_budget',
        '必需上下文超出预算。'
      )
      const turnId = uuid()
      prepared.request.turnId = turnId
      const turn = {
        id: turnId,
        input: input ? { kind: 'message', messageId: input.id } : { kind: 'scenario' },
        meta: {
          requestId: options.requestId,
          generation: prepared.generation,
          speech: prepared.speech,
          scenario: copy(candidate.meta.settings.scenario),
          characters: copy(candidate.meta.settings.characters),
          tools: prepared.request.tools.map((tool) => tool.name),
          worldbook: null,
          worldbookDiagnostics: [],
          extensions: { requestFingerprint: material }
        },
        messages: input ? [copy(input)] : []
      }
      candidate.turns.push(turn)
      candidate.meta.extensions.acceptedRequests ??= {}
      candidate.meta.extensions.acceptedRequests[options.requestId] = {
        material,
        turnId,
        replaceId,
        options: copy(options),
        input: input ? { ...copy(input), id: null } : null
      }
      if (delivery)
        candidate.meta.reminders.find((item) => item.id === delivery).state = 'delivered'
      // 这是受理点：保存成功之前不替换内存 Chat、消费自动输入或启动 Agent。
      const saved = await this.store.save(candidate)
      state.chat = saved
      await this.assets
        .replaceReferences(state.resourceOwner, extractAssetReferences(saved))
        .catch((error) => this.assets.reportMaintenance(error))
      if (!automatic || !state.group) {
        state.group = uuid()
        state.pending = []
        this.interactions?.publish()
      }
      const execution = { turnId, state: 'running', phase: 'generation', problem: null }
      /** @type {TurnRun} */
      const run = {
        turnId,
        group: state.group,
        execution,
        activity: 'generating',
        controller: new AbortController(),
        guides: [],
        speechTasks: [],
        pending: [],
        prepared
      }
      state.executions.set(turnId, execution)
      state.active = run
      state.problem = null
      await this.withScene(state.chat, (scene) =>
        this.performance.setScene(state.sequenceId, scene)
      )
      this.publish(state)
      accepted = true
      run.done = Promise.resolve()
        .then(() => this.execute(state, run))
        .catch((error) => {
          state.problem = problem(error)
          this.publish(state)
        })
      return turnId
    } finally {
      if (!accepted) await this.assets.releaseWhenPossible(prepared.resourceOwner)
    }
  }
  /** 在队列外等待长任务，结果再进入 Chat 队列；否则停止、工具回调会被整轮等待锁住。 */
  async execute(state, run) {
    const signal = run.controller.signal
    try {
      const result = await abortable(
        this.agent.run(
          run.prepared.request,
          {
            onResponse: (update) =>
              this.queue.run(state.chat.id, () => this.acceptResponse(state, run, update)),
            onToolResult: (result) =>
              this.queue.run(state.chat.id, async () => {
                signal.throwIfAborted()
                const turn = state.chat.turns.find((item) => item.id === run.turnId)
                requireValue(
                  turn &&
                    !turn.messages.some(
                      (message) =>
                        message.kind === 'tool-result' && message.callId === result.callId
                    ),
                  'duplicate_tool_result'
                )
                turn.messages.push(copy(result))
                await this.persist(state)
              })
          },
          signal
        ),
        signal
      )
      run.usage = result.usage
      await this.queue.run(state.chat.id, async () => {
        run.activity = 'presenting'
        run.execution.phase = 'speech'
        this.publish(state)
      })
      await Promise.all(run.pending)
      run.execution.phase = 'presentation'
      this.publish(state)
      // 等的是后端计划终态；客户端关闭、卡顿或静音都不参与完成条件。
      await Promise.all(run.guides.map((id) => this.performance.waitForGuideFinish(id)))
    } catch (error) {
      await this.queue.run(state.chat.id, async () => {
        run.execution.state = 'terminated'
        run.execution.problem = problem(error)
        if (state.group === run.group) {
          state.group = null
          state.pending = []
          this.interactions?.publish()
        }
        run.controller.abort()
        await this.performance.discardPendingGuides(run.guides)
        if (run.speechTasks.length) this.speech.cancel(run.speechTasks).catch(() => {})
        this.interruptMissing(state.chat, run.turnId)
        await this.persist(state)
      })
      await Promise.allSettled(run.pending)
      await Promise.allSettled(run.guides.map((id) => this.performance.waitForGuideFinish(id)))
    } finally {
      await this.queue.run(state.chat.id, async () => {
        if (run.execution.state === 'running') run.execution.state = 'finished'
        run.execution.phase = null
        if (state.active === run) state.active = null
        await this.performance.stopTransitionGuide(state.sequenceId)
        this.publish(state)
      })
      await this.assets.releaseWhenPossible(run.prepared.resourceOwner)
      this.scheduleDrain(state)
    }
  }
  /** 接受一份模型响应的增量；先保留响应分组，台词另投影为演出材料。 */
  async acceptResponse(state, run, update) {
    run.controller.signal.throwIfAborted()
    const turn = state.chat.turns.find((item) => item.id === run.turnId)
    requireValue(turn, 'execution_expired')
    let message = turn.messages.find((item) => item.id === update.responseId)
    if (!message) {
      message = {
        id: update.responseId,
        kind: 'assistant',
        parts: [],
        protocol: copy(update.protocol),
        extensions: {}
      }
      turn.messages.push(message)
    }
    for (const source of update.appendParts) {
      requireValue(!message.parts.some((part) => part.id === source.id), 'duplicate_response_part')
      const part = copy(source)
      message.parts.push(part)
      if (part.kind !== 'dialogue') continue
      for (const line of part.lines) {
        line.spoken = {
          text: line.text,
          language: run.prepared.generation.language,
          pronunciationOverrides: []
        }
        line.audio = null
        line.extensions = {}
        const ref = { turnId: turn.id, messageId: message.id, partId: part.id, lineId: line.id }
        const [guideId] = await this.performance.appendGuides(state.sequenceId, [
          this.guide(line, !run.prepared.speech.enabled)
        ])
        run.guides.push(guideId)
        state.guides.set(line.id, { line: ref, guideId })
        if (run.prepared.speech.enabled)
          run.pending.push(this.synthesize(state, ref, run.prepared, run, guideId))
      }
    }
    await this.persist(state)
  }
  /** 演出只接收台词投影；存档身份与轮次生命周期留在 Conversations。 */
  guide(line, ready = true) {
    return {
      slotId: line.speakerId,
      text: line.text,
      translation: line.translation,
      emotion: line.emotion,
      performance: copy(line.performance),
      audio: copy(line.audio),
      ready
    }
  }
  /** 用完整嵌套身份定位台词，避免按展示下标把迟到结果写入另一句。 */
  locate(chat, ref) {
    const turn = chat.turns.find((item) => item.id === ref.turnId)
    return turn?.messages
      .find((item) => item.id === ref.messageId && item.kind === 'assistant')
      ?.parts.find((item) => item.id === ref.partId && item.kind === 'dialogue')
      ?.lines.find((item) => item.id === ref.lineId)
  }
  /** 合成在队列外运行；交付时同时核对句身份、发声材料和合成资格，失败允许无声演出。 */
  async synthesize(state, ref, prepared, run = null, guideId = null) {
    requireValue(!this.closing && !this.closed, 'backend_closed')
    const controller = new AbortController()
    const signal = run
      ? AbortSignal.any([controller.signal, run.controller.signal])
      : controller.signal
    this.synthesisControllers.add(controller)
    const pending = this.assets.using(prepared.capabilities, () =>
      this.synthesizeProtected(state, ref, prepared, run, guideId, signal)
    )
    this.synthesizing.add(pending)
    try {
      return await pending
    } finally {
      this.synthesizing.delete(pending)
      this.synthesisControllers.delete(controller)
    }
  }

  /**
   * 执行单句台词的语音合成，并在合成结果写入对话与演出序列后再释放底层语音任务。
   */
  async synthesizeProtected(state, ref, prepared, run, guideId, signal) {
    const token = uuid()
    state.audioTokens.set(ref.lineId, token)
    const original = copy(this.locate(state.chat, ref))
    let taskId = null
    let audio = null
    let audioProblem = null
    try {
      signal.throwIfAborted()
      const capability = prepared.capabilities.find(
        (item) => item.id === original.speakerId && item.formId === original.formId
      )
      const voice = await this.characters.resolveVoice(capability, original.emotion)
      requireValue(voice.voice, 'voice_unavailable', '当前形态没有可用声音。')
      taskId = await this.speech.submit({
        speed: prepared.speech.speed,
        sentencePauseMs: prepared.speech.sentencePauseMs,
        sampling: prepared.speech.sampling,
        ...original.spoken,
        voice: voice.voice,
        priority: 1
      })
      if (run) {
        run.speechTasks.push(taskId)
        // 停止可能早于 submit 返回；拿到迟到编号后仍须取消真实 Python 任务。
      }
      if (signal.aborted) await this.speech.cancel([taskId])
      const result = await this.speech.waitForResult(taskId, signal)
      if (result.status === 'succeeded') audio = result.audio
      else if (result.status === 'failed') audioProblem = result.problem
    } catch (error) {
      if (signal.aborted && taskId) await this.speech.cancel([taskId]).catch(() => {})
      audioProblem = problem(error)
    }
    try {
      await this.queue.run(state.chat.id, async () => {
        const line = this.locate(state.chat, ref)
        if (
          state.deleted ||
          !line ||
          state.audioTokens.get(ref.lineId) !== token ||
          fingerprint(line.spoken) !== fingerprint(original.spoken) ||
          signal.aborted
        )
          return
        if (audio) await this.assets.addReferences(state.resourceOwner, [audio.asset])
        line.audio = audio
        if (audioProblem) state.audioProblems.push({ line: ref, problem: audioProblem })
        if (guideId) await this.performance.resolveGuide(guideId, { audio })
        await this.persist(state)
      })
    } finally {
      if (taskId) await this.speech.releaseTask?.(taskId)
    }
  }
  /** 为缺结果的调用补记中断；不推断外部副作用未发生，也不自动重试工具。 */
  interruptMissing(chat, onlyTurn = null) {
    let changed = false
    for (const turn of chat.turns) {
      if (onlyTurn && turn.id !== onlyTurn) continue
      const resolved = new Set(
        turn.messages
          .filter((message) => message.kind === 'tool-result')
          .map((message) => message.callId)
      )
      for (const message of [...turn.messages])
        if (message.kind === 'assistant')
          for (const part of message.parts) {
            if (part.kind !== 'tool-call' || resolved.has(part.callId)) continue
            turn.messages.push({
              id: uuid(),
              kind: 'tool-result',
              assistantMessageId: message.id,
              callId: part.callId,
              origin: 'runner',
              outcome: {
                status: 'interrupted',
                problem: {
                  code: 'execution_interrupted',
                  message: '执行已中断，外部副作用可能已经发生。',
                  retryable: false
                }
              },
              extensions: {}
            })
            resolved.add(part.callId)
            changed = true
          }
    }
    return changed
  }
  /** 用副本提交历史或设置修改；只有语义变更才使旧 async 组与音频资格失效。 */
  async update(chatId, mutation, semantic = false) {
    return this.queue.run(chatId, async () => {
      const state = await this.state(chatId)
      if (semantic) this.assertIdle(state, true)
      else requireValue(state.persistence.state !== 'failed', 'save_failed')
      const candidate = copy(state.chat)
      await mutation(candidate)
      const saved = await this.store.save(candidate)
      state.chat = saved
      await this.assets
        .replaceReferences(state.resourceOwner, extractAssetReferences(saved))
        .catch((error) => this.assets.reportMaintenance(error))
      if (semantic) {
        state.group = null
        state.pending = []
        state.audioTokens.clear()
        this.interactions?.publish()
        await this.performance.removeGuides([...state.guides.values()].map((item) => item.guideId))
        state.guides.clear()
      }
      this.publish(state)
    })
  }
  /** 保存对话级覆盖值；运行中的 Turn 继续使用受理时固定的材料。 */
  async setting(chatId, key, value) {
    return this.update(chatId, (chat) => {
      chat.meta.settings[key] = copy(value)
    })
  }
  /** 在当前执行释放门禁后批量消费 async；自动轮受理失败时保留队列，不丢失用户答案。 */
  scheduleDrain(state) {
    if (this.closed || this.closing) return
    setTimeout(
      () =>
        this.queue
          .run(state.chat.id, async () => {
            if (
              !state.pending.length ||
              !state.group ||
              state.active ||
              state.persistence.state === 'failed' ||
              state.compressing
            )
              return
            const pending = copy(state.pending)
            const input = {
              id: uuid(),
              kind: 'user',
              source: { kind: 'async' },
              results: pending,
              extensions: {}
            }
            await this.admit(
              state,
              input,
              { requestId: 'async:' + fingerprint(pending.map((item) => item.requestId)) },
              { automatic: true }
            )
            state.pending = state.pending.filter(
              (item) => !pending.some((consumed) => consumed.requestId === item.requestId)
            )
          })
          .catch((error) => {
            state.problem = problem(error)
            this.publish(state)
          }),
      0
    ).unref?.()
  }
  /**
   * 查询最近完整发布的快照；浏览器重连以此恢复。
   * @param {string} chatId 对话身份。
   * @returns {Promise<import('../../shared/contracts/conversation.js').ConversationSnapshot>} 独立快照，不暴露正在修改的内部对象。
   */
  async getSnapshot(chatId) {
    const state = await this.state(chatId)
    return copy(state.published ?? this.snapshot(state))
  }
  /**
   * 核对实际运行中的工具调用，建立交互来源资格。
   * @param {import('../../shared/contracts/tools.js').InteractionRequest} request 工具内部登记的请求。
   * @returns {Promise<InteractionRegistration | null>} 后端查得的来源；失效时为 null，不接受页面自报 Chat 或组。
   */
  async qualifyInteraction(request) {
    for (const state of this.states.values()) {
      const run = state.active
      if (
        !run ||
        run.turnId !== request.source.turnId ||
        run.controller.signal.aborted ||
        run.group !== state.group
      )
        continue
      const turn = state.chat.turns.find((item) => item.id === run.turnId)
      const message = turn.messages.find(
        (item) => item.id === request.source.assistantMessageId && item.kind === 'assistant'
      )
      const call = message?.parts.find(
        (part) => part.kind === 'tool-call' && part.callId === request.source.callId
      )
      if (call)
        return {
          chatId: state.chat.id,
          group: run.group,
          toolName: call.toolName,
          source: copy(request.source)
        }
    }
    return null
  }
  /** 资格跟随组而非窗口存活；新手动轮或语义修改后，旧答案不能进入新上下文。 */
  validInteraction(registration) {
    return !this.closed && this.states.get(registration.chatId)?.group === registration.group
  }
  /** 在所属 Chat 队列内去重并接收交互结果；失败或取消只结算，不制造自动回复。 */
  async queueInteraction(completion, registration, request) {
    return this.queue.run(registration.chatId, async () => {
      const state = await this.state(registration.chatId)
      requireValue(this.validInteraction(registration), 'interaction_expired')
      if (state.acceptedResults.has(completion.requestId)) return
      if (completion.status === 'succeeded')
        state.pending.push({
          requestId: completion.requestId,
          sourceTurnId: registration.source.turnId,
          callId: registration.source.callId,
          toolName: registration.toolName,
          status: 'succeeded',
          request: copy(request.payload),
          result: copy(completion.value)
        })
      state.acceptedResults.add(completion.requestId)
      this.scheduleDrain(state)
    })
  }
  /**
   * 供已绑定 Chat 的工具创建提醒，同一 Turn/call 重试复用结果。
   * @param {string} chatId 工具装配固定的对话。
   * @param {{dueAt: string, instruction: string}} options 到期时间和提示内容。
   * @param {import('../../shared/contracts/tools.js').ToolExecutionContext} source 当前工具调用身份。
   * @returns {Promise<import('../../shared/contracts/conversation.js').Reminder>} 已持久化的提醒；保存失败明确拒绝。
   */
  async createReminder(chatId, options, source) {
    return this.queue.run(chatId, async () => {
      const state = await this.state(chatId)
      requireValue(
        state.active?.turnId === source.turnId && !state.active.controller.signal.aborted,
        'execution_expired'
      )
      requireValue(
        Number.isFinite(Date.parse(options.dueAt)) &&
          typeof options.instruction === 'string' &&
          options.instruction.trim()
      )
      const key = source.turnId + ':' + source.callId
      const existing = state.chat.meta.extensions.reminderCalls?.[key]
      if (existing) return copy(state.chat.meta.reminders.find((item) => item.id === existing))
      const reminder = {
        id: uuid(),
        dueAt: new Date(options.dueAt).toISOString(),
        instruction: options.instruction,
        state: 'enabled'
      }
      state.chat.meta.reminders.push(reminder)
      state.chat.meta.extensions.reminderCalls ??= {}
      state.chat.meta.extensions.reminderCalls[key] = reminder.id
      requireValue(await this.persist(state), 'save_failed', '提醒尚未可靠保存，请处理存档故障。')
      return copy(reminder)
    })
  }
  /**
   * 停止进程前取消任务并结算，不在重启后重放。
   * @returns {Promise<void>} 运行任务已收尾，观察者已释放。
   */
  async close() {
    if (this.closed) return
    this.closing = true
    for (const controller of this.synthesisControllers) controller.abort()
    for (const state of this.states.values())
      if (state.active) await this.stopTurn(state.chat.id, state.active.turnId)
    for (const state of this.states.values())
      await this.performance.removeGuides([...state.guides.values()].map((item) => item.guideId))
    await Promise.allSettled([...this.states.values()].map((state) => state.active?.done))
    await Promise.allSettled([...this.synthesizing])
    for (const state of this.states.values())
      await this.assets.releaseWhenPossible(state.resourceOwner)
    this.closed = true
    this.bus.clear()
  }

  /**
   * 列出已保存对话的摘要，供界面展示和选择对话。
   * @returns {Promise<import('../../shared/contracts/conversation.js').ChatSummary[]>} 按持久索引排序的入口，不加载全文。
   */
  async listChats() {
    return this.store.list()
  }

  /**
   * 创建并保存新的空白 Chat。
   * revision 从 0 提交为 1；身份解析失败拒绝，不伪造人格。
   * @param {CreateChatOptions} options 创建材料。
   * @returns {Promise<string>} 已保存的 ChatId。
   */
  async createChat(options) {
    requireValue(
      Array.isArray(options.characterIds) &&
        options.characterIds.length > 0 &&
        new Set(options.characterIds).size === options.characterIds.length
    )
    requireValue(['single-character', 'scripted-dialogue'].includes(options.mode))
    requireValue(
      options.mode === 'single-character'
        ? options.characterIds.length === 1
        : options.characterIds.length === 2
    )
    return this.assets.using(options, async (owner) => {
      const chat = {
        id: uuid(),
        revision: 0,
        title: options.title,
        characterIds: copy(options.characterIds),
        mode: options.mode,
        userPersona: options.userPersonaId
          ? await this.characters.acquirePersona(owner, options.userPersonaId)
          : null,
        meta: { settings: merge(chatSettings(), options.settings), reminders: [], extensions: {} },
        turns: []
      }
      generationOptions(chat.meta.settings.generation)
      speechOptions(chat.meta.settings.speech)
      presentationOptions(chat.meta.settings.presentation, chat.characterIds)
      await this.capabilities(chat, owner)
      await this.store.save(chat)
      return chat.id
    })
  }

  /**
   * 手动发送一条信息。
   * 本接口创建的新轮次来源都是 manual，表示用户自己发送了新消息。自动补聊（工具创建的新轮次）会通过其他接口创建。
   * 相同 requestId/材料重试返回同一 Turn，即使此时忙碌也不重复执行；同 ID 改材料拒绝。
   * 首次受理后开新组、作废旧 async；不控制任何客户端的回放。
   * 发起客户端确认受理后自行退出本地回放；其他客户端仅提示新内容，校验/保存失败不使旧组失效。
   * 工具交互答案不得通过此入口提交；停止后的保留句尚未逻辑结束时，新的请求仍须遵守受理门禁。
   * @param {string} chatId 目标对话。
   * @param {UserSubmission} submission 用户发送的内容。
   * @param {TurnOptions} options 本轮次修改的覆盖选项。
   * @returns {Promise<string>} 输入已保存且生成已受理的 TurnId，不等待演出。
   */
  async createTurn(chatId, submission, options) {
    this.validateSubmission(submission)
    return this.queue.run(chatId, async () =>
      this.admit(
        await this.state(chatId),
        {
          id: uuid(),
          kind: 'user',
          source: { kind: 'manual' },
          content: copy(submission),
          extensions: {}
        },
        options
      )
    )
  }

  /**
   * 按当前情景开始生成小剧场对话。此接口应当只在模式为 scripted-dialogue 时使用。
   * 情景输入记为 Turn.input.kind=scenario，受理与开组规则同手动发起。
   * 小剧场理论上不支持接续生成。不过，后续只重生成的产品限制由前端承担，后端不按模式永久禁止连续输入。
   * @param {string} chatId 目标对话。
   * @param {TurnOptions} options 稳定请求身份与本次覆盖。
   * @returns {Promise<string>} 新 TurnId。
   */
  async generateScenario(chatId, options) {
    return this.queue.run(chatId, async () => {
      const state = await this.state(chatId)
      requireValue(
        state.chat.mode === 'scripted-dialogue' &&
          state.chat.meta.settings.scenario?.situation?.trim(),
        'scenario_required'
      )
      return this.admit(state, null, options)
    })
  }

  /**
   * 停止指定轮次的生成及后续自动回复，保留已经接受的历史内容和后端计划时间线的当前句。
   * 先使运行 Turn terminated、作废所属组的全部未消费及尚未返回 async，再尽力取消任务。
   * 保留已接受内容并全文展示；原子移除后续演出，保留当前句。底层推理实际退出前仍占调度资源。
   * 保留句逻辑结束前 admission.canGenerate 为 false；无当前句时不增加等待，不等待客户端实际播放或回报。
   * 显式删除或替换若已移除保留句，则该句不再阻止受理；其他保存/压缩等门禁仍独立生效。
   * 缺少结果的调用以原 callId 结算通用中断错误，不推断副作用回滚；accepted 不补第二份结果。
   * 已 finished 的轮可使其组失效但不改为 running；重复停止幂等，旧组命令不影响新组。
   * @param {string} chatId 目标对话。
   * @param {string} turnId 明确目标身份。
   * @returns {Promise<void>} 停止已生效，不表示保留句已逻辑结束或下一轮已可受理。
   */
  async stopTurn(chatId, turnId) {
    return this.queue.run(chatId, async () => {
      const state = await this.state(chatId)
      const run = state.active
      if (!run || run.turnId !== turnId) {
        if (state.chat.turns.at(-1)?.id === turnId) {
          state.group = null
          state.pending = []
          this.interactions?.publish()
        }
        return
      }
      run.execution.state = 'terminated'
      run.controller.abort()
      if (state.group === run.group) {
        state.group = null
        state.pending = []
        this.interactions?.publish()
      }
      await this.performance.discardPendingGuides(run.guides)
      if (run.speechTasks.length) this.speech.cancel(run.speechTasks).catch(() => {})
      this.interruptMissing(state.chat, turnId)
      await this.persist(state)
    })
  }

  /**
   * 读取单句或整轮已保存的台词与资源，供客户端独立回放。
   * 整轮按记录顺序跳过 reasoning/工具/摘要；无已保存音频时无声放行，不生成、不补合成、不改历史。
   * 不创建或停止后端 sequence，不维护客户端回放状态；客户端的 silent 和音量不作为后端参数。
   * @param {string} chatId 来源对话。
   * @param {{ kind: 'line', line: LineRef } | { kind: 'turn', turnId: string }} target 单句使用稳定 LineRef，整轮使用来源 TurnId。
   * @returns {Promise<import('../../shared/contracts/presentation.js').ReplayMaterials>} 独立只读材料，全部 guide 已 ready，不表示任何客户端已开始播放。
   */
  async getReplayMaterials(chatId, target) {
    const state = await this.state(chatId)
    let lines
    if (target.kind === 'line') {
      const line = this.locate(state.chat, target.line)
      requireValue(line, 'line_missing')
      lines = [line]
    } else {
      requireValue(target.kind === 'turn')
      const turn = state.chat.turns.find((item) => item.id === target.turnId)
      requireValue(turn, 'turn_missing')
      lines = turn.messages
        .filter((message) => message.kind === 'assistant')
        .flatMap((message) => message.parts)
        .filter((part) => part.kind === 'dialogue')
        .flatMap((part) => part.lines)
    }
    return {
      scene: await this.scene(state.chat, false),
      guides: lines.map((line) => this.guide(line))
    }
  }

  /**
   * 保留最后一轮的输入，按当前设置重新生成并替换该轮回复。
   * 仅替换最后一个 Turn；保留原输入/附件/内部输入或情景命令，保留角色、模式和固定人格。
   * 读取当前配置及本次覆盖，不隐式复用旧临时 options；新 UUID、新组，旧组保持失效。
   * 先校验目标和材料；统一协调旧任务失效、记录替换及持久化。受理保存失败保留旧轮和资源。
   * 已受理后生成失败保留新输入；不撤销旧提醒、文件等副作用，新执行允许产生新副作用。
   * @param {string} chatId 目标对话。
   * @param {string} turnId 受理时仍须是最后一轮。
   * @param {TurnOptions} options 本次独立操作身份与覆盖。
   * @returns {Promise<string>} 新 TurnId。
   */
  async recreateTurn(chatId, turnId, options) {
    return this.queue.run(chatId, async () => {
      const state = await this.state(chatId)
      const receipt = state.chat.meta.extensions.acceptedRequests?.[options.requestId]
      if (receipt) {
        requireValue(
          receipt.replaceId === turnId && fingerprint(receipt.options) === fingerprint(options),
          'request_conflict'
        )
        return receipt.turnId
      }
      const turn = state.chat.turns.find((item) => item.id === turnId)
      requireValue(turn, 'turn_missing')
      const input =
        turn.input.kind === 'message'
          ? copy(turn.messages.find((message) => message.id === turn.input.messageId))
          : null
      if (input) input.id = uuid()
      return this.admit(state, input, options, { replaceId: turnId })
    })
  }

  /**
   * 编辑最后一轮的手动用户输入，并按当前设置重新生成回复。
   * 原输入必须为 manual；提醒、async 结果或情景命令不能通过此入口编辑。
   * 内部一次原子受理完成编辑和替换，不要求调用方先删除再创建。
   * 新输入校验或受理保存失败不丢旧轮，共用附件先建立新引用再释放旧引用；其他语义同 recreateTurn。
   * @param {string} chatId 目标对话。
   * @param {string} turnId 最后一轮身份。
   * @param {UserSubmission} submission 编辑后的手动内容。
   * @param {TurnOptions} options 操作身份及本次覆盖。
   * @returns {Promise<string>} 新 TurnId/新组。
   */
  async editAndRecreateTurn(chatId, turnId, submission, options) {
    this.validateSubmission(submission)
    return this.queue.run(chatId, async () => {
      const state = await this.state(chatId)
      const receipt = state.chat.meta.extensions.acceptedRequests?.[options.requestId]
      if (receipt) {
        requireValue(
          receipt.replaceId === turnId &&
            fingerprint(receipt.options) === fingerprint(options) &&
            fingerprint(receipt.input?.content) === fingerprint(submission),
          'request_conflict'
        )
        return receipt.turnId
      }
      const turn = state.chat.turns.find((item) => item.id === turnId)
      requireValue(
        turn?.messages.find((message) => message.id === turn.input.messageId)?.source?.kind ===
          'manual',
        'not_manual_input'
      )
      return this.admit(
        state,
        {
          id: uuid(),
          kind: 'user',
          source: { kind: 'manual' },
          content: copy(submission),
          extensions: {}
        },
        options,
        { replaceId: turnId }
      )
    })
  }

  /**
   * 原地编辑 manual 用户内容或台词/翻译，保留后文且不生成。不可以编辑内部输入、reasoning、工具和摘要。
   * 移除依赖被改内容的后方摘要，作废全部未消费 async。正文变化同步更新发声文本并使旧音频失效；
   * 仅翻译变化不使无关音频失效。编辑/删除导致目标内容失效时，旧音频任务不得覆盖当前记录。
   * @param {string} chatId 目标对话。
   * @param {HistoryEdit} edit 精确定位与完整可编辑字段。
   * @returns {Promise<void>} 编辑已保存。
   */
  async editHistory(chatId, edit) {
    return this.update(
      chatId,
      (chat) => {
        let index
        if (edit.kind === 'user') {
          this.validateSubmission(edit.content)
          index = chat.turns.findIndex((turn) => turn.id === edit.turnId)
          const message = chat.turns[index]?.messages.find((item) => item.id === edit.messageId)
          requireValue(message?.source?.kind === 'manual', 'not_manual_input')
          message.content = copy(edit.content)
        } else {
          requireValue(edit.kind === 'line' && typeof edit.text === 'string')
          const line = this.locate(chat, edit.line)
          requireValue(line, 'line_missing')
          index = chat.turns.findIndex((turn) => turn.id === edit.line.turnId)
          if (line.text !== edit.text) {
            line.text = edit.text
            line.spoken.text = edit.text
            line.audio = null
          }
          line.translation = edit.translation
        }
        for (const turn of chat.turns.slice(index))
          turn.messages = turn.messages.filter((message) => message.kind !== 'abstract')
      },
      true
    )
  }

  /**
   * 删除选中 Turn 及之后全部内容，协调任务、演出、摘要及资源失效。不撤销已完成工具的副作用。
   * @param {string} chatId 目标对话。
   * @param {string} turnId 第一条删除轮次，包含该轮。
   * @returns {Promise<void>} 截断已保存。
   */
  async truncateFromTurn(chatId, turnId) {
    return this.update(
      chatId,
      (chat) => {
        const index = chat.turns.findIndex((turn) => turn.id === turnId)
        requireValue(index >= 0, 'turn_missing')
        chat.turns = chat.turns.slice(0, index)
      },
      true
    )
  }

  /**
   * 回溯到指定 Turn 结束处；null 清空，受理时作废全部未消费 async。
   * @param {string} chatId 目标对话。
   * @param {string | null} throughTurnId 最后保留轮次，包含该轮。
   * @returns {Promise<void>} 回溯已保存。
   */
  async rollback(chatId, throughTurnId) {
    return this.update(
      chatId,
      (chat) => {
        const index =
          throughTurnId === null ? -1 : chat.turns.findIndex((turn) => turn.id === throughTurnId)
        requireValue(throughTurnId === null || index >= 0, 'turn_missing')
        chat.turns = chat.turns.slice(0, index + 1)
      },
      true
    )
  }

  /**
   * 将指定历史位置及之前的内容复制为一个独立的新对话。
   * 复制到 Turn 结束处，沿用固定人格；新 Chat/revision 0，不复制运行资格，提醒默认停用。
   * 复制的提醒分配全局唯一新 ID，并同步改写复制历史中指向这些提醒的引用。
   * @param {string} chatId 来源对话。
   * @param {string | null} throughTurnId null 只复制配置。
   * @returns {Promise<string>} 已保存新 ChatId。
   */
  async forkChat(chatId, throughTurnId) {
    return this.queue.run(chatId, async () => {
      const original = copy((await this.state(chatId)).chat)
      const index =
        throughTurnId === null ? -1 : original.turns.findIndex((turn) => turn.id === throughTurnId)
      requireValue(throughTurnId === null || index >= 0, 'turn_missing')
      original.turns = original.turns.slice(0, index + 1)
      return this.saveCopy(original)
    })
  }

  /**
   * 保留原句、原形态/情绪/发声语言/读音，按当前对应声音资源及语音参数重合成，不自动回放。
   * 不切换到其他形态；只合并当前记录的音频字段。删除、内容编辑或更新任务后旧结果不能覆盖。
   * 不改变 finished 生命周期或 async 资格。
   * @param {string} chatId 来源对话。
   * @param {LineRef} line 目标句。
   * @returns {Promise<void>} 已受理，结果和音频故障经 observe 交付。
   */
  async regenerateAudio(chatId, line) {
    return this.queue.run(chatId, async () => {
      const state = await this.state(chatId)
      const target = this.locate(state.chat, line)
      requireValue(target?.formId, 'line_missing')
      const owner = await this.assets.createTemporaryOwner('speech-preparation')
      try {
        const capability = await this.characters.acquireCapabilities(owner, target.speakerId, {
          formId: target.formId
        })
        const speech = merge((await this.settings.get()).speech, state.chat.meta.settings.speech)
        this.synthesize(state, line, { speech, capabilities: [capability] })
          .catch((error) => {
            state.problem = problem(error)
            this.publish(state)
          })
          .finally(() => this.assets.releaseWhenPossible(owner))
      } catch (error) {
        await this.assets.releaseWhenPossible(owner)
        throw error
      }
    })
  }

  /**
   * 查询实际请求准备的占用，不压缩、不上传附件、不调用工具，不修改历史。
   * @param {string} chatId 目标对话。
   * @param {UserSubmission | null} submission 可选草稿预览；null 检查当前历史及配置。
   * @param {import('../../shared/contracts/settings.js').GenerationOptions} options 本次预览覆盖。
   * @returns {Promise<import('../llm/interface.js').ContextInspection>} 估计/未知/实际 usage 及来源。
   */
  async inspectContext(chatId, submission, options) {
    const state = await this.state(chatId)
    if (submission) this.validateSubmission(submission)
    const input = submission
      ? { id: uuid(), kind: 'user', source: { kind: 'manual' }, content: copy(submission) }
      : null
    const prepared = await this.prepare(copy(state.chat), input, { generation: options })
    if (!submission) prepared.request.input = null
    try {
      return await this.agent.inspectContext(prepared.request)
    } finally {
      await this.assets.releaseWhenPossible(prepared.resourceOwner)
    }
  }

  /**
   * 手动触发上下文压缩。仅压缩已结束完整轮次，至少保留最后一轮。
   * 压缩过程和生成/历史修改互斥，无法同时进行，但允许历史回放。
   * 累计摘要插入覆盖末轮末尾；失败保留旧有效摘要和原文，不制造回复，不恢复失效 async。
   * @param {string} chatId 目标对话。
   * @returns {Promise<void>} 压缩与保存完成；不可压缩时明确报告。
   */
  async compressHistory(chatId) {
    const state = await this.state(chatId)
    let request
    let throughTurnId
    await this.queue.run(chatId, async () => {
      this.assertIdle(state)
      const generation = merge(
        (await this.settings.get()).generation,
        state.chat.meta.settings.generation
      )
      const count = state.chat.turns.length - Math.max(1, generation.compression.retainedTurns)
      requireValue(count > 0, 'nothing_to_compress')
      throughTurnId = state.chat.turns[count - 1].id
      request = {
        history: this.history({ ...state.chat, turns: state.chat.turns.slice(0, count) }),
        options: generation,
        connection: await this.settings.resolveConnection(generation)
      }
      state.compressing = true
      this.publish(state)
    })
    try {
      const result = await this.agent.compress(request)
      await this.queue.run(chatId, async () => {
        const turn = state.chat.turns.find((item) => item.id === throughTurnId)
        turn.messages = turn.messages.filter((message) => message.kind !== 'abstract')
        turn.messages.push({ id: uuid(), kind: 'abstract', text: result.text, extensions: {} })
        await this.persist(state)
      })
    } finally {
      await this.queue.run(chatId, async () => {
        state.compressing = false
        this.publish(state)
      })
      this.scheduleDrain(state)
    }
  }

  /**
   * 重试保存当前内存修改，按保存顺序合并已运行任务结果。
   * 版本冲突不能修改版本号强存，不重新执行模型/工具/合成。
   * @param {string} chatId 保存失败的对话。
   * @returns {Promise<void>} 当前待保存内容已落盘。
   */
  async retrySave(chatId) {
    return this.queue.run(chatId, async () => {
      const state = await this.state(chatId)
      requireValue(await this.persist(state, true), 'save_failed')
      this.scheduleDrain(state)
    })
  }

  /**
   * 修改对话的显示标题。
   * @param {string} chatId 目标。
   * @param {string} title 新标题。
   * @returns {Promise<void>} 已保存，不改历史语义。
   */
  async renameChat(chatId, title) {
    requireValue(typeof title === 'string')
    return this.update(chatId, (chat) => {
      chat.title = title
    })
  }

  /**
   * 保存对话列表的完整显示顺序。
   * @param {string[]} chatIds 完整有序 ID，原子校验无遗漏/重复。
   * @returns {Promise<void>} 仅保存索引，不重写全部 Chat。
   */
  async setChatOrder(chatIds) {
    return this.store.setOrder(chatIds)
  }

  /**
   * 设置或清除后续小剧场生成使用的情境及各角色互动设定。
   * @param {string} chatId 目标。
   * @param {import('../../shared/contracts/settings.js').ScenarioSettings | null} options 情景。
   * @returns {Promise<void>} 后续轮次使用。
   */
  async setScenario(chatId, options) {
    return this.setting(chatId, 'scenario', options)
  }

  /**
   * 设置该对话后续生成可使用的普通工具集合。
   * @param {string} chatId 目标。
   * @param {import('../../shared/contracts/settings.js').ToolSelection} options 普通工具，校验依赖。
   * @returns {Promise<void>} 已保存，不改本轮工具集合。
   */
  async setTools(chatId, options) {
    await this.tools.resolve(chatId, options, this.access, null)
    return this.setting(chatId, 'tools', options)
  }

  /**
   * 设置该对话后续生成使用的世界书范围、剧情进度及相关开关。
   * @param {string} chatId 目标。
   * @param {import('../../shared/contracts/worldbook.js').WorldbookInfo} options 知识范围。
   * @returns {Promise<void>} 后续使用，不清除历史已泄露的知识。
   */
  async setWorldbookInfo(chatId, options) {
    return this.setting(chatId, 'worldbook', options)
  }

  /**
   * 更改角色的形态和 live2d 服装选择。
   * 换装不改形态，形态选择不改目录默认；当前 Agent 约定不变，下轮读取新目录。
   * 演出更新不重播音频；V2 按情绪动作组、V3 按实际模型逐通道降级。
   * @param {string} chatId 目标。
   * @param {string} characterId 参与角色。
   * @param {import('../../shared/contracts/settings.js').CharacterSelection} options 当前形态及各形态模型覆盖。
   * @returns {Promise<void>} 已校验保存并协调场景更新。
   */
  async setCharacterSelection(chatId, characterId, options) {
    await this.update(chatId, async (chat) => {
      requireValue(chat.characterIds.includes(characterId))
      await this.characters.resolveCapabilities(characterId, {
        formId: options.formId,
        modelId: options.modelOverrides?.[options.formId]
      })
      chat.meta.settings.characters[characterId] = copy(options)
    })
    const state = await this.state(chatId)
    await this.withScene(state.chat, (scene) => this.performance.setScene(state.sequenceId, scene))
  }

  /**
   * 修改对话的生成设置。这些设置需要等到下轮对话才能生效。
   * @param {string} chatId 目标。
   * @param {import('../../shared/contracts/settings.js').GenerationOptions} options 完整覆盖集合，空对象恢复继承。
   * @returns {Promise<void>} 后续执行使用。
   */
  async setGenerationOptions(chatId, options) {
    generationOptions(options)
    return this.setting(chatId, 'generation', options)
  }

  /**
   * 设置对话的语音合成相关选项。需要等到下轮对话才生效。
   * @param {string} chatId 目标。
   * @param {import('../../shared/contracts/settings.js').SpeechOptions} options 完整覆盖集合。
   * @returns {Promise<void>} 自动语音开关下轮生效，不改当前轮后续台词。
   */
  async setSpeechOptions(chatId, options) {
    speechOptions(options)
    return this.setting(chatId, 'speech', options)
  }

  /**
   * 更新当前的 live2d 场景，包含背景图片、角色 live2d 模型缩放和平移数据。
   * @param {string} chatId 目标。
   * @param {import('../../shared/contracts/settings.js').ChatPresentationSettings} options 背景/布局/朝向。
   * @returns {Promise<void>} 保存并更新场景，不失效 async。
   */
  async setPresentation(chatId, options) {
    presentationOptions(options, (await this.state(chatId)).chat.characterIds)
    await this.setting(chatId, 'presentation', options)
    const state = await this.state(chatId)
    await this.withScene(state.chat, (scene) => this.performance.setScene(state.sequenceId, scene))
  }

  /**
   * 列出全程序的提醒事项，也可只查询指定对话的提醒。
   * @param {{ chatId?: string }} [options] 省略 chatId 时查询所有对话。
   * @returns {Promise<import('../../shared/contracts/conversation.js').ReminderEntry[]>} 携带所属 chatId，包含 disabled/delivered/expired 的记录。
   */
  async listReminders(options) {
    const ids = options?.chatId ? [options.chatId] : (await this.listChats()).map((chat) => chat.id)
    const result = []
    for (const id of ids)
      result.push(
        ...(await this.state(id)).chat.meta.reminders.map((reminder) => ({
          ...copy(reminder),
          chatId: id
        }))
      )
    return result
  }

  /**
   * 按全局唯一 ID 查看提醒详情及其所属对话。
   * @param {string} reminderId 全局唯一提醒身份。
   * @returns {Promise<import('../../shared/contracts/conversation.js').ReminderEntry | null>} 记录或不存在。
   */
  async getReminder(reminderId) {
    return (await this.listReminders()).find((reminder) => reminder.id === reminderId) ?? null
  }

  /**
   * 按全局唯一 ID 删除提醒，阻止它之后被投递。
   * 由本模块定位所属 Chat，与该 Chat 的提醒投递及保存串行协调；不存在时无副作用。
   * @param {string} reminderId 全局唯一提醒身份。
   * @returns {Promise<void>} 已删除；不终止已经受理的提醒 Turn。
   */
  async removeReminder(reminderId) {
    const entry = await this.getReminder(reminderId)
    if (entry)
      await this.update(entry.chatId, (chat) => {
        chat.meta.reminders = chat.meta.reminders.filter((item) => item.id !== reminderId)
      })
  }

  /**
   * 接收 async 工具发起的宿主交互的最终结果，让角色在当前轮结束后根据有效结果继续回复。
   * 前端交互工具统一 async；成功结果作为后续轮次的内部输入，不回写为原工具调用的第二份结果，也不继续原 Loop。
   * 内部入口，仅给装配绑定的交互协调者，页面不能自报来源/组。登记成功不表示立即创建 Turn。
   * 校验请求身份和资格，重复结果幂等；running 全阶段只排队，空闲且允许受理才批量消费当时有效成功结果。
   * 同批用一条 UserMessage、新 Turn，后到结果留下一批，不等待其他交互。受理失败不消费队列。
   * failed/cancelled 只结算该请求，不触发回复；新手动/停止/历史修改与消费串行，重启旧请求拒收。
   * @param {import('../../shared/contracts/tools.js').InteractionCompletion} completion 已登记请求的最终结果。
   * @returns {Promise<'queued' | 'settled' | 'duplicate' | 'stale'>} 登记结果，不暴露组内部状态。
   */
  async acceptInteractionResult(completion) {
    return this.interactions.complete(completion).then(() => 'settled')
  }

  /**
   * 尝试投递一条到期提醒；符合投递条件且对话允许受理时，以提醒内容创建新的 Turn。
   * 内部入口，仅提醒扫描器可调用。绑定所属 Chat、到期/12 小时补发及稳定投递 ID，忙碌延后。
   * 持久化输入和标记 delivered 必须协调去重；不消费草稿，无有效组时开启新组。
   * @param {string} chatId 所属对话。
   * @param {string} reminderId 已保存提醒身份。
   * @param {string} deliveryId 稳定投递身份。
   * @returns {Promise<string | null>} 已受理 TurnId，忙碌或过期时 null；状态经提醒查询反映。
   */
  async deliverReminder(chatId, reminderId, deliveryId) {
    return this.queue.run(chatId, async () => {
      const state = await this.state(chatId)
      const reminder = state.chat.meta.reminders.find((item) => item.id === reminderId)
      if (!reminder || reminder.state !== 'enabled' || Date.parse(reminder.dueAt) > Date.now())
        return null
      if (Date.now() - Date.parse(reminder.dueAt) > 12 * 60 * 60 * 1000) {
        reminder.state = 'expired'
        await this.persist(state)
        return null
      }
      if (state.active || state.compressing || state.persistence.state === 'failed') return null
      return this.admit(
        state,
        {
          id: uuid(),
          kind: 'user',
          source: { kind: 'reminder', reminderId, deliveryId },
          text: reminder.instruction,
          extensions: {}
        },
        { requestId: 'reminder:' + deliveryId },
        { automatic: true, delivery: reminderId }
      )
    })
  }

  /**
   * 删除选中的对话。
   * 正常 running 阶段遵守历史修改互斥，拒绝删除；受理后再协调旧任务与资源清理。
   * @param {string} chatId 目标。
   * @returns {Promise<void>} 停止任务并删除存档和索引，仅按引用回收资源。
   */
  async deleteChat(chatId) {
    return this.queue.run(chatId, async () => {
      const state = await this.state(chatId)
      this.assertIdle(state)
      await this.store.delete(chatId, state.chat.revision)
      state.deleted = true
      state.audioTokens.clear()
      state.group = null
      state.pending = []
      await this.performance.closeSequence(state.sequenceId)
      this.states.delete(chatId)
      this.interactions?.publish()
      await this.assets.releaseWhenPossible(state.resourceOwner)
    })
  }

  /**
   * 导出选定的一个或者多个对话。
   * @param {string[]} chatIds 目标。
   * @returns {Promise<import('../../shared/contracts/common.js').AssetRef>} 已保存内容及必要资源的归档。
   */
  async exportChats(chatIds) {
    return this.store.exportArchive(chatIds)
  }

  /**
   * 从外部备份导入其中的所有对话。
   * 导入分配新身份/revision 0，修正内部引用、停用复制提醒；不恢复组/任务，也不重新解析固定人格。
   * 复制的提醒分配全局唯一新 ID，并同步改写导入历史中指向这些提醒的引用，重复导入不复用旧 ID。
   * 缺图/音频/角色保留引用及可读历史，生成前另查可用性。
   * @param {import('../../shared/contracts/common.js').AssetRef} archive 归档。
   * @returns {Promise<import('../../shared/contracts/archive.js').ChatImportResult>} 新身份及结构化警告。
   */
  async importChats(archive) {
    const drafts = await this.store.readArchive(archive)
    const chatIds = []
    try {
      for (const chat of drafts.chats) chatIds.push(await this.saveCopy(chat))
      return { chatIds, warnings: drafts.warnings }
    } finally {
      await this.assets.releaseWhenPossible(drafts.resourceOwner)
    }
  }

  /**
   * 订阅某个 chatId 的事件。
   * 先当前快照后按运行版本发布，取消订阅不停止任务；观察回调不能阻塞推进。
   * @param {string} chatId 目标。
   * @param {(snapshot: import('../../shared/contracts/conversation.js').ConversationSnapshot) => void} listener 本地回调。
   * @returns {import('../../shared/contracts/common.js').Unsubscribe} 取消观察。
   */
  async observe(chatId, listener) {
    const state = await this.state(chatId)
    return this.bus.subscribe(chatId, listener, state.published ?? this.snapshot(state))
  }
  /** 分支与导入统一分配新身份，重写内部引用并停用复制的提醒，避免重复触发旧副作用。 */
  async saveCopy(original) {
    const replacements = new Map(original.meta.reminders.map((reminder) => [reminder.id, uuid()]))
    function remap(value) {
      if (typeof value === 'string') return replacements.get(value) ?? value
      if (Array.isArray(value)) return value.map(remap)
      if (value && typeof value === 'object')
        return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, remap(item)]))
      return value
    }
    const chat = remap(copy(original))
    chat.id = uuid()
    chat.revision = 0
    chat.meta.reminders.forEach((reminder) => {
      reminder.state = 'disabled'
    })
    delete chat.meta.extensions.acceptedRequests
    delete chat.meta.extensions.reminderCalls
    for (const turn of chat.turns) {
      turn.meta.requestId = null
      delete turn.meta.extensions.requestFingerprint
    }
    this.interruptMissing(chat)
    await this.store.save(chat)
    return chat.id
  }
}
