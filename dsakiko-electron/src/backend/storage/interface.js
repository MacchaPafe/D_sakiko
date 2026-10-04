/* eslint no-unused-vars: ["error", { "args": "none" }] -- 接口原型保留参数名称，方法体刻意留空。 */

/** @typedef {import('../../shared/contracts/conversation.js').Chat} Chat */

/**
 * Chat 存储模块：串行化同一存档的写入，负责原子落盘、格式校验、版本迁移和备份导入导出。
 * 需要：携带所基于版本的完整 Chat 快照、删除时的预期版本、资源引用与明确的存储位置配置。
 * 不需要：Agent、演出进度、TTS、哪条消息应该删除或重新生成的业务判断。
 * Chat 是持久化数据，Conversation 是它的运行时修改所有者；本模块只负责 Chat 的持久化。
 * 版本校验防止旧快照覆盖新存档，不承担鉴权或判断异步业务结果是否仍有效的职责。
 * 这是审查用接口，方法均未实现。
 */
export class ChatStore {
  /**
   * 读取摘要列表，不加载所有对话内容。
   * @returns {Promise<import('../../shared/contracts/conversation.js').ChatSummary[]>} 存档入口。
   */
  async list() {}

  /**
   * 加载并迁移存档，保留未知扩展字段；不创建生成或演出任务。
   * 保留 userPersona 中已固定的文本，不查询当前身份目录；旧存档仅在缺少身份信息时迁移为 null。
   * @param {string} chatId 存档身份。
   * @returns {Promise<Chat | null>} 独立的数据快照；不存在时为 null。
   */
  async load(chatId) {}

  /**
   * snapshot.revision 表示本次修改所基于的持久化版本，调用方不预先递增。
   * 新建使用 revision 0，仅当该 ID 不存在时接受，首次成功保存为版本 1。
   * 更新时版本比较与写入是同一原子操作：必须匹配当前存档版本，再由本模块递增一次。
   * 冲突拒绝，不覆盖较新的存档；未成功提交不递增，不修改传入对象。
   * @param {Chat} snapshot 已完成业务修改、仍携带原版本的完整快照。
   * @returns {Promise<Chat>} 已落盘且携带新 revision 的独立快照，作为后续修改的基础。
   */
  async save(snapshot) {}

  /**
   * 版本比较与删除是同一原子操作；共享附件和音频不在此直接删除。
   * 此操作不携带 Chat 快照，因此显式提供所基于的版本，不能用内部最新值替代。
   * @param {string} chatId 存档身份。
   * @param {number} expectedRevision 调用方所基于的持久化版本，必须为正整数。
   * @returns {Promise<void>} 存档已删除；版本冲突拒绝。
   */
  async delete(chatId, expectedRevision) {}

  /**
   * 导出可恢复的存档及其必要附件、语音资源，不打包大型模型和 API 凭据。
   * 包含 Chat 的 userPersona 快照与其头像资源，不要求导出来源人格定义或角色目录。
   * @param {string[]} chatIds 待导出的存档。
   * @returns {Promise<import('../../shared/contracts/common.js').AssetRef>} 可供宿主保存或客户端下载的归档资源。
   */
  async exportArchive(chatIds) {}

  /**
   * 读取并校验归档，返回已迁移的数据草稿；不直接覆盖正在运行的对话。
   * 草稿保留归档中的身份与版本；Conversation 分配新身份并将 revision 设为 0 后，才能作为新 Chat 保存。
   * 提醒是否启用和保存时机也由 Conversation 决定；失败时回收未交付的临时资源。
   * userPersona 快照及其资源引用随归档恢复，不根据来源编号重新获取或覆盖人格文本。
   * @param {import('../../shared/contracts/common.js').AssetRef} archive 已导入的归档文件。
   * @returns {Promise<Chat[]>} 可由对话模块继续处理的数据草稿。
   */
  async readArchive(archive) {}
}
