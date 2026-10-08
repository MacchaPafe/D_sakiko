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
 * @property {'sync' | 'block-async' | 'async'} mode 等待本地完成、交互完成或可靠受理；只有 async 成功受理返回 accepted，其他模式返回实际结果。
 * @property {(parameters: import('./common.js').Metadata, context: ToolExecutionContext, signal: AbortSignal) => Promise<import('./conversation.js').ToolOutcome>} execute 已绑定 Chat、授权及本次范围的 Node 函数，不传输或持久化。
 */

/**
 * @typedef {object} ToolDefinition
 * @property {string} name 稳定工具名。
 * @property {string} description 工具用途。
 * @property {string[]} dependencies 依赖工具；解析成闭包，缺少依赖或循环明确报错。
 * @property {'sync' | 'block-async' | 'async'} mode 执行方式。
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
 * @typedef {object} InteractionRequest
 * @property {string} requestId 调用方为该调用固定的去重身份，重试同 ID/材料。
 * @property {ToolExecutionContext} source 原执行关联；由已绑定的工具能力校验。
 * @property {'async' | 'block-async'} mode 交互完成的去向。
 * @property {string} kind 已登记的宿主交互种类。
 * @property {import('./common.js').JsonValue} payload 问题或展示材料。
 */

/**
 * @typedef {{ requestId: string } & ({ status: 'succeeded', value: import('./common.js').JsonValue }
 *   | { status: 'failed', problem: import('./common.js').Problem } | { status: 'cancelled' })} InteractionCompletion
 */

export {}
