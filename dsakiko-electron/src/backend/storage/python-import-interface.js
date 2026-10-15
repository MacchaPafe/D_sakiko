/* eslint no-unused-vars: ["error", { "args": "none" }] -- 一次性导入声明，不实现旧格式读取。 */

/**
 * 旧版 Python 对话存档导入器，用于将旧版数据一次性转换为新版对话草稿。
 */
export class PythonChatImporter {
  /**
   * 读取旧版 Python 对话备份文件并转换为新版格式的对话草稿。
   * @param {import('../../shared/contracts/common.js').AssetRef} source 已导入到媒体库的旧版存档或备份包引用。
   * @returns {Promise<import('../../shared/contracts/archive.js').ArchiveDrafts>} 转换后的对话草稿、迁移警告信息及临时资源持有者标识。
   */
  async read(source) {}
}
