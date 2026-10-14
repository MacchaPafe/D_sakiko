/* eslint no-unused-vars: ["error", { "args": "none" }] -- 只定义后端，不实际检索。 */

/**
 * 世界书运行模块：直接上下文与查询工具共用固定范围，筛选依赖包、剧情时间和角色知情权限。
 * 只返回知识与检索事实，不知道调用阶段或 Turn，也不直接写存档；Conversation 的本轮查询函数统一接受诊断。
 * 查询工具的实际调用/结果仍进入正式历史，诊断不成为 Message。
 * 降低进度不清除既有台词/摘要的知识。多角色私有知识隔离细则未定，不能据此宣称已经支持安全隔离。
 */
export class Worldbook {
  /**
   * 根据对话配置和角色知识映射，确定本次生成可使用的世界书知识范围。
   * @param {import('../../shared/contracts/worldbook.js').WorldbookInfo} info 对话配置。
   * @param {Object<string, import('../../shared/contracts/worldbook.js').KnowledgeMapping[]>} mappings 目录的共享知识映射。
   * @returns {Promise<import('../../shared/contracts/worldbook.js').WorldbookScope>} 解析依赖和角色视角后的固定范围。
   */
  async resolveScope(info, mappings) {}

  /**
   * 在已确定的知识范围内检索相关证据，并返回同次查询的过滤、预算和失败事实。
   * 直接上下文和工具均调用相同查询契约；预算不能扩大角色权限。
   * 可预期检索失败返回空 evidence 与 trace.problem；非法范围/参数按 Problem 拒绝，取消以 AbortError 拒绝。
   * 不接受 stage、TurnId 或诊断回调；来源关联由调用方绑定，不能为了诊断再执行一次查询。
   * @param {import('../../shared/contracts/worldbook.js').WorldbookScope} scope 本次范围。
   * @param {import('../../shared/contracts/worldbook.js').WorldbookQuery} query 查询。
   * @param {AbortSignal} signal 本次查询取消。
   * @returns {Promise<import('../../shared/contracts/worldbook.js').WorldbookQueryResult>} 可用知识与检索事实，不直接赋予其进入历史的资格。
   */
  async query(scope, query, signal) {}
}
