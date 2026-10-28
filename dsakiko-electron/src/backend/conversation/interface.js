import { characterContext } from '../llm/context.js'
import { contextBudget, trimHistory } from '../llm/context-budget.js'
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
import { randomInt } from 'node:crypto'

/** @typedef {import('../../shared/contracts/conversation.js').UserSubmission} UserSubmission */
/** @typedef {import('../../shared/contracts/conversation.js').LineRef} LineRef */
/** @typedef {import('../../shared/contracts/settings.js').TurnOptions} TurnOptions */

/**
 * @typedef {object} CreateChatOptions
 * @property {string} title 对话标题。
 * @property {string[]} characterIds 参与对话的角色 ID 列表。对话创建后不能再增删参与角色。
 * @property {import('../../shared/contracts/conversation.js').DialogueMode} mode 对话模式（单人对话或双人小剧场）。
 * @property {import('../../shared/contracts/settings.js').ChatSettings} settings 对话级覆盖设置。
 * @property {string | null} [userPersonaId] 用户自定义人格 ID；省略或传 null 表示不使用自定义人格。
 */

/**
 * @typedef {{ kind: 'user', turnId: string, messageId: string, content: UserSubmission }
 *   | { kind: 'line', line: LineRef, text: string, translation: string | null }} HistoryEdit
 */

/**
 * @typedef {object} PreparedTurn
 * @property {import('../llm/interface.js').GenerationRequest} request 已组装好的模型生成请求，在轮次正式受理时补齐 turnId。
 * @property {import('../../shared/contracts/settings.js').GenerationOptions} generation 本轮生效的生成参数。
 * @property {import('../../shared/contracts/settings.js').SpeechOptions} speech 本轮生效的语音合成参数。
 * @property {import('../../shared/contracts/characters.js').CharacterCapabilities[]} capabilities 本轮参与角色的能力快照（包含声音与形态信息），不随角色目录后续修改而改变。
 * @property {import('../llm/interface.js').ContextInspection} inspection 受理前的上下文预算与模型能力检查结果。
 */

/**
 * @typedef {object} TurnRun
 * @property {string} turnId 当前轮次 ID。
 * @property {string} group 所属的异步交互组 ID；手动停止或修改历史语义会使该组失效。
 * @property {import('../../shared/contracts/conversation.js').TurnExecution} execution 对外公开的轮次执行状态。
 * @property {'generating' | 'presenting'} activity 当前轮次所处的主要阶段（生成中或演出中）。
 * @property {AbortController} controller 用于取消模型生成与语音等待的控制器。
 * @property {string[]} guides 本轮已登记的演出计划项 ID 列表；所有计划项结算后本轮才算完全结束。
 * @property {string[]} speechTasks 本轮已提交的底层语音合成任务 ID 列表。
 * @property {Promise<void>[]} pending 正在进行中的单句语音合成与演出交接任务列表。
 * @property {PreparedTurn} prepared 本轮启动时固定的上下文与配置快照。
 * @property {Promise<void>} [done] 整轮执行的收尾 Promise，供后端关闭时等待任务退出。
 * @property {import('../llm/interface.js').TokenUsage | null} [usage] 模型返回的 Token 消耗统计。
 */

/**
 * @typedef {object} ConversationState
 * @property {import('../../shared/contracts/conversation.js').Chat} chat 当前内存中的对话数据；保存失败时可能比磁盘存档更新。
 * @property {string} resourceOwner 持有当前内存中对话所引用资源的临时持有者 ID。
 * @property {number} revision 内存快照的观察版本号，与磁盘存档的 chat.revision 独立递增。
 * @property {TurnRun | null} active 当前正在运行或等待保留句演出结束的轮次。
 * @property {Map<string, import('../../shared/contracts/conversation.js').TurnExecution>} executions 本次运行期间记录过的各轮次执行状态。
 * @property {string | null} group 当前有效的异步交互组 ID，仅保存在内存中。
 * @property {import('../../shared/contracts/conversation.js').AsyncResultItem[]} pending 等待下一轮自动消费的异步交互结果队列。
 * @property {Set<string>} acceptedResults 已接收过的异步交互请求 ID 集合，用于去重。
 * @property {Map<string, {line: LineRef, guideId: string}>} guides 台词 ID 到演出计划项的映射表。
 * @property {Map<string, string>} audioTokens 每句台词当前最新的语音合成令牌，用于丢弃被覆盖的旧合成结果。
 * @property {import('../../shared/contracts/conversation.js').ConversationSnapshot['audioProblems']} audioProblems 单句语音合成失败记录。
 * @property {import('../../shared/contracts/conversation.js').ConversationSnapshot['persistence']} persistence 当前存档保存状态与错误信息。
 * @property {import('../../shared/contracts/common.js').Problem | null} problem 最近一次发生的业务错误。
 * @property {boolean} compressing 是否正在执行历史上下文压缩。
 * @property {string} sequenceId 该对话在演出模块中对应的演出序列 ID。
 * @property {import('../../shared/contracts/conversation.js').ConversationSnapshot} [published] 最近一次对外发布的完整快照。
 * @property {boolean} [deleted] 标记该对话是否已被删除，用于拦截迟到的异步任务回写。
 */

/**
 * @typedef {object} InteractionRegistration
 * @property {string} chatId 发起该交互的对话 ID。
 * @property {string} group 登记交互时所属的轮次组 ID。
 * @property {string} toolName 发起该交互的工具名称。
 * @property {import('../../shared/contracts/tools.js').ToolExecutionContext} source 发起交互时的工具调用上下文。
 */

/**
 * 对话管理服务。
 * 负责维护每个对话的生命周期、轮次生成、语音合成与演出推进、历史记录修改、提醒投递以及异步工具交互结果的消费。
 */
export class Conversations {
  /**
   * 创建对话管理服务实例。
   * @param {Pick<import('../create-core-backend.js').CoreBackend, 'store' | 'settings' | 'characters' | 'agent' | 'speech' | 'performance' | 'assets' | 'tools'> & {remoteFiles?: import('../remote-files/interface.js').RemoteFiles, access: import('../../shared/contracts/tools.js').ToolAccessScope}} dependencies 后端各子模块实例与工具权限配置。
   */
  constructor({
    store,
    settings,
    characters,
    agent,
    speech,
    performance,
    assets,
    tools,
    access,
    remoteFiles
  }) {
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
    this.remoteFiles = remoteFiles
    this.loading = new Map()
    this.queue = new SerialQueue()
    this.bus = new SnapshotBus()
    this.runtimeId = uuid()
    this.closed = false
    this.pendingCompletions = new Map()
    this.synthesizing = new Set()
    this.synthesisControllers = new Set()
  }

  /**
   * 获取指定对话的运行时状态；若尚未加载到内存，则先从存档目录加载并初始化。
   * @param {string} chatId 对话 ID。
   * @returns {Promise<ConversationState>} 对话的运行时状态对象。
   */
  async state(chatId) {
    requireValue(!this.closed, 'backend_closed')
    if (this.states.has(chatId)) return this.states.get(chatId)
    // 同一对话首次被并发访问时共用同一个加载 Promise，避免重复初始化两个状态实例或创建两条演出序列。
    if (!this.loading.has(chatId))
      this.loading.set(
        chatId,
        this.load(chatId).finally(() => this.loading.delete(chatId))
      )
    return this.loading.get(chatId)
  }

  /**
   * 从磁盘加载对话存档，并初始化其运行时状态与演出序列。
   * @param {string} chatId 对话 ID。
   * @returns {Promise<ConversationState>} 初始化完成的运行时状态对象。
   */
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
      // 重启后只恢复存档记录，不自动续跑中断前的生成任务；若有缺少结果的工具调用，直接补记为已中断。
      if (this.interruptMissing(state.chat)) await this.persist(state)
      this.states.set(chatId, state)
      return state
    } catch (error) {
      if (state?.sequenceId) await this.performance.closeSequence(state.sequenceId)
      await this.assets.releaseWhenPossible(resourceOwner)
      throw error
    }
  }

  /**
   * 解析当前对话中所有参与角色的能力快照（包含人设、动作表情列表及声音配置），并校验角色名称不重复。
   * @param {import('../../shared/contracts/conversation.js').Chat} chat 对话数据。
   * @param {string | null} [owner] 关联的临时资源持有者 ID。
   * @returns {Promise<import('../../shared/contracts/characters.js').CharacterCapabilities[]>} 参与角色的能力快照列表。
   */
  async capabilities(chat, owner = null) {
    const result = []
    for (const id of chat.characterIds) {
      const selection = chat.meta.settings.characters[id] ?? {}
      result.push(await this.characterCapability(owner, id, selection))
    }
    requireValue(
      new Set(result.map((character) => character.name)).size === result.length,
      'duplicate_character_name',
      '参与对话的角色名称重复，请先在角色目录中修改。'
    )
    return result
  }

  /**
   * 解析单个角色在指定形态与模型覆盖下的能力快照。
   * @param {string | null} owner 关联的临时资源持有者 ID；若提供，会在读取角色时将相关资源绑定到该持有者名下。
   * @param {string} id 角色 ID。
   * @param {Partial<import('../../shared/contracts/settings.js').CharacterSelection>} selection 角色的形态与模型选择配置。
   * @returns {Promise<import('../../shared/contracts/characters.js').CharacterCapabilities>} 角色能力快照。
   */
  async characterCapability(owner, id, selection) {
    const options = {
      formId: selection.formId,
      modelId: selection.modelOverrides?.[selection.formId]
    }
    return owner
      ? this.characters.acquireCapabilities(owner, id, options)
      : this.characters.resolveCapabilities(id, options)
  }

  /**
   * 解析对话当前的演出场景配置，并在场景资源受保护的期间执行指定操作。
   * @template T
   * @param {import('../../shared/contracts/conversation.js').Chat} chat 对话数据。
   * @param {(scene: import('../../shared/contracts/presentation.js').Scene) => Promise<T>} operation 接收场景配置的异步操作（如创建或更新演出序列）。
   * @returns {Promise<T>} 异步操作的返回结果。
   */
  async withScene(chat, operation) {
    // 待 operation（如 createSequence 或 setScene）建立好自身的资源保护后，才释放解析阶段创建的临时持有者。
    return this.assets.using(chat, async (owner) => operation(await this.scene(chat, false, owner)))
  }

  /**
   * 根据对话的角色选择与布局设置，构造供演出模块使用的场景配置（Scene）。
   * @param {import('../../shared/contracts/conversation.js').Chat} chat 对话数据。
   * @param {boolean} [strict] 是否严格校验角色与模型存在。加载历史对话或更新舞台时传 false 允许缺失模型降级为空展示，不阻断历史查看。
   * @param {string | null} [owner] 关联的临时资源持有者 ID。
   * @returns {Promise<import('../../shared/contracts/presentation.js').Scene>} 演出场景配置对象。
   */
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

  /**
   * 根据对话当前的运行时状态构造对外发布的完整状态快照。
   * @param {ConversationState} state 对话的运行时状态。
   * @returns {import('../../shared/contracts/conversation.js').ConversationSnapshot} 完整的对话状态快照。
   */
  snapshot(state) {
    const reasons = []
    // 当轮次已被停止（terminated）但后端时间线上的当前句尚未播完时，仍标记为 retained-guide 阻止发起新轮次。
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

  /**
   * 递增观察版本号，并向所有订阅者推送当前对话的最新状态快照。
   * @param {ConversationState} state 对话的运行时状态。
   * @returns {void}
   */
  publish(state) {
    state.revision += 1
    // 缓存最近一次完整发布的快照，避免外部 getSnapshot 在某次异步修改中途读到尚未修改完毕的内部状态。
    state.published = this.snapshot(state)
    this.bus.emit(state.chat.id, state.published)
  }

  /**
   * 将内存中的对话内容保存到磁盘，并更新持久化状态与观察快照。
   * @param {ConversationState} state 对话的运行时状态。
   * @param {boolean} [retry] 是否为针对此前保存失败状态的显式重试。
   * @returns {Promise<boolean>} 保存成功返回 true；保存失败或处于未恢复的失败状态时返回 false。
   */
  async persist(state, retry = false) {
    await this.assets.replaceReferences(state.resourceOwner, extractAssetReferences(state.chat))
    // 为什么保存失败后在非 retry 调用时直接跳过写盘：
    // 磁盘保存失败时内存中的对话仍包含最新生成的工具结果或台词，不能回滚内存；此时锁定写盘等待用户显式调用 retrySave 恢复。
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

  /**
   * 检查对话当前是否处于空闲且可修改的状态；若正在生成、压缩或存在未恢复的保存错误则抛出异常。
   * @param {ConversationState} state 对话的运行时状态。
   * @param {boolean} [allowRetained] 是否允许在已停止轮次仍保留最后一句演出的状态下继续操作（历史编辑与截断会一并清理保留句，因此可传 true）。
   * @returns {void}
   */
  assertIdle(state, allowRetained = false) {
    requireValue(state.persistence.state !== 'failed', 'save_failed', '请先重试保存当前内容。')
    requireValue(
      !state.compressing &&
        (!state.active || (allowRetained && state.active.execution.state === 'terminated')),
      'chat_busy',
      '本对话尚未完成。'
    )
  }

  /**
   * 校验用户手动发送的消息内容格式是否合法（要求文本与附件至少有一项非空）。
   * @param {UserSubmission} submission 用户提交的消息内容。
   * @returns {void}
   */
  validateSubmission(submission) {
    fields(submission, ['text', 'attachments'])
    requireValue(
      typeof submission.text === 'string' &&
        Array.isArray(submission.attachments) &&
        (submission.text.trim() || submission.attachments.length)
    )
  }

  /**
   * 将存档中的用户消息（包括手动发言、到期提醒或异步交互结果）转换为供 Agent 读取的统一输入格式。
   * @param {import('../../shared/contracts/conversation.js').UserMessage} message 存档中的用户消息对象。
   * @returns {import('../llm/interface.js').ProjectedUserMessage} 转换后的模型输入消息。
   */
  userProjection(message) {
    let text
    let attachments = []
    if (message.source.kind === 'manual') {
      text = message.content.text
      attachments = message.content.attachments.filter((item) =>
        item.mediaType.startsWith('image/')
      )
      const files = message.content.attachments.filter(
        (item) => !item.mediaType.startsWith('image/')
      )
      if (files.length)
        text +=
          '\n工作文件（内容尚未读取，请使用文件工具）：' +
          JSON.stringify(files.map((item) => ({ name: item.name, asset: item.asset })))
    } else if (message.source.kind === 'async') {
      text =
        '[async_tool_results]\n以下为此前异步工具（提问或抽签）完成的返回结果，不是对话者新说出口的台词，请据此自然继续回应。提问结果 kind=skipped 表示对方主动跳过、未提供答案，不代表同意、否定或默认选择，请勿将其当作授权或反复追问：\n' +
        JSON.stringify(message.results) +
        '\n[/async_tool_results]'
    } else {
      text =
        '[scheduled_reminder]\n以下为定时提醒到期的触发指令，不是对话者说出口的台词，请按提醒内容主动向对方开口：\n' +
        message.text +
        '\n[/scheduled_reminder]'
    }
    return {
      messageId: message.id,
      kind: 'user',
      source: copy(message.source),
      text,
      attachments: copy(attachments),
      unavailableAttachments: []
    }
  }

  /**
   * 从对话历史中提取供模型请求使用的上下文消息列表（若历史中存在压缩摘要，则自动从最后一条有效摘要处开始截取）。
   * @param {import('../../shared/contracts/conversation.js').Chat} chat 对话数据。
   * @returns {import('../llm/interface.js').ProjectedTurn[]} 供模型读取的历史轮次列表。
   */
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
        // 遇到历史压缩摘要时清空此前累积的更早轮次，以该摘要作为后续上下文的起点，但存档中的原始历史消息本身保持不变。
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
      if (messages.length)
        result.push({
          turnId: turn.id,
          characterContext: copy(turn.meta.characterContext ?? null),
          messages
        })
    }
    return result
  }

  /** 将手动输入中的非图片资源转成可编辑工作文件；只读预览不落盘。 */
  async prepareWorkFiles(chat, message, resourceOwner, preview = false) {
    if (message?.source.kind === 'manual') {
      for (const attachment of message.content.attachments) {
        const info = await this.assets.inspect(attachment.asset)
        attachment.mediaType = info.mediaType
        attachment.name = info.name
        if (!info.mediaType.startsWith('image/') && !preview) {
          requireValue(
            info.kind === 'file' && info.sizeBytes <= 64 * 1024 * 1024,
            'unsupported_work_file'
          )
          chat.meta.workspaceFiles ??= []
          let existing = chat.meta.workspaceFiles.find((file) => file.asset.id === info.asset.id)
          if (!existing) {
            const imported = await this.assets.writeWorkspaceFile(
              chat.id,
              info.name,
              await this.assets.read(info.asset),
              resourceOwner,
              { mediaType: info.mediaType }
            )
            existing = {
              asset: imported.asset,
              name: imported.name,
              mediaType: imported.mediaType
            }
            chat.meta.workspaceFiles.push(existing)
          }
          Object.assign(attachment, copy(existing))
        }
      }
    }
  }

  /**
   * 合并全局设置、对话设置与本轮覆盖选项，解析模型连接、角色能力与工具列表，并构造生成请求与上下文占用检查结果。
   * @param {import('../../shared/contracts/conversation.js').Chat} chat 对话数据。
   * @param {import('../../shared/contracts/conversation.js').UserMessage | null} input 本轮的用户输入消息；情景生成或仅预览历史时为 null。
   * @param {Partial<TurnOptions>} options 本轮的覆盖配置。
   * @returns {Promise<PreparedTurn & {resourceOwner: string}>} 准备就绪的轮次执行材料及临时资源持有者 ID。
   */
  async prepare(chat, input, options, preview = false) {
    const resourceOwner = await this.assets.createTemporaryOwner(
      'turn',
      extractAssetReferences({ chat, input })
    )
    try {
      await this.prepareWorkFiles(chat, input, resourceOwner, preview)
      const global = await this.settings.get()
      const generation = merge(global.generation, chat.meta.settings.generation, options.generation)
      if (generation.compression.thresholdTokens === 258000)
        generation.compression.thresholdTokens = 270000
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
      const workspaceFiles = (chat.meta.workspaceFiles ?? []).map((file) => file.name)
      const hasFileTools = tools.some((tool) =>
        ['list_directory', 'read_file_content', 'grep_search_in_file', 'export_document'].includes(
          tool.name
        )
      )
      const request = {
        turnId: '',
        mode: chat.mode,
        characters: capabilities.map((character) => ({
          id: character.id,
          name: character.name,
          forms: copy(character.forms),
          modelVersion: character.modelVersion,
          formId: character.formId,
          displayName: character.displayName,
          description: character.description,
          performances: {
            motions: character.performances.motions,
            expressions: character.performances.expressions,
            presets: character.performances.presets
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
        supplementalContext:
          hasFileTools || workspaceFiles.length > 0
            ? [
                '文件工具的相对路径以本对话 workspace 为基准。仅能向 workspace 写入文件；工作文件并未作为模型附件发送，必须使用工具读取。',
                '当前工作文件：' + JSON.stringify(workspaceFiles)
              ]
            : [],
        tools,
        toolDefinitions: await this.tools.list()
      }

      const inspection = await this.agent.inspectContext(request)
      return { request, speech, generation, capabilities, inspection, resourceOwner }
    } catch (error) {
      await this.assets.releaseWhenPossible(resourceOwner)
      throw error
    }
  }

  /**
   * 校验并受理新的对话轮次：完成请求去重、自动上下文压缩、将新轮次持久化写入存档，并在后台启动模型生成。
   * @param {ConversationState} state 对话的运行时状态（调用方需已持有该对话的串行队列锁）。
   * @param {import('../../shared/contracts/conversation.js').UserMessage | null} input 本轮的用户输入消息；情景生成时为 null。
   * @param {TurnOptions} options 本轮的请求 ID 与覆盖配置。
   * @param {{ replaceId?: string | null, automatic?: boolean, delivery?: string | null }} [flags] 受理附加标志：replaceId 指定要替换的最后一轮 ID，automatic 标记是否为工具/提醒触发的自动轮次，delivery 指定本次投递的提醒 ID。
   * @returns {Promise<string>} 已受理并持久化的新轮次 ID（TurnId）。
   */
  async admit(
    state,
    input,
    options,
    { replaceId = null, automatic = false, delivery = null, lotteryDelivery = null } = {}
  ) {
    fields(options, ['requestId', 'generation', 'speech'])
    requireValue(
      typeof options.requestId === 'string' &&
        options.requestId.length > 0 &&
        !['__proto__', 'constructor', 'prototype'].includes(options.requestId)
    )
    // 只保留原始提交的摘要，避免去重账本继续持有已复制到 workspace 的临时上传原件。
    const submissionFingerprint =
      input?.source.kind === 'manual' ? fingerprint(input.content) : null
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
    if (!automatic) for (const lottery of candidate.meta.lotteries ?? []) lottery.cancelled = true
    if (replaceId) {
      requireValue(candidate.turns.at(-1)?.id === replaceId, 'not_last_turn')
      candidate.turns.pop()
    }
    const prepared = await this.prepare(candidate, input, options)
    let accepted = false
    try {
      let compressionProblem = null
      const compression = prepared.generation.compression
      if (
        compression.enabled &&
        (prepared.inspection.fit === 'over-budget' ||
          prepared.inspection.estimatedInputTokens >
            contextBudget(prepared.request).thresholdTokens)
      ) {
        const count = candidate.turns.length - Math.max(1, compression.retainedTurns)
        if (count > 0) {
          try {
            const result = await this.agent.compress({
              history: this.history({ ...candidate, turns: candidate.turns.slice(0, count) }),
              options: prepared.generation,
              connection: prepared.request.connection
            })
            const covered = candidate.turns[count - 1]
            covered.messages = covered.messages.filter((message) => message.kind !== 'abstract')
            covered.messages.push({
              id: uuid(),
              kind: 'abstract',
              text: result.text,
              extensions: {}
            })
            prepared.request.history = this.history(candidate)
            prepared.inspection = await this.agent.inspectContext(prepared.request)
          } catch (error) {
            compressionProblem = problem(error)
          }
        }
      }
      const trimmed = await trimHistory(prepared.request, (request) =>
        this.agent.inspectContext(request)
      )
      prepared.inspection = trimmed.inspection
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
          characterContext: characterContext(prepared.request),
          tools: prepared.request.tools.map((tool) => tool.name),
          worldbook: null,
          worldbookDiagnostics: [],
          extensions: {
            requestFingerprint: material,
            contextPreparation: { removedTurnIds: trimmed.removedTurnIds, compressionProblem }
          }
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
        input: input ? { ...copy(input), id: null } : null,
        submissionFingerprint
      }
      if (delivery)
        candidate.meta.reminders.find((item) => item.id === delivery).state = 'delivered'
      if (lotteryDelivery)
        candidate.meta.lotteries.find((item) => item.id === lotteryDelivery).deliveredTurnId =
          turnId
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

  /**
   * 驱动单轮对话的完整执行流程：依次运行模型生成、等待语音合成完成、等待演出时间线播放结束，并在异常或取消时负责收尾清理。
   * @param {ConversationState} state 对话的运行时状态。
   * @param {TurnRun} run 当前轮次的运行控制对象。
   * @returns {Promise<void>}
   */
  async execute(state, run) {
    const signal = run.controller.signal
    try {
      await this.performance.setActivity(state.sequenceId, 'thinking')
      // 在对话串行队列之外等待耗时的模型生成，仅在收到增量响应或工具结果回调时进入队列更新状态，防止长任务占住队列导致 stopTurn 无法执行。
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
        await this.performance.setActivity(state.sequenceId, 'idle')
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
        for (const lottery of state.chat.meta.lotteries ?? []) {
          if (lottery.source.turnId === run.turnId) lottery.cancelled = true
        }
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
        await this.performance.setActivity(state.sequenceId, 'idle')
        this.publish(state)
      })
      await this.assets.releaseWhenPossible(run.prepared.resourceOwner)
      this.scheduleDrain(state)
    }
  }

  /**
   * 接收 Agent 产生的增量响应片段，将其写入当前轮次消息，并为新增台词排入演出计划项与语音合成任务。
   * @param {ConversationState} state 对话的运行时状态。
   * @param {TurnRun} run 当前轮次的运行控制对象。
   * @param {import('../llm/interface.js').AgentResponseUpdate} update 模型响应增量。
   * @returns {Promise<void>}
   */
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

  /**
   * 将对话台词对象转换为供演出模块使用的演出计划项（Guide）。
   * @param {import('../../shared/contracts/conversation.js').DialogueLine} line 对话台词对象。
   * @param {boolean} [ready] 该台词是否已无需等待语音合成即可直接播放，默认 true。
   * @returns {import('../../shared/contracts/presentation.js').Guide} 演出计划项对象。
   */
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

  /**
   * 根据四级嵌套引用（turnId / messageId / partId / lineId）在对话存档中精确定位目标台词。
   * @param {import('../../shared/contracts/conversation.js').Chat} chat 对话数据。
   * @param {LineRef} ref 台词定位引用。
   * @returns {import('../../shared/contracts/conversation.js').DialogueLine | undefined} 找到的台词对象；若不存在则返回 undefined。
   */
  locate(chat, ref) {
    // 为什么使用完整的四级 ID 定位而不是按数组下标查找：
    // 防止在异步合成期间因消息编辑或轮次变化，把迟到返回的音频错误写入到另一句台词上。
    const turn = chat.turns.find((item) => item.id === ref.turnId)
    return turn?.messages
      .find((item) => item.id === ref.messageId && item.kind === 'assistant')
      ?.parts.find((item) => item.id === ref.partId && item.kind === 'dialogue')
      ?.lines.find((item) => item.id === ref.lineId)
  }

  /**
   * 为指定台词启动异步语音合成，并在合成期间保护角色声音资源。
   * @param {ConversationState} state 对话的运行时状态。
   * @param {LineRef} ref 目标台词的定位引用。
   * @param {Pick<PreparedTurn, 'speech' | 'capabilities'>} prepared 语音合成参数与角色能力快照。
   * @param {TurnRun | null} [run] 所属轮次的运行控制对象；手动重合成单句音频时为 null。
   * @param {string | null} [guideId] 关联的演出计划项 ID；手动重合成单句音频时为 null。
   * @returns {Promise<void>}
   */
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
   * 执行单句台词的语音合成，并将合成结果写入对话存档与演出序列。
   * @param {ConversationState} state 对话的运行时状态对象。
   * @param {LineRef} ref 目标台词的定位信息。
   * @param {Pick<PreparedTurn, 'speech' | 'capabilities'>} prepared 语音合成参数与角色能力快照。
   * @param {TurnRun | null} run 所属轮次的运行控制对象；手动重合成单句音频时为 null。
   * @param {string | null} guideId 关联的演出计划项 ID；手动重合成单句音频时为 null。
   * @param {AbortSignal} signal 用于取消语音合成等待的中断信号。
   * @returns {Promise<void>}
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
        // 停止操作可能早于 submit 返回；拿到迟到的任务 ID 后仍需通知 Python 侧取消该任务。
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
        // 若对话已删除、台词已被编辑或删除、或有更新的合成任务覆盖了当前令牌，则丢弃本次迟到的合成结果。
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

  /**
   * 为对话中尚未收到结果的工具调用补齐“已中断”结果记录。
   * @param {import('../../shared/contracts/conversation.js').Chat} chat 对话数据对象。
   * @param {string | null} [onlyTurn] 若传入轮次 ID，则只检查并补齐该轮次内的工具调用；省略时检查所有轮次。
   * @returns {boolean} 是否补充了任何中断记录。
   */
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
            // 仅补记中断状态，不假设外部工具的副作用未发生，也不自动重试工具。
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

  /**
   * 修改指定对话的数据并持久化保存。
   * @param {string} chatId 对话 ID。
   * @param {(chat: import('../../shared/contracts/conversation.js').Chat) => Promise<void> | void} mutation 对话数据的修改函数，在副本上执行。
   * @param {boolean} [semantic=false] 是否属于改变对话历史语义的修改。若为 true，会同时作废未消费的异步交互结果与旧音频合成资格。
   * @returns {Promise<void>}
   */
  async update(chatId, mutation, semantic = false) {
    return this.queue.run(chatId, async () => {
      const state = await this.state(chatId)
      if (semantic) this.assertIdle(state, true)
      else requireValue(state.persistence.state !== 'failed', 'save_failed')
      const candidate = copy(state.chat)
      await mutation(candidate)
      if (semantic) for (const lottery of candidate.meta.lotteries ?? []) lottery.cancelled = true
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

  /**
   * 修改指定对话的单个设置项并保存。
   * @param {string} chatId 对话 ID。
   * @param {keyof import('../../shared/contracts/settings.js').ChatSettings} key 设置项键名。
   * @param {unknown} value 新的设置值。
   * @returns {Promise<void>}
   */
  async setting(chatId, key, value) {
    return this.update(chatId, (chat) => {
      chat.meta.settings[key] = copy(value)
    })
  }

  /**
   * 在当前对话空闲时调度下一轮自动回复，批量消费排队中的异步工具交互结果。
   * @param {ConversationState} state 对话的运行时状态对象。
   */
  scheduleDrain(state) {
    if (this.closed || this.closing) return
    void this.deliverLotteries(state.chat.id).catch((error) => this.assets.reportMaintenance(error))
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
            // 先尝试受理自动轮次，成功后再从队列中移除已消费的结果，防止受理失败时丢失用户的交互答案。
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
   * 获取指定对话当前的完整快照。
   * @param {string} chatId 对话 ID。
   * @returns {Promise<import('../../shared/contracts/conversation.js').ConversationSnapshot>} 对话快照副本。
   */
  async getSnapshot(chatId) {
    const state = await this.state(chatId)
    return copy(state.published ?? this.snapshot(state))
  }

  /**
   * 校验异步交互请求是否对应当前正在运行的工具调用，并返回交互登记信息。
   * @param {import('../../shared/contracts/tools.js').InteractionRequest} request 工具发起的交互请求。
   * @returns {Promise<InteractionRegistration | null>} 校验通过时返回关联的对话与工具信息；若对应轮次已停止或失效则返回 null。
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

  /**
   * 检查已登记的异步交互是否仍然有效。
   * @param {InteractionRegistration} registration 交互登记信息。
   * @returns {boolean} 若所属轮次组尚未因手动新消息、停止或历史编辑而失效，则返回 true。
   */
  validInteraction(registration) {
    return !this.closed && this.states.get(registration.chatId)?.group === registration.group
  }

  /**
   * 接收已完成的异步交互结果并加入对应对话的待消费队列。
   * @param {import('../../shared/contracts/tools.js').InteractionCompletion} completion 异步交互的完成结果。
   * @param {InteractionRegistration} registration 交互登记信息。
   * @param {import('../../shared/contracts/tools.js').InteractionRequest} request 原始交互请求。
   * @returns {Promise<void>}
   */
  async queueInteraction(completion, registration, request) {
    return this.queue.run(registration.chatId, async () => {
      const state = await this.state(registration.chatId)
      requireValue(this.validInteraction(registration), 'interaction_expired')
      if (state.acceptedResults.has(completion.requestId)) return
      // 正常完成的交互（包括用户主动跳过问题）进入后续自动回复；失败或系统取消仅标记已处理。
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

  /** 校验副作用来自本对话中实际运行的指定工具调用。 */
  async requireToolSource(chatId, source, toolName) {
    const registration = await this.qualifyInteraction({ source })
    requireValue(
      registration?.chatId === chatId && registration.toolName === toolName,
      'execution_expired'
    )
  }

  /** 写入工作文件并把下载引用保存到对话，运行持有者覆盖保存失败期间。 */
  async writeToolFile(chatId, input, source, signal) {
    return this.queue.run(chatId, async () => {
      await this.requireToolSource(chatId, source, 'export_document')
      const state = await this.state(chatId)
      requireValue(state.persistence.state !== 'failed', 'save_failed')
      const calls = (state.chat.meta.extensions.fileCalls ??= {})
      const key = source.turnId + ':' + source.callId
      const material = fingerprint(input)
      if (calls[key]) {
        requireValue(calls[key].material === material, 'request_conflict')
        return copy(calls[key].file)
      }
      const info = await this.assets.writeWorkspaceFile(
        chatId,
        input.filename,
        Buffer.from(input.content, 'utf8'),
        state.resourceOwner,
        {
          overwrite: input.overwrite === true,
          renameOnConflict: false,
          mediaType: 'text/plain',
          signal
        }
      )
      const file = { asset: info.asset, name: info.name, mediaType: info.mediaType }
      state.chat.meta.workspaceFiles ??= []
      const index = state.chat.meta.workspaceFiles.findIndex(
        (item) => item.asset.id === info.asset.id
      )
      if (index < 0) state.chat.meta.workspaceFiles.push(file)
      else state.chat.meta.workspaceFiles[index] = file
      calls[key] = { material, file }
      requireValue(await this.persist(state), 'save_failed')
      return copy(file)
    })
  }

  /** 返回当前对话可下载的工作文件列表。 */
  async listWorkspaceFiles(chatId) {
    return copy((await this.state(chatId)).chat.meta.workspaceFiles ?? [])
  }

  /** 只列出本对话当前角色形态下可解析的服装，不泄露模型磁盘路径或内部角色与形态 ID。 */
  async listToolModels(chatId, character) {
    const chat = (await this.state(chatId)).chat
    const definitions = await Promise.all(chat.characterIds.map((id) => this.characters.get(id)))
    const selected = character ? definitions.filter((item) => item.name === character) : definitions
    requireValue(selected.length > 0, 'character_unavailable')
    const result = []
    for (const definition of selected) {
      const id = definition.id
      const selection = chat.meta.settings.characters[id] ?? { formId: definition.defaultFormId }
      const form = definition.forms.find((item) => item.id === selection.formId)
      requireValue(form, 'form_unavailable')
      const models = []
      for (const model of form.models) {
        try {
          const capabilities = await this.characters.resolveCapabilities(id, {
            formId: form.id,
            modelId: model.id
          })
          if (!capabilities.modelVersion) continue
          models.push({
            modelId: model.id,
            name: model.name ?? model.id,
            selected: model.id === (selection.modelOverrides?.[form.id] ?? form.defaultModelId)
          })
        } catch {
          /* 失效资源不作为可选服装提供给模型。 */
        }
      }
      result.push({ character: definition.name, form: form.displayName, models })
    }
    return { characters: result }
  }

  /** 工具只能切换当前形态的服装，不借助服装接口改变角色形态。 */
  async changeToolModel(chatId, input, source) {
    let resolvedName = input.character
    await this.update(chatId, async (chat) => {
      await this.requireToolSource(chatId, source, 'change_character_live2d')
      const definitions = await Promise.all(chat.characterIds.map((id) => this.characters.get(id)))
      const definition = definitions.find((item) => item.name === input.character)
      requireValue(definition, 'character_unavailable')
      resolvedName = definition.name
      const selection = copy(
        chat.meta.settings.characters[definition.id] ?? {
          formId: definition.defaultFormId,
          modelOverrides: {}
        }
      )
      const capabilities = await this.characters.resolveCapabilities(definition.id, {
        formId: selection.formId,
        modelId: input.modelId
      })
      requireValue(capabilities.modelVersion, 'model_unavailable', '服装资源当前不可用。')
      selection.modelOverrides ??= {}
      selection.modelOverrides[selection.formId] = input.modelId
      chat.meta.settings.characters[definition.id] = selection
    })
    const state = await this.state(chatId)
    await this.withScene(state.chat, (scene) => this.performance.setScene(state.sequenceId, scene))
    return { character: resolvedName, modelId: input.modelId, state: 'saved' }
  }

  /** 创建持久抽签请求，实际随机抽取等待客户端明确调用 drawLottery。 */
  async createLottery(chatId, input, source) {
    return this.queue.run(chatId, async () => {
      await this.requireToolSource(chatId, source, 'start_lottery')
      const state = await this.state(chatId)
      requireValue(state.persistence.state !== 'failed', 'save_failed')
      const options = [...new Set(input.options.map((item) => item.trim()).filter(Boolean))]
      requireValue(options.length >= 2 && options.length <= 30, 'invalid_lottery')
      state.chat.meta.lotteries ??= []
      const material = fingerprint({ title: input.title, options })
      let item = state.chat.meta.lotteries.find(
        (item) => item.source.turnId === source.turnId && item.source.callId === source.callId
      )
      if (item) requireValue(item.material === material, 'request_conflict')
      else {
        item = {
          id: uuid(),
          title: input.title,
          options,
          source: copy(source),
          material,
          result: null,
          drawnAt: null,
          deliveredTurnId: null,
          cancelled: false
        }
        state.chat.meta.lotteries.push(item)
        requireValue(await this.persist(state), 'save_failed')
      }
      return { status: 'accepted', requestId: item.id }
    })
  }

  /** 多客户端通过快照或此接口查看同一份持久抽签状态。 */
  async listLotteries(chatId) {
    return copy((await this.state(chatId)).chat.meta.lotteries ?? [])
  }

  /** 抽签结果保存成功才返回；重复点击不会重抽或重复触发回复。 */
  async drawLottery(chatId, lotteryId) {
    const result = await this.queue.run(chatId, async () => {
      const state = await this.state(chatId)
      requireValue(state.persistence.state !== 'failed', 'save_failed')
      const item = state.chat.meta.lotteries?.find((item) => item.id === lotteryId)
      requireValue(item, 'lottery_missing')
      if (item.result) return copy(item)
      requireValue(!item.cancelled && this.lotterySourceExists(state.chat, item), 'lottery_expired')
      const index = randomInt(item.options.length)
      item.result = { index, option: item.options[index] }
      item.drawnAt = new Date().toISOString()
      requireValue(await this.persist(state), 'save_failed')
      return copy(item)
    })
    void this.deliverLotteries(chatId).catch((error) => this.assets.reportMaintenance(error))
    return result
  }

  /** 持久来源必须仍存在且已成功受理；重启中断的工具调用不可继续交付。 */
  lotterySourceExists(chat, item) {
    const turn = chat.turns.find((turn) => turn.id === item.source.turnId)
    return Boolean(
      turn?.messages.some(
        (message) =>
          message.kind === 'tool-result' &&
          message.callId === item.source.callId &&
          message.outcome.status === 'accepted' &&
          message.outcome.requestId === item.id
      )
    )
  }

  /** 结果交付与动画无关；当前生成或演出结束后继续调度，受理账本保证至多一次。 */
  async deliverLotteries(chatId) {
    if (this.closed || this.closing) return
    return this.queue.run(chatId, async () => {
      const state = await this.state(chatId)
      if (state.active || state.compressing || state.persistence.state === 'failed') return
      const item = state.chat.meta.lotteries?.find(
        (item) =>
          item.result &&
          !item.cancelled &&
          !item.deliveredTurnId &&
          this.lotterySourceExists(state.chat, item)
      )
      if (!item) return
      await this.admit(
        state,
        {
          id: uuid(),
          kind: 'user',
          source: { kind: 'async' },
          results: [
            {
              requestId: item.id,
              sourceTurnId: item.source.turnId,
              callId: item.source.callId,
              toolName: 'start_lottery',
              status: 'succeeded',
              request: { title: item.title, options: item.options },
              result: copy(item.result)
            }
          ],
          extensions: {}
        },
        { requestId: 'lottery:' + item.id },
        { automatic: true, lotteryDelivery: item.id }
      )
    })
  }

  /**
   * 为指定对话创建一条定时提醒并持久化保存。
   * @param {string} chatId 对话 ID。
   * @param {{dueAt: string, instruction: string}} options 提醒的到期时间与提示内容。
   * @param {import('../../shared/contracts/tools.js').ToolExecutionContext} source 发起创建的工具调用上下文。
   * @returns {Promise<import('../../shared/contracts/conversation.js').Reminder>} 已保存的提醒记录。
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
      // 同一轮次的同一个工具调用重试时直接复用已创建的提醒，防止重复创建。
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
   * 关闭对话管理服务，停止所有正在运行的轮次与语音合成任务并释放资源。
   * @returns {Promise<void>}
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
   * 列出所有已保存对话的摘要信息。
   * @returns {Promise<import('../../shared/contracts/conversation.js').ChatSummary[]>} 按显示顺序排列的对话摘要列表。
   */
  async listChats() {
    return this.store.list()
  }

  /**
   * 创建并保存一个新的空白对话。
   * @param {CreateChatOptions} options 创建对话所需的标题、参与角色、模式及初始设置。
   * @returns {Promise<string>} 新创建的对话 ID。
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
   * 发送一条用户消息并启动新的一轮回复生成。
   * @param {string} chatId 目标对话 ID。
   * @param {UserSubmission} submission 用户发送的消息文本与附件。
   * @param {TurnOptions} options 请求标识与本轮临时覆盖的生成选项。
   * @returns {Promise<string>} 新创建的轮次 ID。
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
   * 按当前对话配置的情景开始生成双人小剧场对话（仅在 scripted-dialogue 模式下可用）。
   * @param {string} chatId 目标对话 ID。
   * @param {TurnOptions} options 请求标识与本轮临时覆盖的生成选项。
   * @returns {Promise<string>} 新创建的轮次 ID。
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
   * 停止指定轮次的生成与后续待播演出，保留已生成的历史内容。
   * @param {string} chatId 目标对话 ID。
   * @param {string} turnId 要停止的轮次 ID。
   * @returns {Promise<void>}
   */
  async stopTurn(chatId, turnId) {
    return this.queue.run(chatId, async () => {
      const state = await this.state(chatId)
      const run = state.active
      if (!run || run.turnId !== turnId) {
        // 即使该轮已经生成完毕，对最后一轮调用停止也会作废其尚未消费的异步交互组。
        if (state.chat.turns.at(-1)?.id === turnId) {
          for (const lottery of state.chat.meta.lotteries ?? []) lottery.cancelled = true
          state.group = null
          state.pending = []
          this.interactions?.publish()
          await this.persist(state)
        }
        return
      }
      run.execution.state = 'terminated'
      run.controller.abort()
      for (const lottery of state.chat.meta.lotteries ?? []) lottery.cancelled = true
      if (state.group === run.group) {
        state.group = null
        state.pending = []
        this.interactions?.publish()
      }
      // 移除后续尚未开始的演出计划项，但保留当前正在演出的那一句直到其逻辑时间结束。
      await this.performance.discardPendingGuides(run.guides)
      if (run.speechTasks.length) this.speech.cancel(run.speechTasks).catch(() => {})
      this.interruptMissing(state.chat, turnId)
      await this.persist(state)
    })
  }

  /**
   * 获取指定单句或整轮已保存的台词与场景资源，供前端独立回放。
   * @param {string} chatId 来源对话 ID。
   * @param {{ kind: 'line', line: LineRef } | { kind: 'turn', turnId: string }} target 要回放的单句定位或整轮 ID。
   * @returns {Promise<import('../../shared/contracts/presentation.js').ReplayMaterials>} 组装好的回放场景与演出计划项列表。
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
    const scene = await this.scene(state.chat, false)
    return {
      scene,
      guides: await this.performance.resolveGuides(
        scene,
        lines.map((line) => this.guide(line))
      )
    }
  }

  /**
   * 保留最后一轮的输入内容，按当前设置重新生成并替换该轮回复。
   * @param {string} chatId 目标对话 ID。
   * @param {string} turnId 要重新生成的最后一轮 ID。
   * @param {TurnOptions} options 请求标识与本轮临时覆盖的生成选项。
   * @returns {Promise<string>} 替换后的新轮次 ID。
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
   * 修改最后一轮的用户手动输入内容，并重新生成该轮回复。
   * @param {string} chatId 目标对话 ID。
   * @param {string} turnId 要修改并重生成的最后一轮 ID。
   * @param {UserSubmission} submission 修改后的用户消息内容。
   * @param {TurnOptions} options 请求标识与本轮临时覆盖的生成选项。
   * @returns {Promise<string>} 替换后的新轮次 ID。
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
            (receipt.submissionFingerprint ?? fingerprint(receipt.input?.content)) ===
              fingerprint(submission),
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
   * 原地编辑历史记录中的用户消息或角色台词/翻译，不触发重新生成。
   * @param {string} chatId 目标对话 ID。
   * @param {HistoryEdit} edit 要编辑的目标位置与新内容。
   * @returns {Promise<void>}
   */
  async editHistory(chatId, edit) {
    return this.update(
      chatId,
      async (chat) => {
        let index
        if (edit.kind === 'user') {
          this.validateSubmission(edit.content)
          index = chat.turns.findIndex((turn) => turn.id === edit.turnId)
          const message = chat.turns[index]?.messages.find((item) => item.id === edit.messageId)
          requireValue(message?.source?.kind === 'manual', 'not_manual_input')
          message.content = copy(edit.content)
          await this.prepareWorkFiles(chat, message, (await this.state(chatId)).resourceOwner)
        } else {
          requireValue(edit.kind === 'line' && typeof edit.text === 'string')
          const line = this.locate(chat, edit.line)
          requireValue(line, 'line_missing')
          index = chat.turns.findIndex((turn) => turn.id === edit.line.turnId)
          // 台词正文发生变化时同步更新合成文本并清除旧音频；仅修改翻译时保留原音频。
          if (line.text !== edit.text) {
            line.text = edit.text
            line.spoken.text = edit.text
            line.audio = null
          }
          line.translation = edit.translation
        }
        // 清除被编辑轮次及其后续轮次中的压缩摘要，避免摘要与修改后的原文不一致。
        for (const turn of chat.turns.slice(index))
          turn.messages = turn.messages.filter((message) => message.kind !== 'abstract')
      },
      true
    )
  }

  /**
   * 删除指定轮次及其之后的所有历史记录。
   * @param {string} chatId 目标对话 ID。
   * @param {string} turnId 起始删除的轮次 ID（该轮次也会被删除）。
   * @returns {Promise<void>}
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
   * 将对话历史回退到指定轮次结束处，删除其后的所有轮次。
   * @param {string} chatId 目标对话 ID。
   * @param {string | null} throughTurnId 要保留的最后一轮 ID（包含该轮）；传 null 时清空全部历史轮次。
   * @returns {Promise<void>}
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
   * 将指定轮次及之前的对话内容复制为一个独立的新对话。
   * @param {string} chatId 来源对话 ID。
   * @param {string | null} throughTurnId 复制截止的轮次 ID（包含该轮）；传 null 时仅复制对话设置而不包含历史轮次。
   * @returns {Promise<string>} 新生成的对话 ID。
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
   * 按当前语音设置重新合成指定台词的音频。
   * @param {string} chatId 来源对话 ID。
   * @param {LineRef} line 目标台词定位信息。
   * @returns {Promise<void>}
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
   * 预估当前对话上下文与草稿内容的 Token 占用情况，不修改对话历史。
   * @param {string} chatId 目标对话 ID。
   * @param {UserSubmission | null} submission 可选的草稿内容；传 null 时仅检查当前已保存的历史与设置。
   * @param {import('../../shared/contracts/settings.js').GenerationOptions} options 预览使用的生成选项覆盖。
   * @returns {Promise<import('../llm/interface.js').ContextInspection>} 上下文占用预估与模型预算检查结果。
   */
  async inspectContext(chatId, submission, options) {
    const state = await this.state(chatId)
    if (submission) this.validateSubmission(submission)
    const input = submission
      ? { id: uuid(), kind: 'user', source: { kind: 'manual' }, content: copy(submission) }
      : null
    const prepared = await this.prepare(copy(state.chat), input, { generation: options }, true)
    if (!submission) prepared.request.input = null
    try {
      return await this.agent.inspectContext(prepared.request)
    } finally {
      await this.assets.releaseWhenPossible(prepared.resourceOwner)
    }
  }

  /**
   * 手动触发对话历史压缩，将较早的轮次总结为摘要并保存。
   * @param {string} chatId 目标对话 ID。
   * @returns {Promise<void>}
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
      // 至少保留最后一轮完整对话不被压缩。
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
   * 重试将当前内存中的对话修改保存到磁盘。
   * @param {string} chatId 保存失败的对话 ID。
   * @returns {Promise<void>}
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
   * @param {string} chatId 目标对话 ID。
   * @param {string} title 新标题。
   * @returns {Promise<void>}
   */
  async renameChat(chatId, title) {
    requireValue(typeof title === 'string')
    return this.update(chatId, (chat) => {
      chat.title = title
    })
  }

  /**
   * 保存对话列表的显示排序。
   * @param {string[]} chatIds 按新顺序排列的完整对话 ID 列表。
   * @returns {Promise<void>}
   */
  async setChatOrder(chatIds) {
    return this.store.setOrder(chatIds)
  }

  /**
   * 设置或清除后续小剧场生成使用的情景设定。
   * @param {string} chatId 目标对话 ID。
   * @param {import('../../shared/contracts/settings.js').ScenarioSettings | null} options 情景设定；传 null 时清除。
   * @returns {Promise<void>}
   */
  async setScenario(chatId, options) {
    return this.setting(chatId, 'scenario', options)
  }

  /**
   * 设置该对话后续生成可使用的工具集合。
   * @param {string} chatId 目标对话 ID。
   * @param {import('../../shared/contracts/settings.js').ToolSelection} options 工具选择配置。
   * @returns {Promise<void>}
   */
  async setTools(chatId, options) {
    await this.tools.resolve(chatId, options, this.access, null)
    return this.setting(chatId, 'tools', options)
  }

  /**
   * 设置该对话后续生成使用的世界书范围与剧情进度。
   * @param {string} chatId 目标对话 ID。
   * @param {import('../../shared/contracts/worldbook.js').WorldbookInfo} options 世界书配置。
   * @returns {Promise<void>}
   */
  async setWorldbookInfo(chatId, options) {
    return this.setting(chatId, 'worldbook', options)
  }

  /**
   * 更改对话中指定角色的形态与 Live2D 服装模型选择，并同步更新当前舞台场景。
   * @param {string} chatId 目标对话 ID。
   * @param {string} characterId 参与对话的角色 ID。
   * @param {import('../../shared/contracts/settings.js').CharacterSelection} options 角色的当前形态与模型覆盖配置。
   * @returns {Promise<void>}
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
   * 修改该对话的生成设置（在下一轮生成时生效）。
   * @param {string} chatId 目标对话 ID。
   * @param {import('../../shared/contracts/settings.js').GenerationOptions} options 对话级生成设置覆盖。
   * @returns {Promise<void>}
   */
  async setGenerationOptions(chatId, options) {
    generationOptions(options)
    return this.setting(chatId, 'generation', options)
  }

  /**
   * 修改该对话的语音合成设置（在下一轮生成时生效）。
   * @param {string} chatId 目标对话 ID。
   * @param {import('../../shared/contracts/settings.js').SpeechOptions} options 对话级语音设置覆盖。
   * @returns {Promise<void>}
   */
  async setSpeechOptions(chatId, options) {
    speechOptions(options)
    return this.setting(chatId, 'speech', options)
  }

  /**
   * 更新该对话的舞台画面设置（包括背景图、角色站位布局等），并立即同步到演出场景。
   * @param {string} chatId 目标对话 ID。
   * @param {import('../../shared/contracts/settings.js').ChatPresentationSettings} options 舞台画面配置。
   * @returns {Promise<void>}
   */
  async setPresentation(chatId, options) {
    presentationOptions(options, (await this.state(chatId)).chat.characterIds)
    await this.setting(chatId, 'presentation', options)
    const state = await this.state(chatId)
    await this.withScene(state.chat, (scene) => this.performance.setScene(state.sequenceId, scene))
  }

  /**
   * 列出提醒事项；如果不指定对话 ID，则返回所有对话中的提醒。
   * @param {{ chatId?: string }} [options] 筛选条件；省略 chatId 时查询全部对话。
   * @returns {Promise<import('../../shared/contracts/conversation.js').ReminderEntry[]>} 提醒列表（每项附带所属的 chatId）。
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
   * 根据提醒 ID 查询单条提醒详情及其所属对话。
   * @param {string} reminderId 提醒 ID。
   * @returns {Promise<import('../../shared/contracts/conversation.js').ReminderEntry | null>} 提醒记录；若不存在则返回 null。
   */
  async getReminder(reminderId) {
    return (await this.listReminders()).find((reminder) => reminder.id === reminderId) ?? null
  }

  /**
   * 删除指定的提醒事项。
   * @param {string} reminderId 提醒 ID。
   * @returns {Promise<void>}
   */
  async removeReminder(reminderId) {
    const entry = await this.getReminder(reminderId)
    if (entry)
      await this.update(entry.chatId, (chat) => {
        chat.meta.reminders = chat.meta.reminders.filter((item) => item.id !== reminderId)
      })
  }

  /**
   * 接收异步交互工具的最终结果，供当前轮次结束后自动触发后续回复。
   * @param {import('../../shared/contracts/tools.js').InteractionCompletion} completion 异步交互的完成结果。
   * @returns {Promise<'queued' | 'settled' | 'duplicate' | 'stale'>} 处理状态结果。
   */
  async acceptInteractionResult(completion) {
    return this.interactions.complete(completion).then(() => 'settled')
  }

  /**
   * 尝试投递一条到期的提醒事项；若对话当前空闲且提醒未过期，则创建新的一轮回复。
   * @param {string} chatId 所属对话 ID。
   * @param {string} reminderId 提醒 ID。
   * @param {string} deliveryId 本次投递的唯一标识。
   * @returns {Promise<string | null>} 成功触发生成时返回新轮次 ID；若对话正忙、提醒无效或已过期则返回 null。
   */
  async deliverReminder(chatId, reminderId, deliveryId) {
    return this.queue.run(chatId, async () => {
      const state = await this.state(chatId)
      const reminder = state.chat.meta.reminders.find((item) => item.id === reminderId)
      if (!reminder || reminder.state !== 'enabled' || Date.parse(reminder.dueAt) > Date.now())
        return null
      // 超过到期时间 12 小时以上的提醒直接标记为过期，不再补发。
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
   * 删除指定的对话及其本地存档，并释放相关资源。
   * @param {string} chatId 目标对话 ID。
   * @returns {Promise<void>}
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
      // 本地删除已成功；远端文件清理失败仅记录维护告警，不回滚对话删除结果。
      void this.remoteFiles?.collectUnused().catch((error) => this.assets.reportMaintenance(error))
    })
  }

  /**
   * 将选定的一个或多个对话及其关联资源打包导出为归档文件。
   * @param {string[]} chatIds 要导出的对话 ID 列表。
   * @returns {Promise<import('../../shared/contracts/common.js').AssetRef>} 导出的归档文件引用。
   */
  async exportChats(chatIds) {
    return this.store.exportArchive(chatIds)
  }

  /**
   * 从归档文件中导入对话记录，为每个对话分配新的 ID 并保存。
   * @param {import('../../shared/contracts/common.js').AssetRef} archive 归档文件引用。
   * @returns {Promise<import('../../shared/contracts/archive.js').ChatImportResult>} 导入后的新对话 ID 列表与告警信息。
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
   * 订阅指定对话的状态快照变化，订阅时会立即推送一次当前快照。
   * @param {string} chatId 目标对话 ID。
   * @param {(snapshot: import('../../shared/contracts/conversation.js').ConversationSnapshot) => void} listener 接收快照更新的回调函数。
   * @returns {Promise<import('../../shared/contracts/common.js').Unsubscribe>} 取消订阅的函数。
   */
  async observe(chatId, listener) {
    const state = await this.state(chatId)
    return this.bus.subscribe(chatId, listener, state.published ?? this.snapshot(state))
  }

  /**
   * 将复制或导入的对话分配新的唯一标识并持久化保存。
   * @param {import('../../shared/contracts/conversation.js').Chat} original 源对话数据对象。
   * @returns {Promise<string>} 保存后的新对话 ID。
   */
  async saveCopy(original) {
    // 为复制出的提醒分配新的全局唯一 ID，并同步替换对话历史中指向原提醒 ID 的引用；
    // 同时将复制出的提醒默认设为停用状态，避免分支或导入后重复触发定时提醒。
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
    delete chat.meta.extensions.fileCalls
    chat.meta.lotteries = []
    for (const turn of chat.turns) {
      turn.meta.requestId = null
      delete turn.meta.extensions.requestFingerprint
    }
    this.interruptMissing(chat)
    await this.assets.using(original, async (owner) => {
      const assets = new Map()
      for (const file of chat.meta.workspaceFiles ?? []) {
        const info = await this.assets.inspect(file.asset)
        requireValue(info.sizeBytes <= 64 * 1024 * 1024, 'asset_too_large')
        const imported = await this.assets.writeWorkspaceFile(
          chat.id,
          file.name,
          await this.assets.read(file.asset),
          owner,
          { mediaType: file.mediaType }
        )
        assets.set(file.asset.id, imported.asset.id)
      }
      function remapAssets(value) {
        if (Array.isArray(value)) return value.map(remapAssets)
        if (value && typeof value === 'object') {
          if (Object.keys(value).length === 1 && assets.has(value.id))
            return { id: assets.get(value.id) }
          return Object.fromEntries(
            Object.entries(value).map(([key, item]) => [key, remapAssets(item)])
          )
        }
        return value
      }
      await this.store.save(remapAssets(chat))
    })
    return chat.id
  }
}
