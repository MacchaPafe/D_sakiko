/* eslint no-unused-vars: ["error", { "args": "none" }] -- 一次性导入声明，不实现旧格式读取。 */

/**
 * 旧 Python 数据的一次性适配器，不把兼容分支留在新核心。
 * 按用户边界划 Turn；不能恢复原响应分组时建立迁移容器并保留可读原文，不补造 reasoning。
 * 工具结果截断或残缺可接受，转换为合法新记录并警告，不追溯原供应商请求或补跑工具。
 * 旧摘要放到覆盖末条消息所属 Turn 末尾，接受同轮尾部未涵盖内容从压缩上下文丢失；原文保留。
 * 不强制重新摘要或开启压缩。这是旧数据导入例外，新摘要仍须覆盖完整 Turn。
 * 缺逐句 formId 时写入当前对应角色默认形态；角色缺失则保留未解析值和警告，不造形态。
 * 已有音频保留；缺资源不阻止恢复可读历史；不恢复任务/演出/未完成交互。
 */
export class PythonChatImporter {
  /**
   * @param {import('../../shared/contracts/common.js').AssetRef} source 已受控导入的旧存档或包。
   * @returns {Promise<import('../../shared/contracts/archive.js').ArchiveDrafts>} 新格式草稿、警告、临时资源持有者，之后按普通导入受理。
   */
  async read(source) {}
}
