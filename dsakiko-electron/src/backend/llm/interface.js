/* eslint no-unused-vars: ["error", { "args": "none" }] -- 审查声明，方法体刻意留空。 */

/**
 * @typedef {object} AgentCharacter
 * @property {string} id 允许发言的角色。
 * @property {string} formId 本次形态。
 * @property {string} displayName 名称快照。
 * @property {string} description 形态描述及语气材料。
 * @property {import('../../shared/contracts/presentation.js').PerformanceCatalog} performances 本次允许的逻辑演出目录。
 */

/** @typedef {Pick<import('../../shared/contracts/conversation.js').CharacterLine, 'id' | 'speakerId' | 'formId' | 'text' | 'translation' | 'emotion' | 'performance'>} ContextLine 历史形态可能因导入缺失而未解析，不伪造形态。 */

/**
 * 临时语义投影保留 assistant 分组、reasoning 类别和 callId；不以扁平展示反推历史。
 * 缺图在所属用户内容中追加不可读取说明并保留问题，不伪造图像；附件可用性涵盖本次和历史材料。
 * 摘要投影仅取最后有效 abstract 及其后文，原存档不裁掉。
 * @typedef {{ messageId: string } & ({ kind: 'user', source: import('../../shared/contracts/conversation.js').UserMessage['source'], text: string,
 *   attachments: import('../../shared/contracts/conversation.js').Attachment[], unavailableAttachments: string[] }
 *   | { kind: 'assistant', parts: Array<import('../../shared/contracts/conversation.js').ReasoningPart
 *       | import('../../shared/contracts/conversation.js').ToolCallPart | { kind: 'dialogue', lines: ContextLine[] }>, protocol: import('../../shared/contracts/common.js').Metadata }
 *   | { kind: 'tool-result', callId: string, toolName: string, outcome: import('../../shared/contracts/conversation.js').ToolOutcome }
 *   | { kind: 'abstract', text: string })} ContextMessage
 */

/** @typedef {Extract<ContextMessage, { kind: 'user' }>} ContextUserMessage 仅输入消息投影。 */

/**
 * 保留 Turn 边界，预算裁剪不能拆散响应和对应结果；不传原 Chat 或可写消息对象。
 * @typedef {object} ContextTurn
 * @property {string} turnId 来源 Turn 身份。
 * @property {ContextMessage[]} messages 已应用最后有效摘要规则的有序投影。
 */

/** @typedef {Required<Omit<import('../../shared/contracts/settings.js').GenerationOptions, 'connectionId' | 'modelId'>>} ResolvedGenerationOptions 已解析的生成值，连接与模型仅由 connection 指定。 */

/**
 * @typedef {object} GeneratedLine
 * @property {string} id 本次输出稳定句身份，重试不能为已交付句重新生成 ID。
 * @property {string} speakerId 本轮角色。
 * @property {string} formId 本轮形态。
 * @property {string} text 完整合法正文。
 * @property {string | null} translation 翻译。
 * @property {string | null} emotion 合法情绪。
 * @property {import('../../shared/contracts/presentation.js').PerformanceSelection} performance 本轮目录中的演出选择。
 */

/**
 * 同一次非流式模型响应的有序增量：先接受实际 reasoning/调用，台词完成校验后再追加到同一容器。
 * appendParts 只追加此前未接受的稳定身份；一份响应仅有一个 responseId，不重复交付最终台词。
 * 修复请求是新响应；不能把修复输出伪装成原响应或把失败原文当台词。不是 token 流接口。
 * @typedef {object} ResponseUpdate
 * @property {string} responseId 稳定响应身份，作为 AssistantMessage.id。
 * @property {Array<import('../../shared/contracts/conversation.js').ReasoningPart
 *   | import('../../shared/contracts/conversation.js').ToolCallPart | { id: string, kind: 'dialogue', lines: GeneratedLine[] }>} appendParts 待接受的内容。
 * @property {import('../../shared/contracts/common.js').Metadata} protocol 必要协议材料，限脱敏白名单。
 */

/**
 * @typedef {object} GenerationRequest
 * @property {string} turnId 本次已受理 UUID，用于工具调用去重和来源关联，不授权读取 Chat。
 * @property {import('../../shared/contracts/conversation.js').DialogueMode} mode 编排方式。
 * @property {AgentCharacter[]} characters 一次固定的角色材料。
 * @property {{ displayName: string, description: string } | null} userPersona 从 Chat 固定人格提取，不包含头像/来源/能力。
 * @property {ContextTurn[]} history 当前输入之前的轮次投影，保留完整裁剪单位。
 * @property {{ kind: 'message', message: ContextUserMessage } | { kind: 'scenario' }} input 手动/内部消息投影或主动情景命令。
 * @property {import('../../shared/contracts/settings.js').ScenarioSettings | null} scenario 固定情境，拼入 User Prompt，不伪造存档用户输入。
 * @property {ResolvedGenerationOptions} options 已完整解析的生成设置，禁止下层再读全局覆盖。
 * @property {import('../../shared/contracts/models.js').ResolvedModelConnection} connection 后端临时连接快照，与附件准备使用同一作用域。
 * @property {string[]} supplementalContext 允许直接供模型使用的内容，不含检索诊断。
 * @property {import('../../shared/contracts/tools.js').AgentTool[]} tools 已解析依赖和权限且固定的工具，不隐式注入未选择工具。
 */

/**
 * 依次 await 接受，再继续执行；回调不是可丢弃的 UI 通知。
 * 保存故障后 Conversation 可接受到内存并暂停新受理；拒绝接受时 Agent 停止推进，不重放工具。
 * @typedef {object} GenerationObserver
 * @property {(update: ResponseUpdate) => Promise<void>} onResponse 接受原分组中的 reasoning、调用及合法台词，包含最终台词。
 * @property {(result: import('../../shared/contracts/conversation.js').ToolResultMessage) => Promise<void>} onToolResult 每个调用唯一结果，所有工具统一通知。
 */

/**
 * @typedef {object} TokenUsage
 * @property {number | null} inputTokens 实际输入消耗，供应商未给时 null。
 * @property {number | null} outputTokens 实际输出消耗。
 * @property {number | null} reasoningTokens 实际推理消耗，不能用估算冒充。
 */

/**
 * @typedef {object} ContextInspection
 * @property {number | null} estimatedInputTokens 包含 system、人格、角色、历史、输入、工具、输出 Schema、附件的估值；无法估计为 null。
 * @property {number} reservedOutputTokens 为生成预留的预算。
 * @property {number | null} modelLimit 已知模型上限，未知为 null。
 * @property {string | null} limitSource 目录、用户覆盖或供应商声明来源。
 * @property {'fits' | 'over-budget' | 'unknown'} fit 是否容纳请求。
 * @property {{ enabled: boolean, abstractMessageId: string | null, retainedTurns: number }} compression 当前有效摘要及压缩设置。
 * @property {TokenUsage | null} lastUsage Conversation 可补充最近实际请求消耗，Agent 预览无已知值时为 null，不冒充本次测量。
 * @property {import('../../shared/contracts/common.js').Problem[]} problems 未知能力、资源缺失或预算问题。
 */

/**
 * 压缩输入由 Conversation 在同一操作内固定，不把可改 Chat 传入 Agent。
 * @typedef {object} CompressionRequest
 * @property {ContextTurn[]} history 旧累计摘要和新增的完整可压缩轮次投影，不包含必须保留的末轮。
 * @property {Required<import('../../shared/contracts/settings.js').GenerationOptions>} options 固定预算、摘要提示及模型设置。
 * @property {import('../../shared/contracts/models.js').ResolvedModelConnection} connection 本次连接快照。
 */

/** @typedef {Omit<GenerationRequest, 'turnId' | 'input'> & { input: GenerationRequest['input'] | null }} ContextInspectionRequest 只读检查无需创建 Turn，null 输入只检查历史和配置。 */

/**
 * 超预算先由 Conversation 协调自动压缩；失败保留原文/旧摘要。仍需裁剪时只裁完整旧 Turn，
 * 不拆开 assistant 与对应结果，不删本次输入、必需提示或保留末轮；必需材料仍超限则明确失败。
 * Agent 管提示、单轮预算、模型和工具循环；LLM 适配负责供应商参数与协议转换。
 * 同响应工具按调用顺序串行：sync 等本地完成，block-async 等交互完成，async 等可靠 accepted 后继续。
 * 重试只重试失败请求，不重放已执行工具或重复通知；同调用去重，显式新 Turn 是新执行。
 * 校验 Schema、角色/形态/语言/情绪/演出目录，格式修复有界；失败原文只留诊断，不进入正式上下文或 Speech。
 * 失败稳定类别至少含 timeout/authentication/rate_limit/quota_exhausted/empty_response/invalid_output/unsupported_input。
 * 仅瞬时错误按上限和退避重试；鉴权、额度及无效配置直接失败，保留脱敏 Problem，原始异常只入诊断。
 * 附件能力同时检查历史与本次材料，区分输入能力和上传能力。未知不能推定支持。
 * 文件引用失效只使实际旧引用失效；同连接快照重新准备，再判断重试安全性，不重跑整个 Loop。
 * 停止/失败后缺结果调用交由 Conversation 结算；已 accepted 的交互不产生第二个协议结果。
 * 本文件仅声明，未实现或接入 AI SDK。
 */
export class Agent {
  /**
   * 所有内容都经 observer 接受，返回值不再重复最终台词。取消不撤销已接受过程及外部副作用。
   * @param {GenerationRequest} request 固定输入。
   * @param {GenerationObserver} observer 顺序接收者。
   * @param {AbortSignal} signal 取消信号。
   * @returns {Promise<{ status: 'completed' | 'cancelled', usage: TokenUsage | null }>} Loop 结束，不代表 Turn 已 finished；不可恢复错误拒绝。
   */
  async run(request, observer, signal) {}

  /**
   * 与 run 共用请求准备规则，估计附件开销但不上传、不调用模型/工具、不压缩或写存档。
   * @param {ContextInspectionRequest} request 要检查的材料，不创建运行身份。
   * @returns {Promise<ContextInspection>} 占用和能力诊断。
   */
  async inspectContext(request) {}

  /**
   * 自动压缩也由 Conversation 在当前生成操作内调用并确定插入位置；失败保留原文和旧摘要。
   * @param {CompressionRequest} request 可压缩的完整材料。
   * @param {AbortSignal} signal 取消。
   * @returns {Promise<{ text: string, usage: TokenUsage | null }>} 累计摘要文本，不生成角色台词。
   */
  async compress(request, signal) {}
}
