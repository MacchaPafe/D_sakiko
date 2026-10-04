/* eslint no-unused-vars: ["error", { "args": "none" }] -- 接口原型保留参数名称，方法体刻意留空。 */

/** @typedef {import('../../shared/contracts/conversation.js').Chat} Chat */
/** @typedef {import('../../shared/contracts/conversation.js').UserSubmission} UserSubmission */
/** @typedef {import('../../shared/contracts/conversation.js').ConversationSnapshot} ConversationSnapshot */
/** @typedef {import('../../shared/contracts/common.js').Metadata} Metadata */

/**
 * @typedef {object} CreateChatOptions
 * @property {string} title 对话标题。
 * @property {string[]} characterIds 本 Chat 的 AI 参与角色，创建后不动态追加角色。
 * @property {import('../../shared/contracts/conversation.js').DialogueMode} mode 编排方式。
 * @property {Metadata} defaults 后续轮次的默认设置。
 * @property {string | null} [userPersonaId] 创建时采用的对话身份；省略或 null 表示无自定义人格。
 */

/**
 * 对话模块：管理多个 Conversation，分别持有 Chat 并编排生成、持久化、合成与演出。
 * Chat 是可持久化的数据，Conversation 是其运行对象；每个 Chat 只由一个 Conversation 修改。
 * 每个对话独立推进；同一对话的修改必须有序执行。共享一个 Node 进程，不为每个 Chat 建进程。
 * 保存时保留 Chat 所基于的 revision，成功后采用 ChatStore 返回的新快照；版本冲突不能靠改大数字强行重试。
 * 内部管理执行编号及 Message ↔ guide/task 的对应关系，丢弃停止、回溯等操作后的迟到结果。
 * 提醒只在所属 Chat 中触发；统一扫描，到期但忙碌时延后。
 * 在 run 前用私有函数将 Message 投影为 ContextMessage，保留 reasoning 类别供 Agent 按协议处理，不负责供应商格式。
 * GeneratedMessage 接受后按顺序构造成 Message 保存；只有 character 台词交给语音和演出，reasoning 按全文展示，是否折叠由界面决定。
 * 接收可见工具记录后按 callId 新增或更新 kind 为 tool 的消息，保存后通过 observe 发布，不交给语音或演出模块。
 * 创建 Chat 时解析并保存对话身份；后续 run 只从 chat.userPersona 提取名称和描述，不重新查询身份目录。
 *
 * 需要：用户意图、角色能力快照，以及 Agent、语音、演出和存储模块提供的有限能力。
 * 不需要：动作结束判断、音频计时、模型常驻队列、SDK 对象、文件路径或传输连接细节。
 * 不向外暴露可修改的 Chat、内部任务编号或完整模块实例。
 *
 * 这是审查用接口，所有方法均未实现，不应实例化后接入应用。
 */
export class Conversations {
  /**
   * 列出对话入口；不加载全部消息或触发生成。
   * @returns {Promise<import('../../shared/contracts/conversation.js').ChatSummary[]>} 对话列表。
   */
  async listChats() {}

  /**
   * 创建空对话；角色可以没有 Live2D 或语音能力。新 Chat 以 revision 0 提交，成功保存后为 1。
   * 提供 userPersonaId 时调用目录的 resolvePersona，将实际身份与 Chat 一起保存；来源不存在则拒绝创建。
   * 身份快照保存完成前不返回成功；目录中新增角色可用于新建，不修改已有 Chat 的参与者。
   * @param {CreateChatOptions} options 初始配置。
   * @returns {Promise<string>} 新对话的 chatId。
   */
  async createChat(options) {}

  /**
   * 提交用户输入并启动一个新 Turn。返回表示输入已保存且生成已受理，不等待演出。
   * 同一对话忙碌时拒绝；相同 requestId 的重试返回同一个 Turn，不重复生成。
   * @param {string} chatId 目标对话。
   * @param {UserSubmission} submission 用户的输入内容与本次的聊天附件。
   * @param {{ requestId: string }} options 一次用户发送操作的稳定编号。
   * @returns {Promise<string>} 已受理的 Turn Id。
   */
  async createTurn(chatId, submission, options) {}

  /**
   * 停止指定轮次。在生成未结束时，取消 Agent Loop 的执行，保留已提交的 reasoning、过渡台词和工具消息；
   * 生成已结束时保留完整回复并立即全文展示。两者都撤销尚未开始的演出和合成，保留当前句。
   * 迟到结果不得恢复播放或继续写入；指定轮次已结束时无副作用。
   * 未取得终态的工具消息标记 interrupted；迟到回调不能覆盖已停止执行的记录。
   * @param {string} chatId 目标对话。
   * @param {string} turnId 目标轮次（由 createTurn 返回），避免迟到的停止命令影响下一轮。
   * @returns {Promise<void>} 停止意图已生效；当前句可能仍在播放。
   */
  async stopTurn(chatId, turnId) {}

  /**
   * 将指定对话切换到前台，被抢占的对话放到后台继续进行。
   * 选择某对话时结束临时回放，显式选择它的常驻 sequence。
   * 输入 null 表示清空前台的对话。
   * @param {string | null} chatId 目标对话。
   * @returns {Promise<void>} 选择已生效。
   */
  async selectConversation(chatId) {}

  /**
   * 使用已保存材料建立临时回放；替换之前的回放，不创建消息、不自动补做语音合成。
   * 只接受 character 台词；reasoning、工具记录和用户输入不属于演出回放对象，传入时拒绝。
   * silent 将屏蔽回放消息的音频。
   * @param {string} chatId 来源对话。
   * @param {string} messageId 来源消息。
   * @param {{ silent: boolean }} options 回放选项。
   * @returns {Promise<void>} 回放已受理，不等待结束。
   */
  async replayMessage(chatId, messageId, options) {}

  /**
   * 重新生成指定轮次，使用该轮冻结设置；移除该轮旧生成记录（reasoning、工具和台词）及后续轮次，保留该轮输入。
   * 保留原 turnId，为这次生成分配新的内部执行编号，不创建身份不同的 Turn。
   * 先使受影响的异步任务和演出失效，再修改记录和启动新执行。
   * @param {string} chatId 来源对话。
   * @param {string} turnId 目标轮次。
   * @returns {Promise<void>} 新执行已受理。
   */
  async recreateTurn(chatId, turnId) {}

  /**
   * 编辑指定轮次的用户输入并重发；移除该轮旧生成记录（reasoning、工具和台词）及后续轮次，沿用该轮冻结设置。
   * 保留原 turnId，为重发后的生成分配新的内部执行编号。
   * @param {string} chatId 目标对话。
   * @param {string} turnId 必须是用户输入触发的轮次。
   * @param {UserSubmission} submission 新输入。
   * @returns {Promise<void>} 编辑已保存，新执行已受理。
   */
  async editAndRecreateTurn(chatId, turnId, submission) {}

  /**
   * 回溯到某轮结束处；null 表示清空全部轮次。先取消受影响任务，再删除记录。
   * @param {string} chatId 目标对话。
   * @param {string | null} throughTurnId 最后一条保留轮次，包含该轮。
   * @returns {Promise<void>} 历史修改已保存。
   */
  async rollback(chatId, throughTurnId) {}

  /**
   * 从指定轮次结束处复制新对话，不复制运行任务；新身份的 revision 从 0 开始，不沿用来源版本。
   * 复制的提醒默认停用，避免重复触发。
   * 保留来源 Chat 的 userPersona 快照，不按当前目录重新解析历史身份。
   * @param {string} chatId 来源对话。
   * @param {string | null} throughTurnId 包含该轮；null 只复制对话配置。
   * @returns {Promise<string>} 新对话身份。
   */
  async forkChat(chatId, throughTurnId) {}

  /**
   * 只重新合成一条消息的语音并保存结果，不改文本、不新建 Turn、不自动回放。
   * 只接受 character 台词；其他消息类型拒绝，不能为 reasoning 或工具记录生成语音。
   * 合成期间消息被删除或再次重合成时，旧结果不得覆盖新状态。
   * @param {string} chatId 来源对话。
   * @param {string} messageId 来源消息。
   * @returns {Promise<void>} 请求已受理，结果通过对话观察接口呈现。
   */
  async regenerateAudio(chatId, messageId) {}

  /**
   * 修改后续轮次的默认设置；已冻结的轮次保持原值。
   * @param {string} chatId 目标对话。
   * @param {Metadata} defaults 完整的新默认设置。
   * @returns {Promise<void>} 设置已保存。
   */
  async setDefaults(chatId, defaults) {}

  /**
   * 在指定对话登记或修改提醒。Agent 工具应获得已经绑定 chatId 的窄接口。
   * @param {string} chatId 唯一允许触发提醒的对话。后续提醒只会在该对话空闲时触发。
   * @param {import('../../shared/contracts/conversation.js').Reminder} reminder 提醒记录。
   * @returns {Promise<void>} 提醒已保存。
   */
  async setReminder(chatId, reminder) {}

  /**
   * 删除本对话提醒；如果提醒已经引发了一个新的轮次，需要另行调用 stopTurn 来终止这个轮次。
   * @param {string} chatId 所属对话。
   * @param {string} reminderId 提醒身份。
   * @returns {Promise<void>} 删除已保存。
   */
  async removeReminder(chatId, reminderId) {}

  /**
   * 删除对话并使其全部任务失效；共享资源由引用回收处理，不能直接按目录删除。
   * @param {string} chatId 目标对话。
   * @returns {Promise<void>} 对话已删除。
   */
  async deleteChat(chatId) {}

  /**
   * 导出选中对话的已保存快照及必要资源，不要求演出结束。
   * @param {string[]} chatIds 目标对话。
   * @returns {Promise<import('../../shared/contracts/common.js').AssetRef>} 可下载或另存的归档资源。
   */
  async exportChats(chatIds) {}

  /**
   * 将归档导入为新对话，分配新身份、将 revision 设为 0 并停用复制的提醒；不覆盖现有对话或恢复旧任务。
   * 保留归档内的 userPersona 快照，不要求本地存在来源对话身份或角色，不重新解析人格文本。
   * @param {import('../../shared/contracts/common.js').AssetRef} archive 已就绪的归档资源。
   * @returns {Promise<string[]>} 新对话身份。
   */
  async importChats(archive) {}

  /**
   * 订阅后先交付当前快照，之后在消息、工具调用详情或显示状态变化时交付新版本，供界面刷新。
   * reasoning 正文及工具调用详情都来自 chat.turns 中的对应消息；取消订阅只停止通知，对话继续运行。
   * 界面使用消息显示投影，无需知道 guideId；快照只读，不能通过修改它操作对话。
   * @param {string} chatId 目标对话。
   * @param {(snapshot: ConversationSnapshot) => void} listener 本地观察函数。
   * @returns {import('../../shared/contracts/common.js').Unsubscribe} 取消观察，不停止任务。
   */
  observe(chatId, listener) {}
}
