/* eslint no-unused-vars: ["error", { "args": "none" }] -- 完整模型包导入的审查声明。 */

/**
 * Live2D 模型包导入器，负责解析模型压缩包并将内部纹理、动作与表情文件导入到媒体资源库。
 */
export class Live2dImporter {
  /**
   * 将一个 Live2D 模型包导入到媒体资源库，并生成可添加到角色形态中的模型配置。
   * @param {import('../../shared/contracts/common.js').AssetRef} archive 已导入到媒体库的模型包资源引用。
   * @param {{ entry: string, name: string }} options 导入选项：entry 为包内模型入口文件的相对路径，name 为模型的展示名称。
   * @param {AbortSignal} [signal] 控制本次导入的取消信号。
   * @returns {Promise<{ model: import('../../shared/contracts/characters.js').CharacterModel, owner: import('../../shared/contracts/common.js').AssetOwnerId, problems: import('../../shared/contracts/common.js').Problem[] }>} 生成的模型配置、临时资源持有者标识及导入期间发现的问题列表。
   */
  async importPackage(archive, options, signal) {}
}
