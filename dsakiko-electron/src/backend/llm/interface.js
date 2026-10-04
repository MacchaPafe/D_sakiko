/* eslint no-unused-vars: ["error", { "args": "none" }] -- 接口原型保留参数名称，方法体刻意留空。 */

/** @typedef {import('../../shared/contracts/common.js').Metadata} Metadata */
/** @typedef {import('../../shared/contracts/common.js').JsonValue} JsonValue */

/**
 * 从 Chat 的固定身份快照提取的临时输入，不读取目录，也不包含头像、来源编号或角色能力。
 * @typedef {object} AgentUserPersona
 * @property {string} displayName 用户在本 Chat 中扮演的名称。
 * @property {string} description 本 Chat 固定的人格文本。
 */

/**
 * @typedef {object} AgentCharacter
 * @property {string} id 允许发言的角色身份。
 * @property {string} displayName 显示名。
 * @property {string} description 角色描述快照。
 * @property {import('../../shared/contracts/presentation.js').PerformanceCatalog} performances 可选的逻辑演出语义。
 */

/**
 * Conversation 在 run 前从 Message 提取的临时输入，不持久化，也不需要单独的运行时类。
 * JSDoc 不会自动裁剪字段；提取由 Conversation 的私有函数完成，Agent 负责提示词、上下文预算和供应商格式。
 * 输入按只读约定使用；需要隔离修改的嵌套数据应复制，不能借此修改原始消息。
 * reasoning 保留类别标记，不作为普通发言拼入提示词；是否回传和如何组织由 Agent 按供应商协议决定。
 * 这是语义输入投影，仅凭文本和排列顺序不保证能重建原始供应商请求。
 * @typedef {object} ContextMessage
 * @property {'user' | 'character' | 'tool' | 'reasoning'} kind 上下文记录类别；reasoning 必须与普通发言区别处理。
 * @property {string | null} speakerId 发言身份。
 * @property {string} text 上下文文本。
 * @property {import('../../shared/contracts/conversation.js').Attachment[]} attachments 允许进入模型上下文的附件。
 */

/**
 * 临时输出，不持久化；Conversation 接收后补齐消息身份及存档字段，构造正式 Message。
 * @typedef {object} GeneratedLine
 * @property {'character'} kind 角色台词，与 reasoning 区分，供 Conversation 决定是否合成和演出。
 * @property {string} speakerId 必须来自本次请求提供的角色。
 * @property {string} text 完整句子。
 * @property {string | null} translation 翻译。
 * @property {string | null} emotion 情绪语义。
 * @property {import('../../shared/contracts/presentation.js').PerformanceSelection} performance 逻辑演出选择。
 */

/**
 * 一段模型实际返回的 reasoning，不要求角色身份或演出字段；由 Conversation 转为正式 Message。
 * 第一版在非流式响应完成后交付整段文本，不交付 token 增量；未返回的内容不推测、不补造。
 * @typedef {object} GeneratedReasoning
 * @property {'reasoning'} kind 推理记录。
 * @property {string} text 非空推理正文；没有 reasoning 时不创建记录。
 */

/**
 * 按 kind 区分的临时生成消息；不是新的持久化格式或运行时类。
 * @typedef {GeneratedLine | GeneratedReasoning} GeneratedMessage
 */

/**
 * 调用方已限定权限和作用范围的工具；执行函数只在 Node 内使用，不进入传输协议。
 * @typedef {object} AgentTool
 * @property {string} name 工具名称。
 * @property {string} description 工具用途。
 * @property {Metadata} parametersSchema 工具参数的 JSON Schema。
 * @property {'visible' | 'hidden'} visibility visible 交付调用记录供聊天展示；hidden 只参与 Agent 执行及内部诊断，不产生聊天消息。
 * @property {(parameters: Metadata, signal: AbortSignal) => Promise<JsonValue>} execute 已绑定作用范围的执行函数。
 */

/**
 * @typedef {object} GenerationRequest
 * @property {import('../../shared/contracts/conversation.js').DialogueMode} mode 本次生成的编排方式。
 * @property {AgentCharacter[]} characters 本轮角色快照；编剧模式一次生成有序的二人台词。
 * @property {AgentUserPersona | null} userPersona 用户身份的提示词材料；null 表示无自定义人格，不加入允许生成台词的 characters。
 * @property {ContextMessage[]} history 本次输入之前的上下文投影，不是完整 Chat。
 * @property {{ kind: 'user', message: ContextMessage } | { kind: 'reminder' | 'continuation', instruction: string }} input 本次触发输入；内部提醒不伪装成用户发言。
 * @property {string | null} summary 已保存的上下文摘要。
 * @property {{ connectionId: string, model: string, temperature: number, contextTokenBudget: number }} model 模型请求设置；凭据由连接配置解析。
 * @property {string[]} supplementalContext 上游提供的补充上下文；第一版无需实现世界书检索。
 * @property {AgentTool[]} tools 本次允许调用的工具。
 */

/**
 * reasoning、过渡台词和可见工具记录均按生成顺序交付，等待 Conversation 接受后再继续；它负责保存和向界面发布。
 * 同一次 run 内依次等待这些通知，不并行发送，以保留 reasoning → 工具 → reasoning → 台词的顺序。
 * reasoning 不依赖台词 JSON 校验成功；后续格式重试、失败或取消不撤销已经接受的记录，也不重复交付旧记录。
 * 同一 callId 的多次通知更新同一条工具消息；这不是获取 Agent 内部队列的接口。
 * 工具调用前交付 running，结束后交付终态；通知接收失败时停止本次生成，不因此重新执行工具。
 * Observer 不会看到最终的执行结果（生成的台词）。这只是用于通知 Agent 运行中间状态的。
 * @typedef {object} GenerationObserver
 * @property {(messages: GeneratedMessage[]) => Promise<void>} onIntermediate 接收有序的 reasoning 或已完整校验的过渡台词；包含最终响应的 reasoning，但不包含最终台词。
 * @property {(record: import('../../shared/contracts/conversation.js').ToolCallRecord) => Promise<void>} onToolActivity 交付可见工具调用的参数、状态及结果或错误；隐藏工具不经过此通知。
 */

/**
 * 仅返回尚未交付的最终台词；reasoning 已经通过 onIntermediate 接受，不在结果中重复返回。
 * 取消结果不撤销已提交的 reasoning、过渡台词和工具记录。
 * @typedef {{ status: 'completed', lines: GeneratedLine[], summary: string | null }
 *   | { status: 'cancelled' }} GenerationResult
 */

/**
 * Agent 模块：构造提示、控制上下文长度、生成摘要、请求模型、执行工具循环，校验完整输出。
 * 每次 run 独立；第一版不输出 token 流，reasoning 按完整段交付，台词按完整通过校验的批次交付。
 * 每次响应中的 reasoning 先通过 onIntermediate 交付；最终台词只通过返回值交付，不重复通知。
 * 重试不得重复提交已接受的批次或盲目重复执行有副作用的工具。
 * 发送附件时沿用本轮连接快照：file-api 通过绑定同一连接的 RemoteFiles 准备引用，inline 在消息组装时读取并编码。
 * 先检查目标模型与协议是否支持附件类型；预上传结果不能替代发送前的连接核对，也不能假设未知模型支持 base64 图片。
 * 厂商明确拒绝文件引用时，只失效实际使用的旧引用并重新准备；是否安全重试请求由本模块判断，不重放已执行工具。
 *
 * 需要：本次上下文、角色描述、逻辑演出选项、模型配置和已限定范围的工具。
 * 不需要：Chat 的可修改对象、前后台状态、sequence、语音任务、存储位置、播放完成通知。
 * 生成结果是否入档、如何合成和演出，由对话模块决定。
 * 这是审查用接口，方法未实现。
 */
export class Agent {
  /**
   * 执行一次独立生成。取消后不再提交新批次，并尽力取消模型请求和工具执行。
   * 已完成的外部工具副作用不会自动回滚；对话模块仍须拒收旧执行的迟到回调。
   * @param {GenerationRequest} request 不可变的生成输入。
   * @param {GenerationObserver} observer 本地批次接收者，不是跨进程回调对象。
   * @param {AbortSignal} signal 本次执行的取消信号。
   * @returns {Promise<GenerationResult>} 最终批次或取消结果；不可恢复的请求、格式错误拒绝 Promise。
   */
  async run(request, observer, signal) {}
}
