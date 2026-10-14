import Ajv from 'ajv'
import { copy, requireValue, fingerprint, SerialQueue, SnapshotBus } from '../core/support.js'

/** @typedef {import('../../shared/contracts/tools.js').InteractionRequest} InteractionRequest */
/** @typedef {import('../../shared/contracts/tools.js').InteractionCompletion} InteractionCompletion */
/** @typedef {import('../conversation/interface.js').InteractionRegistration} InteractionRegistration */
/**
 * @typedef {object} ToolEntry
 * @property {import('../../shared/contracts/tools.js').ToolDefinition} definition 公开的工具声明。
 * @property {boolean} [frontend] 是否依赖前端交互；此类工具只能使用 async。
 * @property {(access: {chatId: string, interactions: Interactions, reminders?: ReminderWriter}) => import('../../shared/contracts/tools.js').AgentTool['execute']} bind 为本轮绑定窄能力，不交付完整后端。
 */
/**
 * @typedef {object} InteractionRecord
 * @property {InteractionRequest} request 固定的问题与来源。
 * @property {InteractionRegistration} registration 后端核准的资格。
 * @property {string} material 同一登记身份的材料摘要。
 * @property {InteractionCompletion | null} completion 第一个成功登记的终态，用于幂等确认或拒绝其他答案。
 */
/** @typedef {{revision: number, pending: Array<InteractionRequest & {chatId: string}>}} InteractionSnapshot 未完成且仍有效的问题快照。 */

/** 工具目录统一依赖、配置及权限检查；世界书工具由范围绑定注入，不受普通开关替代。 */
export class ToolCatalog {
  /**
   * 绑定工具目录及按 Chat 创建的能力。
   * @param {object} options 宿主依赖。
   * @param {ToolEntry[]} [options.entries] 内置工具实现及声明。
   * @param {Interactions} options.interactions 交互登记能力。
   * @param {(chatId: string) => ReminderWriter} options.reminders 已限定目标 Chat 的提醒能力。
   */
  constructor({ entries = [], interactions, reminders }) {
    this.entries = new Map(entries.map((entry) => [entry.definition.name, entry]))
    this.interactions = interactions
    this.reminders = reminders
  }

  /**
   * 列出工具目录中可供选择的工具及其声明。
   * @returns {Promise<import('../../shared/contracts/tools.js').ToolDefinition[]>} 可选择的工具定义；不执行工具。
   */
  async list() {
    return [...this.entries.values()].map((entry) => copy(entry.definition))
  }

  /**
   * 将对话选择的工具及其依赖解析为本次可执行的工具集合。
   * 按 Chat 绑定窄能力，拒绝越界读取与任意指定写路径；一次固定依赖闭包，不在 run 中读全局选择。
   * 所有工具都记录调用/结果，不提供 visibility 或隐藏通知分支。
   * 需要前端交互的工具必须声明 async；sync 可以等待后端异步 I/O，但不能等待前端交互结果。
   * 非空 queryWorldbook 仅在本轮允许 queryTools 时由 Conversation 提供，用来额外注入世界书工具；普通 selection 不替代该开关。
   * 世界书工具包装固定 origin.kind=tool-query，context 来自 execute 的 ToolExecutionContext；模型参数不含来源或范围。
   * 只将返回知识转为 ToolOutcome；失败沿用 Problem 处理，不重复收集诊断，不把 trace/source 交给模型。
   * @param {string} chatId 唯一作用对话。
   * @param {import('../../shared/contracts/settings.js').ToolSelection} selection 用户选择。
   * @param {import('../../shared/contracts/tools.js').ToolAccessScope} access 宿主已授权范围。
   * @param {import('../conversation/worldbook-query-interface.js').BoundWorldbookQuery | null} queryWorldbook 本轮查询窄能力，未启用查询工具时为 null，不可回退读取全局世界书配置。
   * @returns {Promise<import('../../shared/contracts/tools.js').AgentTool[]>} 后端可执行材料，由装配提供具体函数。
   */
  async resolve(chatId, selection, access, queryWorldbook) {
    const ordered = []
    const visited = new Set()
    const visiting = new Set()
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
    if (queryWorldbook)
      result.push({
        name: 'query_worldbook',
        description: '查询本轮已授权世界书',
        mode: 'sync',
        parametersSchema: {
          type: 'object',
          properties: {
            perspectiveCharacterId: { type: 'string' },
            kind: { enum: ['event', 'thought', 'relation', 'setting'] },
            text: { type: 'string' },
            tokenBudget: { type: 'integer', minimum: 1 }
          },
          required: ['perspectiveCharacterId', 'kind', 'text', 'tokenBudget'],
          additionalProperties: false
        },
        execute: async (parameters, context, signal) => ({
          status: 'succeeded',
          value: await queryWorldbook(parameters, { kind: 'tool-query', context }, signal)
        })
      })
    return result
  }
}

/**
 * 已由装配绑定 Chat 的提醒创建能力，只交给工具，不向页面/Conversations 公共管理入口暴露手动添加。
 * 持久化与所属 Chat 保存串行；同调用去重，新 Turn 的重新生成可创建新的提醒。
 */
export class ReminderWriter {
  /**
   * 绑定限定目标 Chat 的提醒写入能力。
   * @param {(options: {dueAt: string, instruction: string}, source: import('../../shared/contracts/tools.js').ToolExecutionContext) => Promise<import('../../shared/contracts/conversation.js').Reminder>} write 带来源校验的持久化入口。
   */
  constructor(write) {
    this.write = write
  }

  /**
   * 为绑定的对话创建并保存一条到期后触发角色回复的提醒。
   * 新提醒分配全应用唯一 UUID，同一次工具调用的通信重试返回同一提醒。
   * @param {{ dueAt: string, instruction: string }} options 到期时间与内容。
   * @param {import('../../shared/contracts/tools.js').ToolExecutionContext} source 创建调用身份。
   * @returns {Promise<import('../../shared/contracts/conversation.js').Reminder>} 已持久化提醒。
   */
  async create(options, source) {
    return this.write(options, source)
  }
}

/**
 * 宿主交互的窄接口；实例绑定 Chat 与受控宿主。可靠登记时建立 request → Turn/call/组的映射。
 * 前端交互工具统一使用 async；最终结果只路由到 Conversations.acceptInteractionResult，不继续原 Loop。
 * 重启不恢复未完成交互；旧宿主回调没有资格时拒收，不根据 payload 重建资格。
 */
export class Interactions {
  /**
   * 绑定交互资格校验和结果入队能力；连接数不影响交互是否存活。
   * @param {object} options 仅由可信后端装配提供。
   * @param {(request: InteractionRequest) => Promise<InteractionRegistration | null>} options.qualify 核对真实工具调用。
   * @param {(registration: InteractionRegistration) => boolean | Promise<boolean>} options.isValid 查询原组是否有效。
   * @param {(completion: InteractionCompletion, registration: InteractionRegistration, request: InteractionRequest) => Promise<void>} options.accept 提交结果，不等待自动轮完成。
   * @param {Record<string, {requestSchema: object, resultSchema: object}>} [options.kinds] 问题和成功答案的 JSON Schema。
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
   * 查询未完成且仍有资格的交互；断线或没有客户端不会丢请求。
   * @returns {Promise<InteractionSnapshot>} 完整待处理问题，不要求客户端持有旧事件。
   */
  async list() {
    const pending = []
    for (const record of this.requests.values())
      if (!record.completion && (await this.isValid(record.registration)))
        pending.push({ ...copy(record.request), chatId: record.registration.chatId })
    return { revision: this.revision, pending }
  }
  /** 资格变化也重新投影完整列表，客户端无需自行推导哪些问题已过期。 */
  publish() {
    this.revision += 1
    this.list()
      .then((snapshot) => this.bus.emit('pending', snapshot))
      .catch(() => {})
  }
  /**
   * 观察待处理问题列表。
   * @param {(snapshot: InteractionSnapshot) => void} listener 完整快照接收者。
   * @returns {Promise<() => void>} 取消观察，不撤销问题。
   */
  async observe(listener) {
    return this.bus.subscribe('pending', listener, await this.list())
  }
  /** 进程退出时撤销内存交互，不将未完成资格恢复到下一次运行。
   * @returns {void} 记录和观察者已释放。
   */
  close() {
    this.requests.clear()
    this.bus.clear()
  }

  /**
   * 登记抽签、问答等前端交互请求，可靠受理后立即交还当前工具调用。
   * 受理不等待展示端连接、交互播放或用户回答；待处理请求由宿主在可用时展示。
   * @param {import('../../shared/contracts/tools.js').InteractionRequest} request 固定请求身份与交互材料。
   * @param {AbortSignal} signal 当前执行的取消信号。
   * @returns {Promise<import('../../shared/contracts/conversation.js').ToolOutcome>} 可靠受理后返回 accepted；受理前失败返回工具失败，不冒充交互已完成。
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
   * 接收宿主交互的最终结果，转交对话模块登记并安排后续回复。
   * 校验已登记请求与宿主来源，重复完成幂等；失败或取消不会制造后续回复。
   * @param {import('../../shared/contracts/tools.js').InteractionCompletion} completion 宿主最终回调。
   * @returns {Promise<void>} 已向对话模块登记结果，不代表后续 Turn 已受理。
   */
  async complete(completion) {
    // 竞争答案在同一身份下串行裁定；只有 accept 成功才锁定答案，入队失败允许相同答案重试。
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
