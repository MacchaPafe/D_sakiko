/* eslint no-unused-vars: ["error", { "args": "none" }] -- 接口原型保留参数名称，方法体刻意留空。 */

/** @typedef {import('../../shared/contracts/persona.js').UserPersonaDefinition} UserPersonaDefinition */

/** @typedef {import('../../shared/contracts/characters.js').CharacterDefinition} CharacterDefinition */
/** @typedef {import('../../shared/contracts/characters.js').CharacterCapabilities} CharacterCapabilities */

/**
 * 角色目录模块：统一管理角色配置、对话身份定义与资源解析，是 Node 中唯一的权威目录。
 * 对话身份独立存放于用户数据目录的 personas/<personaId>.json；路径由宿主装配，不由业务调用方指定。
 * 需要：角色配置、对话身份定义和资源引用。
 * 不需要：全局“当前角色”、当前 Chat、生成历史、演出时间线或模型推理实例。
 * 修改目录不自动替换运行中的演出，也不追溯修改已有轮次快照。
 * 保存成功后，后续读取与解析立即可见；本模块更新或失效自己的缓存，依赖资源变化也须使相关解析缓存失效。
 * 页面可重新查询列表；Python 与演出模块只接收任务材料，不各自维护完整角色目录。
 * 人格指向角色时只保存引用；解析时机与 Chat 快照保存由 Conversation 决定，目录不接收 Chat。
 * 调用方只把能力快照中必要的部分交给 Agent、语音和演出模块。
 * 这是审查用接口，方法均未实现。
 */
export class CharacterCatalog {
  /**
   * 按目录顺序列出 AI 角色默认形态的摘要，不包含用户人格、不加载推理模型。
   * 新角色经目录保存后即可出现在后续查询中并用于创建 Chat，不要求重启应用。
   * @returns {Promise<Array<{ id: string, displayName: string, avatar: import('../../shared/contracts/common.js').AssetRef | null }>>} 角色摘要。
   */
  async list() {}

  /**
   * 读取角色配置供编辑，不隐式开始任何能力加载。
   * @param {string} characterId 角色身份。
   * @returns {Promise<CharacterDefinition | null>} 独立配置快照。
   */
  async get(characterId) {}

  /**
   * 使用 definition.revision 原子核对并保存角色，成功后由目录递增一次；调用方不预先递增。
   * 0 仅用于新建且要求该 ID 不存在，首次保存为 1；冲突拒绝，不修改输入对象。
   * @param {CharacterDefinition} definition 已完成修改、仍携带原版本的完整配置。
   * @returns {Promise<CharacterDefinition>} 已保存且携带新版本的独立配置；后续读取与解析可见新配置。
   */
  async save(definition) {}

  /**
   * 解析身份、演出目录与声音配置；只读取描述和资源元数据，不常驻 Live2D 或 TTS 模型。
   * 显式无效形态/模型拒绝，省略 formId 使用默认形态；已配置但缺失的可选资源保留诊断。
   * @param {string} characterId 角色身份。
   * @param {{ formId?: string, modelId?: string | null }} options null 模型采用本形态默认指针。
   * @returns {Promise<CharacterCapabilities>} 可供一次编排使用的只读快照。
   */
  async resolveCapabilities(characterId, options) {}

  /**
   * 普通生成用已冻结能力，历史重合成先解析当前原形态能力；本方法不重读目录、不重新推断情绪。
   * @param {CharacterCapabilities} capabilities 一致的形态与声音快照。
   * @param {string | null} emotion 原台词情绪。
   * @returns {Promise<import('../../shared/contracts/characters.js').VoiceResolution>} 完整声音材料或同形态降级结果。
   */
  async resolveVoice(capabilities, emotion) {}

  /**
   * @param {string[]} characterIds 完整有序角色 ID，原子校验无遗漏/重复。
   * @returns {Promise<void>} 仅更新目录顺序，不改角色定义。
   */
  async setOrder(characterIds) {}

  /**
   * @param {string} personaId 人格定义身份。
   * @param {number} expectedRevision 所基于版本。
   * @returns {Promise<void>} 定义已删除，已有 Chat 的人格快照及头像引用不受影响。
   */
  async deletePersona(personaId, expectedRevision) {}

  /**
   * 列出对话身份供用户选择；关联角色的摘要使用当前名称与头像，不将摘要保存为身份快照。
   * 角色身份通过其默认形态解析名称和头像；来源角色缺失的身份仍保留入口并报告问题，以便编辑修复，不使整个列表失败。
   * @returns {Promise<import('../../shared/contracts/persona.js').UserPersonaSummary[]>} 当前身份摘要。
   */
  async listPersonas() {}

  /**
   * 读取用于编辑的原始定义；角色引用保持引用，不替换为当前角色的文本。
   * @param {string} personaId 对话身份编号。
   * @returns {Promise<UserPersonaDefinition | null>} 独立定义快照；身份不存在时为 null。
   */
  async getPersona(personaId) {}

  /**
   * 保存自定义人格文本或角色引用，维护定义与头像资源引用；不修改任何已有 Chat。
   * definition.revision 是所基于的版本，0 仅用于新建；版本比较和写入原子完成，成功后递增一次。
   * 关联角色时不复制名称、描述或头像；来源角色后续更新不递增此定义的 revision。
   * @param {UserPersonaDefinition} definition 完整身份定义；不得同时携带自定义文本与角色引用。
   * @returns {Promise<UserPersonaDefinition>} 已保存且携带新版本的独立定义；冲突拒绝，不修改输入对象。
   */
  async savePersona(definition) {}

  /**
   * 按调用时的定义解析实际身份；角色引用每次读取当前角色名称、描述和头像，不复用绑定时的旧文本。
   * 角色引用采用当前默认形态的名称、描述和头像；只读取身份字段，不调用完整能力解析，不加载模型；返回结果来自一致的定义与角色配置快照。
   * 仅构造独立数据，不保存到目录或 Chat；是否以及何时固定这份结果由调用方决定。
   * @param {string} personaId 对话身份编号。
   * @returns {Promise<import('../../shared/contracts/persona.js').UserPersonaSnapshot>} 实际身份快照；身份或来源角色不存在时拒绝，不静默降级为默认身份。
   */
  async resolvePersona(personaId) {}
}
