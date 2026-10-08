/* eslint no-unused-vars: ["error", { "args": "none" }] -- 完整模型包导入的审查声明。 */

/**
 * 独立 Live2D 导入模块；支持 V2/V3 入口、纹理、动作、表情及映射旁文件，重写引用后原子发布。
 * 模型平级不要求摊平包内目录；重名依赖不能互相覆盖。拒绝未获授权的包外引用和路径逃逸。
 * 失败/取消不发布半包，清理临时材料；不负责下载、编辑资源或改变角色默认模型指针。
 */
export class Live2dImporter {
  /**
   * @param {import('../../shared/contracts/common.js').AssetRef} archive 已导入的完整模型包。
   * @param {{ entry: string, name: string }} options 包内入口与显示名，目标存储位置由模块决定。
   * @param {AbortSignal} signal 取消。
   * @returns {Promise<{ model: import('../../shared/contracts/characters.js').CharacterModel, owner: import('../../shared/contracts/common.js').AssetOwnerId, problems: import('../../shared/contracts/common.js').Problem[] }>} 原子发布材料，目录建立引用后释放临时 owner。
   */
  async importPackage(archive, options, signal) {}
}
