/* eslint no-unused-vars: ["error", { "args": "none" }] -- 审查声明，不替换旧原型 Settings。 */

/**
 * 全局设置与连接目录负责校验、默认值及格式迁移；不拥有 Chat，也不改变当前执行材料。
 * 全局/Chat/本次同名字段只在 Conversation 开始时解析一次。必需连接和资源缺失明确拒绝。
 * 语音设备/精度/结构/预算下次启动生效；生成默认值下轮生效；BGM/展示偏好保存后由装配协调 Performance/宿主。
 */
export class Settings {
  /**
   * 读取当前全局设置，供设置页面展示及后续执行解析默认值。
   * @returns {Promise<import('../../shared/contracts/settings.js').GlobalSettings>} 独立只读配置，不含凭据。
   */
  async get() {}

  /**
   * 校验并保存完整的全局设置。
   * @param {import('../../shared/contracts/settings.js').GlobalSettings} settings 完整配置及原 revision；未知业务字段拒绝。
   * @returns {Promise<import('../../shared/contracts/settings.js').GlobalSettings>} 原子比较递增后的新版本，不修改输入。
   */
  async save(settings) {}

  /**
   * 列出已配置的模型连接及其能力信息，供选择和管理连接。
   * @returns {Promise<import('../../shared/contracts/models.js').ConnectionDefinition[]>} 连接/能力/凭据存在标记，不返回密钥。
   */
  async listConnections() {}

  /**
   * 创建或更新一个模型连接，并按明确指令保留、替换或移除其凭据。
   * @param {import('../../shared/contracts/models.js').ConnectionDefinition} definition 原版本，0 只用于新建；credentialConfigured 为只读投影。
   * @param {{ kind: 'keep' } | { kind: 'replace', secret: string } | { kind: 'remove' }} credential 仅受控设置入口可写，不回显、记录或归档 secret。
   * @returns {Promise<import('../../shared/contracts/models.js').ConnectionDefinition>} 已保存连接及递增版本。
   */
  async saveConnection(definition, credential) {}

  /**
   * 删除指定的模型连接配置。
   * @param {string} connectionId 目标。
   * @param {number} expectedRevision 防止删除较新配置。
   * @returns {Promise<void>} 已删除；已有请求保留固定材料，后续解析缺失引用明确失败。
   */
  async deleteConnection(connectionId, expectedRevision) {}

  /**
   * 将连接和模型选择解析为一次后端请求可使用的完整连接及能力快照。
   * 后端专用解析，不向 renderer/归档发送凭据作用域。RemoteFiles 与请求使用同一快照。
   * @param {{ connectionId: string, modelId: string }} selection 模型选择。
   * @returns {Promise<import('../../shared/contracts/models.js').ResolvedModelConnection>} 完整临时连接及合并能力来源。
   */
  async resolveConnection(selection) {}

  /**
   * 显式连接检查，允许最小真实请求；不执行工具、不写聊天，也不把成功文本请求当作视觉能力证明。
   * @param {{ connectionId: string, modelId: string }} selection 要检查的连接及模型。
   * @param {AbortSignal} signal 取消。
   * @returns {Promise<{ reachable: boolean, problems: import('../../shared/contracts/common.js').Problem[] }>} 脱敏检查结果。
   */
  async checkConnection(selection, signal) {}
}
