/* eslint no-unused-vars: ["error", { "args": "none" }] -- 审查声明，方法体刻意留空。 */

/** @typedef {import('../../shared/contracts/conversation.js').UserSubmission} UserSubmission */
/** @typedef {import('../../shared/contracts/conversation.js').LineRef} LineRef */
/** @typedef {import('../../shared/contracts/settings.js').TurnOptions} TurnOptions */

/**
 * @typedef {object} CreateChatOptions
 * @property {string} title 标题。
 * @property {string[]} characterIds 固定参与角色，不在已有 Chat 追加角色。
 * @property {import('../../shared/contracts/conversation.js').DialogueMode} mode 编排方式。
 * @property {import('../../shared/contracts/settings.js').ChatSettings} settings 对话设置，创建时校验角色选择。
 * @property {string | null} [userPersonaId] 省略或 null 表示无自定义人格；否则解析并保存固定快照。
 */

/**
 * @typedef {{ kind: 'user', turnId: string, messageId: string, content: UserSubmission }
 *   | { kind: 'line', line: LineRef, text: string, translation: string | null }} HistoryEdit
 */

/**
 * 对话模块是每个 Chat 的唯一运行时修改所有者；同一 Chat 的受理、历史修改、保存及 async 消费串行协调。
 * 初始输入校验和持久化完成才算受理，此前失败不消费草稿/async 队列、不替换旧轮、不启动工具。
 * 受理后保留输入、附件、已接受 reasoning/调用/台词和副作用。保存失败保留内存并阻止新生成和历史修改，
 * 仍允许查看、停止、重试保存；已运行任务继续结算，结果不能因采用旧 Chat 快照而覆盖新的内容。
 * 每次执行固定角色、配置、工具和知识范围；向 Agent/Speech/Performance 分别投影材料，不透传完整 Chat。
 * 正常 Turn 到生成、自动语音及普通演出全部结算才 finished；这期间不受理同 Chat 的下一轮或历史修改。
 * 语音失败保留文字并无声 ready；播放失败须有界结算。async 交互、BGM、驻留/循环过渡不计入完成条件。
 * 待播普通内容清空且本轮不再生成后，清理本轮 transition；独立回放/重合成不让历史 Turn 重新 running。
 * Agent 不可恢复失败归 terminated 并使组失效；保存错误、单句语音/播放错误不是组终止。
 * 手动新轮和显式重生成开新组；自动轮沿用有效组，无有效组的提醒另开组。组和接收资格只存在内存。
 * 历史语义修改作废本 Chat 全部未消费及尚未返回的 async；背景和音频修改不触发此失效。
 * 重启只恢复已保存记录，对缺结果调用做通用中断结算；不恢复任务、演出、交互资格或组，不重放工具。
 * 停止后保留当前句时何时重开提交、手动重合成目标句保护的细节仍待定，不在此固化额外策略。
 * 本文件全部为未实现的审查声明，不接入快速原型。
 */
export class Conversations {
  /** @returns {Promise<import('../../shared/contracts/conversation.js').ChatSummary[]>} 按持久索引排序的入口，不加载全文。 */
  async listChats() {}

  /**
   * 创建并保存空 Chat，revision 从 0 提交为 1；身份解析失败拒绝，不伪造人格。
   * @param {CreateChatOptions} options 创建材料。
   * @returns {Promise<string>} 已保存的 ChatId。
   */
  async createChat(options) {}

  /**
   * 普通手动发送；来源由本入口固定为 manual。空文本加有效图片可受理。
   * 相同 requestId/材料重试返回同一 Turn，即使此时忙碌也不重复执行；同 ID 改材料拒绝。
   * 首次受理后开新组、作废旧 async，停止本 Chat 临时回放（包含当前句）并交接常驻演出。
   * 校验/保存失败不打断回放、不使旧组失效；工具交互答案不得通过此入口提交。
   * @param {string} chatId 目标对话。
   * @param {UserSubmission} submission 手动内容。
   * @param {TurnOptions} options 稳定请求身份与本次覆盖。
   * @returns {Promise<string>} 输入已保存且生成已受理的 TurnId，不等待演出。
   */
  async createTurn(chatId, submission, options) {}

  /**
   * 按当前情景主动生成，不伪造空 UserMessage、不消费草稿；情境必须有效。
   * 情景输入记为 Turn.input.kind=scenario，受理与开组规则同手动发起。
   * 小剧场后续只重生成的产品限制由前端承担，后端不按模式永久禁止连续输入。
   * @param {string} chatId 目标对话。
   * @param {TurnOptions} options 稳定请求身份与本次覆盖。
   * @returns {Promise<string>} 新 TurnId。
   */
  async generateScenario(chatId, options) {}

  /**
   * 先使运行 Turn terminated、作废所属组的全部未消费及尚未返回 async，再尽力取消任务。
   * 保留已接受内容并全文展示；原子移除后续演出，保留当前句。底层推理实际退出前仍占调度资源。
   * 缺少结果的调用以原 callId 结算通用中断错误，不推断副作用回滚；accepted 不补第二份结果。
   * 已 finished 的轮可使其组失效但不改为 running；重复停止幂等，旧组命令不影响新组。
   * @param {string} chatId 目标对话。
   * @param {string} turnId 明确目标身份。
   * @returns {Promise<void>} 停止已生效，当前句可能仍在播放。
   */
  async stopTurn(chatId, turnId) {}

  /**
   * 切换前台并结束相关临时回放；桌宠绑定同一个当前 Chat，后台其他 Chat 继续推进。
   * @param {string | null} chatId null 清空前台，不自动提升其他 Chat。
   * @returns {Promise<void>} 选择已生效。
   */
  async selectConversation(chatId) {}

  /**
   * 用已保存台词和资源临时回放，不写历史、不补合成；silent 只屏蔽本次音频。
   * @param {string} chatId 来源对话。
   * @param {LineRef} line 句级定位，不能把整个 assistant 容器当一句。
   * @param {{ silent: boolean }} options 回放选项。
   * @returns {Promise<void>} 回放已受理。
   */
  async replayLine(chatId, line, options) {}

  /**
   * 按记录顺序回放整 Turn 的台词；跳过 reasoning/工具/摘要，不重跑历史来源 Turn。
   * 新轮持久受理后关闭本 Chat 临时回放 sequence 并结算等待；不关闭常驻 sequence 或其他 Chat。
   * @param {string} chatId 来源对话。
   * @param {string} turnId 来源轮次。
   * @param {{ silent: boolean }} options 回放选项。
   * @returns {Promise<void>} 临时回放已受理。
   */
  async replayTurn(chatId, turnId, options) {}

  /**
   * 仅替换最后一个 Turn；保留原输入/附件/内部输入或情景命令，保留角色、模式和固定人格。
   * 读取当前配置及本次覆盖，不隐式复用旧临时 options；新 UUID、新组，旧组保持失效。
   * 先校验目标和材料；统一协调旧任务失效、记录替换及持久化。受理保存失败保留旧轮和资源。
   * 已受理后生成失败保留新输入；不撤销旧提醒、文件等副作用，新执行允许产生新副作用。
   * @param {string} chatId 目标对话。
   * @param {string} turnId 受理时仍须是最后一轮。
   * @param {TurnOptions} options 本次独立操作身份与覆盖。
   * @returns {Promise<string>} 新 TurnId。
   */
  async recreateTurn(chatId, turnId, options) {}

  /**
   * 只接受最后一轮的 manual 输入；一次原子受理完成编辑和替换，不要求调用方先删除再创建。
   * 新输入校验或受理保存失败不丢旧轮，共用附件先建立新引用再释放旧引用；其他语义同 recreateTurn。
   * @param {string} chatId 目标对话。
   * @param {string} turnId 最后一轮身份。
   * @param {UserSubmission} submission 编辑后的手动内容。
   * @param {TurnOptions} options 操作身份及本次覆盖。
   * @returns {Promise<string>} 新 TurnId/新组。
   */
  async editAndRecreateTurn(chatId, turnId, submission, options) {}

  /**
   * 原地编辑 manual 用户内容或台词/翻译，保留后文且不生成；拒绝内部输入、reasoning、工具和摘要。
   * 移除依赖被改内容的后方摘要，作废全部未消费 async。正文变化同步更新发声文本并使旧音频失效；
   * 仅翻译变化不使无关音频失效。编辑/删除导致目标内容失效时，旧音频任务不得覆盖当前记录。
   * @param {string} chatId 目标对话。
   * @param {HistoryEdit} edit 精确定位与完整可编辑字段。
   * @returns {Promise<void>} 编辑已保存。
   */
  async editHistory(chatId, edit) {}

  /**
   * 删除选中 Turn 及之后全部内容，协调任务、演出、摘要及资源失效，不撤销外部副作用。
   * @param {string} chatId 目标对话。
   * @param {string} turnId 第一条删除轮次，包含该轮。
   * @returns {Promise<void>} 截断已保存。
   */
  async truncateFromTurn(chatId, turnId) {}

  /**
   * 回溯到指定 Turn 结束处；null 清空，受理时作废全部未消费 async。
   * @param {string} chatId 目标对话。
   * @param {string | null} throughTurnId 最后保留轮次，包含该轮。
   * @returns {Promise<void>} 回溯已保存。
   */
  async rollback(chatId, throughTurnId) {}

  /**
   * 复制到 Turn 结束处，沿用固定人格；新 Chat/revision 0，不复制运行资格，提醒默认停用。
   * @param {string} chatId 来源对话。
   * @param {string | null} throughTurnId null 只复制配置。
   * @returns {Promise<string>} 已保存新 ChatId。
   */
  async forkChat(chatId, throughTurnId) {}

  /**
   * 保留原句、原形态/情绪/发声语言/读音，按当前对应声音资源及语音参数重合成，不自动回放。
   * 不切换到其他形态；只合并当前记录的音频字段。删除、内容编辑或更新任务后旧结果不能覆盖。
   * 不改变 finished 生命周期或 async 资格；显式锁定参考材料的优先级仍待定，本次不提供参数。
   * @param {string} chatId 来源对话。
   * @param {LineRef} line 目标句。
   * @returns {Promise<void>} 已受理，结果和音频故障经 observe 交付。
   */
  async regenerateAudio(chatId, line) {}

  /**
   * 查询实际请求准备的占用，不压缩、不上传附件、不调用工具，不修改历史。
   * @param {string} chatId 目标对话。
   * @param {UserSubmission | null} submission 可选草稿预览；null 检查当前历史及配置。
   * @param {import('../../shared/contracts/settings.js').GenerationOptions} options 本次预览覆盖。
   * @returns {Promise<import('../llm/interface.js').ContextInspection>} 估计/未知/实际 usage 及来源。
   */
  async inspectContext(chatId, submission, options) {}

  /**
   * 手动压缩互斥生成/历史修改，但允许历史回放。仅压缩已结束完整轮次，至少保留最后一轮。
   * 累计摘要插入覆盖末轮末尾；失败保留旧有效摘要和原文，不制造回复，不恢复失效 async。
   * @param {string} chatId 目标对话。
   * @returns {Promise<void>} 压缩与保存完成；不可压缩时明确报告。
   */
  async compressHistory(chatId) {}

  /**
   * 只重试持久化当前内存修改，按保存顺序合并已运行任务结果；成功解除受理限制。
   * 版本冲突不能修改版本号强存，不重新执行模型/工具/合成。
   * @param {string} chatId 保存失败的对话。
   * @returns {Promise<void>} 当前待保存内容已落盘。
   */
  async retrySave(chatId) {}

  /**
   * @param {string} chatId 目标。
   * @param {string} title 新标题。
   * @returns {Promise<void>} 已保存，不改历史语义。
   */
  async renameChat(chatId, title) {}

  /**
   * @param {string[]} chatIds 完整有序 ID，原子校验无遗漏/重复。
   * @returns {Promise<void>} 仅保存索引，不重写全部 Chat。
   */
  async setChatOrder(chatIds) {}

  /**
   * @param {string} chatId 目标。
   * @param {import('../../shared/contracts/settings.js').ScenarioSettings | null} options 情景。
   * @returns {Promise<void>} 后续轮次使用。
   */
  async setScenario(chatId, options) {}

  /**
   * @param {string} chatId 目标。
   * @param {import('../../shared/contracts/settings.js').ToolSelection} options 普通工具，校验依赖。
   * @returns {Promise<void>} 已保存，不改本轮工具集合。
   */
  async setTools(chatId, options) {}

  /**
   * @param {string} chatId 目标。
   * @param {import('../../shared/contracts/worldbook.js').WorldbookInfo} options 知识范围。
   * @returns {Promise<void>} 后续使用，不清除历史已泄露的知识。
   */
  async setWorldbookInfo(chatId, options) {}

  /**
   * 换装不改形态，形态选择不改目录默认；当前 Agent 约定不变，下轮读取新目录。
   * 演出更新不重播音频；V2 按情绪动作组、V3 按实际模型逐通道降级。
   * @param {string} chatId 目标。
   * @param {string} characterId 参与角色。
   * @param {import('../../shared/contracts/settings.js').CharacterSelection} options 当前形态及各形态模型覆盖。
   * @returns {Promise<void>} 已校验保存并协调场景更新。
   */
  async setCharacterSelection(chatId, characterId, options) {}

  /**
   * @param {string} chatId 目标。
   * @param {import('../../shared/contracts/settings.js').GenerationOptions} options 完整覆盖集合，空对象恢复继承。
   * @returns {Promise<void>} 后续执行使用。
   */
  async setGenerationOptions(chatId, options) {}

  /**
   * @param {string} chatId 目标。
   * @param {import('../../shared/contracts/settings.js').SpeechOptions} options 完整覆盖集合。
   * @returns {Promise<void>} 自动语音开关下轮生效，不改当前轮后续台词。
   */
  async setSpeechOptions(chatId, options) {}

  /**
   * @param {string} chatId 目标。
   * @param {import('../../shared/contracts/settings.js').ChatPresentationSettings} options 背景/布局/朝向。
   * @returns {Promise<void>} 保存并更新场景，不失效 async。
   */
  async setPresentation(chatId, options) {}

  /**
   * @param {string} chatId 所属对话。
   * @returns {Promise<import('../../shared/contracts/conversation.js').Reminder[]>} 包含 delivered/expired 的记录。
   */
  async listReminders(chatId) {}

  /**
   * @param {string} chatId 所属对话。
   * @param {string} reminderId 身份。
   * @returns {Promise<import('../../shared/contracts/conversation.js').Reminder | null>} 记录或不存在。
   */
  async getReminder(chatId, reminderId) {}

  /**
   * @param {string} chatId 所属对话。
   * @param {string} reminderId 身份。
   * @returns {Promise<void>} 已删除；不终止已经受理的提醒 Turn。
   */
  async removeReminder(chatId, reminderId) {}

  /**
   * 内部入口，仅给装配绑定的交互协调者，页面不能自报来源/组。登记成功不表示立即创建 Turn。
   * 校验请求身份和资格，重复结果幂等；running 全阶段只排队，空闲且允许受理才批量消费当时有效成功结果。
   * 同批用一条 UserMessage、新 Turn，后到结果留下一批，不等待其他交互。受理失败不消费队列。
   * failed/cancelled 只结算该请求，不触发回复；新手动/停止/历史修改与消费串行，重启旧请求拒收。
   * @param {import('../../shared/contracts/tools.js').InteractionCompletion} completion 已登记请求的最终结果。
   * @returns {Promise<'queued' | 'settled' | 'duplicate' | 'stale'>} 登记结果，不暴露组内部状态。
   */
  async acceptInteractionResult(completion) {}

  /**
   * 内部入口，仅提醒扫描器可调用。绑定所属 Chat、到期/12 小时补发及稳定投递 ID，忙碌延后。
   * 持久化输入和标记 delivered 必须协调去重；不消费草稿，无有效组时开启新组。
   * @param {string} chatId 所属对话。
   * @param {string} reminderId 已保存提醒身份。
   * @param {string} deliveryId 稳定投递身份。
   * @returns {Promise<string | null>} 已受理 TurnId，忙碌或过期时 null；状态经提醒查询反映。
   */
  async deliverReminder(chatId, reminderId, deliveryId) {}

  /**
   * 正常 running 阶段遵守历史修改互斥，拒绝删除；受理后再协调旧任务与资源清理。
   * @param {string} chatId 目标。
   * @returns {Promise<void>} 停止任务并删除存档和索引，仅按引用回收资源。
   */
  async deleteChat(chatId) {}

  /**
   * @param {string[]} chatIds 目标。
   * @returns {Promise<import('../../shared/contracts/common.js').AssetRef>} 已保存内容及必要资源的归档。
   */
  async exportChats(chatIds) {}

  /**
   * 导入分配新身份/revision 0，修正内部引用、停用复制提醒；不恢复组/任务，也不重新解析固定人格。
   * 缺图/音频/角色保留引用及可读历史，生成前另查可用性。
   * @param {import('../../shared/contracts/common.js').AssetRef} archive 归档。
   * @returns {Promise<import('../../shared/contracts/archive.js').ChatImportResult>} 新身份及结构化警告。
   */
  async importChats(archive) {}

  /**
   * 先当前快照后按运行版本发布，取消订阅不停止任务；观察回调不能阻塞推进。
   * @param {string} chatId 目标。
   * @param {(snapshot: import('../../shared/contracts/conversation.js').ConversationSnapshot) => void} listener 本地回调。
   * @returns {import('../../shared/contracts/common.js').Unsubscribe} 取消观察。
   */
  observe(chatId, listener) {}
}
