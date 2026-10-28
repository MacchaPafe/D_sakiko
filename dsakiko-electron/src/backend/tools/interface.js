import Ajv from 'ajv'
import { copy, requireValue, fingerprint, SerialQueue, SnapshotBus } from '../core/support.js'

/** @typedef {import('../../shared/contracts/tools.js').InteractionRequest} InteractionRequest */
/** @typedef {import('../../shared/contracts/tools.js').InteractionCompletion} InteractionCompletion */
/** @typedef {import('../conversation/interface.js').InteractionRegistration} InteractionRegistration */

/**
 * @typedef {object} ToolEntry
 * @property {import('../../shared/contracts/tools.js').ToolDefinition} definition 工具的公开声明信息。
 * @property {boolean} [frontend] 是否依赖前端用户交互（依赖前端交互的工具必须采用 async 模式）。
 * @property {(access: {chatId: string, interactions: Interactions, reminders?: ReminderWriter}) => import('../../shared/contracts/tools.js').AgentTool['execute']} bind 为指定对话生成工具执行函数的工厂函数。
 */

/**
 * @typedef {object} InteractionRecord
 * @property {InteractionRequest} request 交互请求内容与来源信息。
 * @property {InteractionRegistration} registration 后端核准的交互登记凭证。
 * @property {string} material 请求内容的哈希摘要，用于校验重复请求是否一致。
 * @property {InteractionCompletion | null} completion 已完成的交互结果（未完成时为 null）。
 */

/** @typedef {{revision: number, pending: Array<InteractionRequest & {chatId: string}>}} InteractionSnapshot 当前待处理的前端交互请求快照。 */

/**
 * 工具目录模块，负责管理可用工具定义，并为对话解析出可执行的工具列表。
 */
export class ToolCatalog {
  /**
   * 创建一个工具目录。
   * @param {object} options 创建选项。
   * @param {ToolEntry[]} [options.entries] 注册的工具条目列表（包含工具声明与执行绑定函数）。
   * @param {Interactions} options.interactions 前端交互管理器，供需要用户交互的工具发起请求。
   * @param {(chatId: string) => ReminderWriter} options.reminders 为指定对话创建提醒写入器的工厂函数。
   */
  constructor({ entries = [], interactions, reminders }) {
    this.entries = new Map(entries.map((entry) => [entry.definition.name, entry]))
    this.interactions = interactions
    this.reminders = reminders
  }

  /**
   * 列出工具目录中所有可供选择的工具定义。
   * @returns {Promise<import('../../shared/contracts/tools.js').ToolDefinition[]>} 工具定义列表。
   */
  async list() {
    return [...this.entries.keys()].sort().map((name) => copy(this.entries.get(name).definition))
  }

  /**
   * 根据对话选择的工具列表与权限范围，解析出本轮生成可调用的工具实例集合。
   * @param {string} chatId 当前对话 ID。
   * @param {import('../../shared/contracts/settings.js').ToolSelection} selection 对话中启用的工具选择配置。
   * @param {import('../../shared/contracts/tools.js').ToolAccessScope} access 当前宿主环境允许的权限范围。
   * @param {import('../conversation/worldbook-query-interface.js').BoundWorldbookQuery | null} queryWorldbook 已绑定本轮范围的世界书查询函数；未启用世界书查询工具时传 null。
   * @returns {Promise<import('../../shared/contracts/tools.js').AgentTool[]>} 可直接供 Agent 调用的工具列表。
   */
  async resolve(chatId, selection, access, queryWorldbook) {
    const ordered = []
    const visited = new Set()
    const visiting = new Set()
    // 通过深度优先遍历自动补齐工具依赖，并检测是否存在循环依赖。
    const visit = (name) => {
      if (visited.has(name)) return
      requireValue(!visiting.has(name), 'tool_dependency_cycle')
      const entry = this.entries.get(name)
      requireValue(entry, 'tool_unavailable')
      requireValue(
        entry.definition.requiredCapabilities.every((capability) =>
          access.hostCapabilities.includes(capability)
        ),
        'tool_forbidden'
      )
      // 为什么要求依赖前端交互的工具必须是 async 模式：
      // 用户何时回答不确定（甚至可能不在线），采用 async 模式可让当前轮次在登记问题后先行结束，待用户回答后再作为新轮次继续对话，避免长期挂起 LLM 请求。
      requireValue(
        ['sync', 'async'].includes(entry.definition.mode) &&
          (!entry.frontend || entry.definition.mode === 'async'),
        'invalid_tool_mode'
      )
      visiting.add(name)
      entry.definition.dependencies.forEach(visit)
      visiting.delete(name)
      visited.add(name)
      ordered.push(entry)
    }
    selection.enabled.forEach(visit)
    const result = ordered.map((entry) => ({
      name: entry.definition.name,
      description: entry.definition.description,
      parametersSchema: copy(entry.definition.parametersSchema),
      mode: entry.definition.mode,
      execute: entry.bind({
        chatId,
        interactions: this.interactions,
        reminders: this.reminders?.(chatId)
      })
    }))
    // 世界书查询工具由当前对话的世界书配置决定是否注入，不混在普通工具勾选列表中。
    if (queryWorldbook)
      result.push({
        name: 'query_worldbook',
        description:
          '当对话涉及拿不准的作品设定、过往事件、人物想法或人际关系时，按指定角色的认知视角检索本轮已授权的世界书知识。',
        mode: 'sync',
        parametersSchema: {
          type: 'object',
          properties: {
            character: {
              type: 'string',
              minLength: 1,
              description:
                '查询视角的角色名（必须使用本轮参与角色的角色名，仅检索该角色可知范围内的信息）。'
            },
            kind: {
              enum: ['event', 'thought', 'relation', 'setting'],
              description:
                '检索的知识类别：event（过往事件与经历）、thought（人物内心想法与态度）、relation（人物关系与称呼）、setting（世界观与背景设定）。'
            },
            text: {
              type: 'string',
              minLength: 1,
              description: '要检索的关键词、问题或相关主题描述。'
            },
            tokenBudget: {
              type: 'integer',
              minimum: 1,
              maximum: 4000,
              description:
                '本次检索返回内容的最大 Token 预算（建议按需填写 200 到 1000 之间的正整数）。'
            }
          },
          required: ['character', 'kind', 'text', 'tokenBudget'],
          additionalProperties: false
        },
        execute: async (parameters, context, signal) => ({
          status: 'succeeded',
          value: await queryWorldbook(parameters, { kind: 'tool-query', context }, signal)
        })
      })
    // 勾选顺序和依赖遍历顺序不应改变模型输入；使用不受系统语言影响的名称字典序。
    return result.sort((left, right) => {
      if (left.name < right.name) return -1
      if (left.name > right.name) return 1
      return 0
    })
  }
}

/**
 * 绑定到特定对话的提醒写入器，仅供工具在对话过程中创建定时提醒。
 */
export class ReminderWriter {
  /**
   * 创建一个提醒写入器。
   * @param {(options: {dueAt: string, instruction: string}, source: import('../../shared/contracts/tools.js').ToolExecutionContext) => Promise<import('../../shared/contracts/conversation.js').Reminder>} write 实际执行提醒创建与持久化的回调函数。
   */
  constructor(write) {
    this.write = write
  }

  /**
   * 为当前绑定的对话创建一条到期后触发角色主动回复的提醒。
   * @param {{ dueAt: string, instruction: string }} options 提醒的到期时间（ISO 字符串）与提示指令。
   * @param {import('../../shared/contracts/tools.js').ToolExecutionContext} source 触发创建的工具调用上下文。
   * @returns {Promise<import('../../shared/contracts/conversation.js').Reminder>} 创建并保存成功的提醒对象。
   */
  async create(options, source) {
    return this.write(options, source)
  }
}

/**
 * 前端交互管理器，负责登记异步工具向用户发起的交互请求（如提问、选择）并收集用户的最终回答。
 */
export class Interactions {
  /**
   * 创建一个前端交互管理器。
   * @param {object} options 创建选项。
   * @param {(request: InteractionRequest) => Promise<InteractionRegistration | null>} options.qualify 校验交互请求是否来自真实且仍在运行中的工具调用。
   * @param {(registration: InteractionRegistration) => boolean | Promise<boolean>} options.isValid 检查已登记的交互请求所属的对话轮次组当前是否仍然有效。
   * @param {(completion: InteractionCompletion, registration: InteractionRegistration, request: InteractionRequest) => Promise<void>} options.accept 将用户完成的交互结果提交给对话模块的回调函数。
   * @param {Record<string, {requestSchema: object, resultSchema: object}>} [options.kinds] 各交互类型的请求参数与返回结果 JSON Schema 定义。
   */
  constructor({ qualify, isValid, accept, kinds = {} }) {
    Object.assign(this, { qualify, isValid, accept, kinds })
    /** @type {Map<string, InteractionRecord>} */
    this.requests = new Map()
    this.queue = new SerialQueue()
    this.bus = new SnapshotBus()
    this.revision = 0
    this.validator = new Ajv({ strict: false })
  }

  /**
   * 获取当前所有未完成且依然有效的前端交互请求列表。
   * @returns {Promise<InteractionSnapshot>} 待处理交互请求快照。
   */
  async list() {
    const pending = []
    for (const record of this.requests.values())
      if (!record.completion && (await this.isValid(record.registration)))
        pending.push({ ...copy(record.request), chatId: record.registration.chatId })
    return { revision: this.revision, pending }
  }

  /**
   * 重新计算并向所有订阅者推送最新的待处理交互列表。
   */
  publish() {
    this.revision += 1
    this.list()
      .then((snapshot) => this.bus.emit('pending', snapshot))
      .catch(() => {})
  }

  /**
   * 订阅待处理交互列表的变更通知（订阅时会立即推送一次当前列表）。
   * @param {(snapshot: InteractionSnapshot) => void} listener 接收最新待处理交互快照的回调函数。
   * @returns {Promise<() => void>} 取消订阅的函数。
   */
  async observe(listener) {
    return this.bus.subscribe('pending', listener, await this.list())
  }

  /**
   * 清空内存中的所有交互记录与订阅者。
   * @returns {void}
   */
  close() {
    this.requests.clear()
    this.bus.clear()
  }

  /**
   * 登记一个新的前端交互请求（登记成功后立即返回 accepted，不阻塞等待用户作答）。
   * @param {import('../../shared/contracts/tools.js').InteractionRequest} request 交互请求标识与内容。
   * @param {AbortSignal} [signal] 当前工具调用的取消信号。
   * @returns {Promise<import('../../shared/contracts/conversation.js').ToolOutcome>} 工具受理结果（status 为 'accepted'）。
   */
  async request(request, signal) {
    signal?.throwIfAborted()
    return this.queue.run(request.requestId, async () => {
      const material = fingerprint(request)
      const previous = this.requests.get(request.requestId)
      if (previous) {
        requireValue(previous.material === material, 'request_conflict')
        return { status: 'accepted', requestId: request.requestId }
      }
      const registration = await this.qualify(request)
      requireValue(registration, 'interaction_expired')
      const kind = this.kinds[request.kind]
      requireValue(kind, 'interaction_kind_unavailable')
      requireValue(
        this.validator.compile(kind.requestSchema)(request.payload),
        'invalid_interaction'
      )
      this.requests.set(request.requestId, {
        request: copy(request),
        registration,
        material,
        completion: null
      })
      this.publish()
      return { status: 'accepted', requestId: request.requestId }
    })
  }

  /**
   * 提交前端交互的完成结果，并将其转交给对话模块处理后续回复。
   * @param {import('../../shared/contracts/tools.js').InteractionCompletion} completion 用户交互的完成状态与回答内容。
   * @returns {Promise<void>}
   */
  async complete(completion) {
    // 多个客户端可能同时提交回答，按 requestId 串行化以确保只采纳第一个成功入队的回答；若相同的回答重复提交则幂等放行。
    return this.queue.run(completion.requestId, async () => {
      const record = this.requests.get(completion.requestId)
      requireValue(record && (await this.isValid(record.registration)), 'interaction_expired')
      if (record.completion) {
        requireValue(
          fingerprint(record.completion) === fingerprint(completion),
          'interaction_resolved',
          '该交互已由其他客户端完成。'
        )
        return
      }
      requireValue(['succeeded', 'failed', 'cancelled'].includes(completion.status))
      if (completion.status === 'succeeded')
        requireValue(
          this.validator.compile(this.kinds[record.request.kind].resultSchema)(completion.value),
          'invalid_interaction_result'
        )
      await this.accept(copy(completion), copy(record.registration), copy(record.request))
      record.completion = copy(completion)
      this.publish()
    })
  }
}
