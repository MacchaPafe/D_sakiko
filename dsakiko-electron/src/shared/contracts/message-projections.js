/* eslint no-unused-vars: ["error", { "args": "none" }] -- 只读 helper 的审查声明，尚无实现，不接入原型。 */

/** @typedef {import('./conversation.js').Turn | import('./conversation.js').AssistantMessage} MessageSource */

/**
 * 按权威存档顺序提取所有台词；句身份保持不变，不能用索引代替，也不能修改返回条目以写回历史。
 * @param {MessageSource} source 一轮或一份响应。
 * @returns {ReadonlyArray<Readonly<import('./conversation.js').CharacterLine>>} 只读句序列，不含 reasoning/工具。
 */
export function getCharacterLines(source) {}

/**
 * @param {MessageSource} source 一轮或响应。
 * @returns {ReadonlyArray<Readonly<import('./conversation.js').ReasoningPart>>} 有序实际 reasoning，不推测缺失内容。
 */
export function getReasoningParts(source) {}

/**
 * @param {MessageSource} source 一轮或响应。
 * @returns {ReadonlyArray<Readonly<import('./conversation.js').ToolCallPart>>} 保留稳定身份和调用顺序的只读调用项。
 */
export function getToolCalls(source) {}

/**
 * @param {import('./conversation.js').Turn} turn 调用所属轮次，callId 的关联范围。
 * @param {string} callId 模型原始调用 ID。
 * @returns {Readonly<import('./conversation.js').ToolResultMessage> | null} null 表示尚无结果，不等于成功空值；多份结果视为存档错误。
 */
export function getToolResult(turn, callId) {}

/**
 * @typedef {object} DisplayEntry
 * @property {string} id 来自 Message/part/line 的已有身份，不在投影时生成。
 * @property {string} turnId 所属轮次。
 * @property {string} messageId 权威消息身份。
 * @property {import('./conversation.js').UserMessage | import('./conversation.js').ReasoningPart | import('./conversation.js').ToolCallPart | import('./conversation.js').CharacterLine | import('./conversation.js').ToolResultMessage | import('./conversation.js').AbstractMessage} content 只读内容，工具结果仍为独立记录。
 */

/**
 * 提供完整扁平展示投影；并不承诺每次全量复制/跨进程发送，也不把展示顺序当作协议分组。
 * @param {ReadonlyArray<import('./conversation.js').Turn>} turns 有序历史。
 * @returns {ReadonlyArray<Readonly<DisplayEntry>>} 按原顺序展开的记录，编辑经 Conversations 精确定位。
 */
export function projectDisplayEntries(turns) {}
