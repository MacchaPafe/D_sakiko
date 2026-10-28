/** 工具执行身份和交互数据；轮次组及有效资格由 Conversation 内存管理，不能由宿主回报重建。 */

/**
 * @typedef {object} ToolExecutionContext
 * @property {string} turnId 一次 Agent Loop 身份。
 * @property {string} assistantMessageId 原响应身份。
 * @property {string} callId 原调用身份；同一次调用的通信重试复用。
 */

/**
 * @typedef {object} AgentTool
 * @property {string} name 工具名称。
 * @property {string} description 用途。
 * @property {import('./common.js').Metadata} parametersSchema 参数 JSON Schema。
 * @property {'sync' | 'async'} mode sync 等后端实际结果再继续当前 Loop，内部可使用 Promise；前端交互工具统一 async，可靠受理后返回 accepted，最终结果进入后续 Turn。
 * @property {(parameters: import('./common.js').Metadata, context: ToolExecutionContext, signal: AbortSignal) => Promise<import('./conversation.js').ToolOutcome>} execute 已绑定 Chat、授权及本次范围的 Node 函数，不传输或持久化。
 */

/**
 * @typedef {object} ToolDefinition
 * @property {string} name 稳定工具名。
 * @property {string} description 工具用途。
 * @property {string[]} dependencies 依赖工具；解析成闭包，缺少依赖或循环明确报错。
 * @property {'sync' | 'async'} mode 后端完成并继续当前 Loop，或可靠登记后在后续 Turn 回流；依赖前端交互的工具必须为 async。
 * @property {import('./common.js').Metadata} parametersSchema 完整参数结构，具体 Python 工具清单由后续适配登记。
 * @property {import('./common.js').Metadata} resultSchema 成功结果结构；失败和中断不要求符合成功格式。
 * @property {string[]} requiredCapabilities 外部服务和宿主授权要求，不通过名称暗含权限。
 */

/**
 * @typedef {object} ToolAccessScope
 * @property {string[]} readableRoots 宿主授权的读取根，不允许调用方或模型自行扩权。
 * @property {string} outputLocation 后端指定写入位置的不透明身份，模型参数不能覆盖路径。
 * @property {string[]} hostCapabilities 已授予的网络、提醒、交互等能力。
 */

/**
 * @typedef {object} InteractionRequest 仅用于 async 交互，不提供等待前端结果后继续当前 Loop 的模式。
 * @property {string} requestId 调用方为该调用固定的去重身份，重试同 ID/材料。
 * @property {ToolExecutionContext} source 原执行关联；由已绑定的工具能力校验。
 * @property {string} kind 已登记的宿主交互种类。
 * @property {import('./common.js').JsonValue} payload 问题或展示材料。
 */

/**
 * @typedef {{kind: 'answered', answer: string} | {kind: 'skipped'}} QuestionResult
 * 问题交互的成功结果；回答可来自候选项或自由输入（1–4000 字符且非纯空白）。
 * skipped 表示用户主动跳过，不代表任何候选项；系统取消仍使用外层 cancelled。
 */

/**
 * @typedef {{ requestId: string } & ({ status: 'succeeded', value: import('./common.js').JsonValue }
 *   | { status: 'failed', problem: import('./common.js').Problem } | { status: 'cancelled' })} InteractionCompletion
 * question 类型成功时 value 必须为 QuestionResult，不接受旧字符串结果。
 */

export {}
