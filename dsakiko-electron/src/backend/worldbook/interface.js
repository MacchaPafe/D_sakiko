/* eslint no-unused-vars: ["error", { "args": "none" }] -- 只定义后端，不实际检索。 */

/**
 * 世界书检索模块，负责根据剧情进度与角色视角权限，检索角色可知的世界书知识。
 */
export class Worldbook {
  /**
   * 根据对话的世界书配置与角色知识映射，解析出本轮生成允许检索的知识范围。
   * @param {import('../../shared/contracts/worldbook.js').WorldbookInfo} info 对话的世界书配置（包含根知识包、剧情进度等）。
   * @param {Object<string, import('../../shared/contracts/worldbook.js').KnowledgeMapping[]>} mappings 各角色的知识视角映射配置。
   * @returns {Promise<import('../../shared/contracts/worldbook.js').WorldbookScope>} 本轮生成固定的世界书检索范围。
   */
  async resolveScope(info, mappings) {}

  /**
   * 在指定的知识范围内检索与查询文本相关的世界书条目。
   * @param {import('../../shared/contracts/worldbook.js').WorldbookScope} scope 由 resolveScope 解析出的知识范围。
   * @param {import('../../shared/contracts/worldbook.js').WorldbookQuery} query 查询条件（包含角色视角、知识类别、查询文本及 Token 预算）。
   * @param {AbortSignal} [signal] 控制本次查询的取消信号。
   * @returns {Promise<import('../../shared/contracts/worldbook.js').WorldbookQueryResult>} 匹配到的知识条目列表及检索追踪诊断信息。
   */
  async query(scope, query, signal) {}
}
