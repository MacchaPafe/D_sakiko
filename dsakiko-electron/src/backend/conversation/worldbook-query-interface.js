/* eslint no-unused-vars: ["error", { "args": "none" }] -- Conversation 内部协作声明，未实现，不公开到 IPC。 */

/**
 * 世界书查询触发来源（区分生成前的直接上下文检索与生成过程中的模型工具调用）。
 * @typedef {{ kind: 'direct-context' }
 *   | { kind: 'tool-query', context: import('../../shared/contracts/tools.js').ToolExecutionContext }} WorldbookQueryOrigin
 */

/**
 * 已绑定本轮范围与诊断接收器的世界书查询函数。
 * @callback BoundWorldbookQuery
 * @param {import('../../shared/contracts/worldbook.js').WorldbookQuery} query 查询条件。
 * @param {WorldbookQueryOrigin} origin 查询调用来源。
 * @param {AbortSignal} [signal] 本次查询调用的取消信号。
 * @returns {Promise<import('../../shared/contracts/worldbook.js').WorldbookEvidence[]>} 检索到的世界书知识条目列表。
 */

/**
 * @typedef {object} WorldbookQueryBinding
 * @property {string} turnId 当前对话轮次 ID。
 * @property {import('../../shared/contracts/worldbook.js').WorldbookScope} scope 本轮固定的世界书知识范围。
 * @property {AbortSignal} signal 控制本轮生成有效期的取消信号。
 * @property {(diagnostic: import('../../shared/contracts/worldbook.js').WorldbookDiagnostic) => Promise<'accepted' | 'stale'>} acceptDiagnostic 接收并保存单次检索诊断记录的内部回调函数。
 */

/**
 * 创建一个绑定了本轮知识范围与诊断收集器的世界书查询函数，供直接上下文构建与世界书查询工具共同使用。
 * @param {import('../worldbook/interface.js').Worldbook} worldbook 世界书检索模块实例。
 * @param {WorldbookQueryBinding} options 本轮绑定的轮次 ID、知识范围、取消信号及诊断接收回调。
 * @returns {BoundWorldbookQuery} 绑定后的世界书查询函数。
 */
export function bindWorldbookQuery(worldbook, options) {}
