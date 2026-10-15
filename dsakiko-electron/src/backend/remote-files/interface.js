/* eslint no-unused-vars: ["error", { "args": "none" }] -- 接口原型保留参数名称，方法体刻意留空。 */

/** @typedef {import('../../shared/contracts/common.js').AssetRef} AssetRef */

/**
 * 厂商远端文件引用（仅在当前模型连接下组装请求时使用，不写入对话存档）。
 * @typedef {object} RemoteFileRef
 * @property {'file-id' | 'file-uri'} kind 模型接口要求的远端文件引用形式。
 * @property {string} value 远端文件 ID 或 URI。
 */

/**
 * 远端文件管理模块，负责将本地媒体资源上传至模型服务商（File API），并管理远端文件缓存、失效与清理。
 */
export class RemoteFiles {
  /**
   * 确保指定的本地资源已上传至当前模型服务商，并返回可用的远端文件引用（若缓存中已有未失效引用则直接复用）。
   * @param {AssetRef} asset 本地媒体资源引用。
   * @param {AbortSignal} [signal] 控制本次上传等待的取消信号。
   * @returns {Promise<RemoteFileRef>} 模型请求可直接使用的远端文件引用。
   */
  async ensureUploaded(asset, signal) {}

  /**
   * 将服务商明确拒绝的远端文件引用标记为已失效，使后续请求不再复用该旧引用。
   * @param {RemoteFileRef} reference 已失效的远端文件引用。
   * @returns {Promise<void>}
   */
  async invalidate(reference) {}

  /**
   * 立即向模型服务商请求删除指定的已上传远端文件，并使其退出本地复用缓存。
   * @param {RemoteFileRef} reference 要删除的远端文件引用。
   * @returns {Promise<void>}
   */
  async delete(reference) {}

  /**
   * 检查并清理所有已不再被本地对话、草稿或任务使用的远端文件。
   * @returns {Promise<void>}
   */
  async collectUnused() {}
}
