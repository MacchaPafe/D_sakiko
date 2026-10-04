/**
 * 对话身份的可复用定义与 Chat 中固定的身份快照。
 * 定义由 CharacterCatalog 持久化，快照随 Chat 持久化；两者不共享可修改的对象。
 */

/** @typedef {import('./common.js').AssetRef} AssetRef */

/**
 * @typedef {object} CustomPersonaSource
 * @property {'custom'} kind 自定义人格。
 * @property {string} displayName 用户扮演身份的显示名称。
 * @property {string} description 用户编写的人格文本，允许为空。
 * @property {AssetRef | null} avatar 可选头像。
 */

/**
 * 只保存角色引用，不附带复制的名称、描述、头像或演出能力。
 * @typedef {object} CharacterPersonaSource
 * @property {'character'} kind 扮演已有角色。
 * @property {string} characterId 来源角色的稳定身份。
 */

/**
 * source 的两种形式互斥，避免角色引用和旧描述同时存在而难以确定应使用哪份内容。
 * @typedef {object} UserPersonaDefinition
 * @property {string} id 稳定对话身份编号，不由显示名推导，也不与来源角色 ID 混用。
 * @property {number} revision 定义自身的非负整数版本；新建为 0，保存时由目录递增，来源角色更新不改变此版本。
 * @property {CustomPersonaSource | CharacterPersonaSource} source 自定义文本或角色引用。
 */

/**
 * 创建 Chat 时解析并固定的实际身份；后续生成不重新查询定义或来源角色。
 * 来源编号仅记录出处，不能作为继续对话、分支或导入归档时必须存在的依赖。
 * @typedef {object} UserPersonaSnapshot
 * @property {string} personaId 来源对话身份编号。
 * @property {string | null} sourceCharacterId 来源角色编号，自定义人格为 null。
 * @property {string} displayName 创建时实际采用的名称。
 * @property {string} description 创建时实际采用的人格文本。
 * @property {AssetRef | null} avatar 创建时采用的头像资源，由 Chat 持有引用。
 */

/**
 * 供选择列表展示的当前摘要，不会写回定义，也不能替代创建 Chat 时的解析。
 * @typedef {object} UserPersonaSummary
 * @property {string} id 对话身份编号。
 * @property {string | null} displayName 当前名称；来源角色缺失时为 null，不伪造一个历史名称。
 * @property {AssetRef | null} avatar 当前可展示的头像。
 * @property {import('./common.js').Problem | null} problem 来源不可用等问题；单条身份失效不阻止列出其他身份。
 */

export {}
