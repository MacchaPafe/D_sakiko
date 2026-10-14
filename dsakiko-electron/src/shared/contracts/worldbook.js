/** 世界书只定义后端契约，本次不实际检索；多角色私有知识隔离的查询细则仍待设计。 */

/**
 * @typedef {object} WorldbookInfo
 * @property {boolean} enabled 是否启用。
 * @property {string[]} rootPackageIds 根包身份。
 * @property {Object<string, string>} progress 各作品的稳定剧情进度标记，不用显示文本推断顺序。
 * @property {boolean} directContext 是否提供直接上下文。
 * @property {boolean} queryTools 是否启用范围受限的查询工具。
 */

/**
 * @typedef {object} KnowledgeMapping
 * @property {string} packageId 知识包。
 * @property {string} characterId 包内角色身份，与应用角色 ID 分开。
 */

/**
 * @typedef {object} WorldbookScope
 * @property {WorldbookInfo} info 本次固定的开关、根包与进度。
 * @property {Array<{ id: string, revision: string }>} packages 解析依赖后的包集合及版本。
 * @property {Object<string, KnowledgeMapping[]>} perspectives 每个应用角色的固定知识映射。
 */

/**
 * @typedef {object} WorldbookQuery
 * @property {string} perspectiveCharacterId 本次范围内的应用角色，必须明确查询视角。
 * @property {'event' | 'thought' | 'relation' | 'setting'} kind 查询类别。
 * @property {string} text 检索文本。
 * @property {number} tokenBudget 正整数输出预算，不扩大知识权限。
 */

/**
 * @typedef {object} WorldbookEvidence
 * @property {string} packageId 来源包。
 * @property {string} entryId 稳定条目身份。
 * @property {string} text 允许供模型使用的知识内容。
 */

/**
 * 不包含调用阶段或 Turn 身份，Worldbook 只报告检索事实；被选中材料仅在 WorldbookQueryResult.evidence 中保存一份。
 * @typedef {object} WorldbookTrace
 * @property {Array<{ packageId: string, entryId: string, reason: string }>} filtered 过滤及预算裁剪原因。
 * @property {number | null} estimatedTokens 返回知识的 token 估值，无法估计时为 null；请求预算来自 WorldbookQuery.tokenBudget。
 * @property {import('./common.js').Problem | null} problem 本次查询故障，不含原始堆栈。
 */

/**
 * @typedef {object} WorldbookQueryResult
 * @property {WorldbookEvidence[]} evidence 通过范围及预算校验的知识；可预期查询失败时为空，不能把失败当作成功的空查询。
 * @property {WorldbookTrace} trace 检索事实，与知识由同一次查询产生。
 */

/**
 * 由本轮查询函数补入的调用来源，不属于模型工具参数；所属 Turn 由包含它的 Turn.meta 表达。
 * @typedef {{ kind: 'direct-context' }
 *   | { kind: 'tool-query', assistantMessageId: string, callId: string }} WorldbookQuerySource
 */

/**
 * Conversation 接受后保存的一份实际查询记录，不再重复保存 stage 或 selected。
 * @typedef {object} WorldbookDiagnostic
 * @property {WorldbookQuerySource} source 由可信调用路径确定的阶段及调用关联。
 * @property {WorldbookQuery} query 实际查询及请求预算。
 * @property {WorldbookQueryResult} result 本次知识与检索事实；只将 result.evidence 投影给模型。
 */

export {}
