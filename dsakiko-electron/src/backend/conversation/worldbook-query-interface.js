/* eslint no-unused-vars: ["error", { "args": "none" }] -- Conversation 内部协作声明，未实现，不公开到 IPC。 */

/**
 * 工具来源使用已有执行上下文；绑定函数核对 turnId，持久化时只保留响应/调用身份，不重复存 TurnId。
 * 调用路径决定来源，模型只能提供 WorldbookQuery 的查询字段。
 * @typedef {{ kind: 'direct-context' }
 *   | { kind: 'tool-query', context: import('../../shared/contracts/tools.js').ToolExecutionContext }} WorldbookQueryOrigin
 */

/**
 * @callback BoundWorldbookQuery
 * @param {import('../../shared/contracts/worldbook.js').WorldbookQuery} query 本轮范围内的查询。
 * @param {WorldbookQueryOrigin} origin 内部调用来源；直接上下文由 Conversation 固定，工具来源由工具包装固定。
 * @param {AbortSignal} signal 本次调用的取消信号，与绑定的执行信号共同生效。
 * @returns {Promise<import('../../shared/contracts/worldbook.js').WorldbookEvidence[]>} 诊断已被接受后的知识；查询失败在记录诊断后按 Problem 拒绝，失效/取消不交付成功。
 */

/**
 * @typedef {object} WorldbookQueryBinding
 * @property {string} turnId 本次预分配或已受理的 Turn 身份，不新增执行编号，不允许回调重建运行资格。
 * @property {import('../../shared/contracts/worldbook.js').WorldbookScope} scope 本次固定范围，之后设置修改不影响它。
 * @property {AbortSignal} signal 本次准备/生成的有效期；准备被放弃、生成结束或执行失效时取消，成功受理不另换绑定，不延长到后续 Turn。
 * @property {(diagnostic: import('../../shared/contracts/worldbook.js').WorldbookDiagnostic) => Promise<'accepted' | 'stale'>} acceptDiagnostic 绑定目标的内部接收函数；串行核对执行及工具调用身份，再暂存或追加到内存，不直接提供给工具/页面。
 */

/**
 * 为一次准备/生成绑定共用的世界书查询函数，统一供直接上下文和世界书工具使用。
 * 绑定不开始检索。调用时核对 enabled 与 directContext/queryTools 开关、范围、来源及取消信号；
 * 工具包装只使用 tool-query 来源，不从模型参数接收 origin，不得借此扩大查询权限。
 * 每次调用只查询一次，将 query、来源与原结果组合为一份诊断并 await 接受，再向调用者交付 evidence。
 * 可预期失败同样只记录一次后拒绝；异常统一脱敏为 Problem，取消/失效不补成功或失败记录。
 * acceptDiagnostic 自身失败不得再进入检索异常分支补第二份诊断，也不能自动重查或重放工具。
 * 受理前接收器只暂存直接上下文诊断；受理成功才随新 Turn 保存，受理失败丢弃，不建空 Turn 或启动工具。
 * 受理后接收器核对本轮仍有效及响应/callId 归属，旧执行返回 stale；保存故障可接受到当前内存并由 retrySave 持久化。
 * 长检索不持有 Chat 修改锁；仅诊断接收与受理/停止串行协调。工具结果仍走 Agent 原有 onToolResult。
 * @param {import('../worldbook/interface.js').Worldbook} worldbook 只负责检索的模块。
 * @param {WorldbookQueryBinding} options 固定范围、执行资格及唯一诊断接收路径，不传完整 Chat。
 * @returns {BoundWorldbookQuery} 仅供后端本轮使用的函数，不能序列化、持久化或公开给页面。
 */
export function bindWorldbookQuery(worldbook, options) {}
