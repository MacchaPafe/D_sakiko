/* eslint no-unused-vars: ["error", { "args": "none" }] -- 只定义后端，不实际检索。 */

/**
 * 世界书运行模块：直接上下文与查询工具共用固定范围，筛选依赖包、剧情时间和角色知情权限。
 * 诊断单独写 Turn.meta，不成为 Message；查询工具的实际调用/结果仍进入正式历史。
 * 降低进度不清除既有台词/摘要的知识。多角色私有知识隔离细则未定，不能据此宣称已经支持安全隔离。
 */
export class Worldbook {
  /**
   * @param {import('../../shared/contracts/worldbook.js').WorldbookInfo} info 对话配置。
   * @param {Object<string, import('../../shared/contracts/worldbook.js').KnowledgeMapping[]>} mappings 目录的共享知识映射。
   * @returns {Promise<import('../../shared/contracts/worldbook.js').WorldbookScope>} 解析依赖和角色视角后的固定范围。
   */
  async resolveScope(info, mappings) {}

  /**
   * 直接上下文和工具均调用相同查询契约；预算不能扩大角色权限。
   * @param {import('../../shared/contracts/worldbook.js').WorldbookScope} scope 本次范围。
   * @param {import('../../shared/contracts/worldbook.js').WorldbookQuery} query 查询。
   * @param {AbortSignal} signal 取消，Conversation 拒收旧执行诊断。
   * @returns {Promise<{ evidence: import('../../shared/contracts/worldbook.js').WorldbookEvidence[], diagnostic: import('../../shared/contracts/worldbook.js').WorldbookDiagnostic }>} 可用知识及独立诊断。
   */
  async query(scope, query, signal) {}
}
