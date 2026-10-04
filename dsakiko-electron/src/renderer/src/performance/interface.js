/* eslint no-unused-vars: ["error", { "args": "none" }] -- 接口原型保留参数名称，方法体刻意留空。 */

/** @typedef {import('../../../shared/contracts/presentation.js').SequenceId} SequenceId */
/** @typedef {import('../../../shared/contracts/presentation.js').GuideId} GuideId */
/** @typedef {import('../../../shared/contracts/presentation.js').Scene} Scene */
/** @typedef {import('../../../shared/contracts/presentation.js').PresentationMode} PresentationMode */

/**
 * 演出模块：持有多个 sequence，统一拥有演出进度、音频、字幕与 Live2D 动作调度。
 * 渲染与调度可以在内部拆分，但属于同一演出模块；普通演出与桌宠展示共用一个持续存活的调度实例。
 * sequence 不随 React 页面卸载或展示窗口切换而销毁；不同窗口不能各自维护并推进同一份队列。
 * 模式选择、展示端交接、DOM 挂载和回调路由属于内部实现；窗口创建、透明及鼠标穿透借助宿主的有限能力完成。
 * Conversation 使用相同的演出接口，不接收窗口、DOM 或模式专用的播放状态；宿主不承担逐句调度。
 *
 * 顺序：每个 sequence 严格按追加顺序执行；队首未 ready 就等待，绝不越过。
 * 前台：由实际语音、阅读计时和动作结束决定完成，等待所需部分中最晚的一项。
 * 长语音：可在语音或阅读仍进行时追加动作；基础时长结束后不再追加，等当前动作收尾。
 * 后台：按音频时长、文本阅读估计和动作时长估计推进，不输出音频或渲染。
 * 切换：转后台从已过时间继续；返回前台从当前未完成句的开头重播，已完成句不重播。
 * 空队列：前台进入默认展示，sequence 仍可追加；不需要 seal 或“以后不会再追加”的标志。
 * 故障：音频/动作失败或结束回调丢失时采用有上限的降级，不让队列无限卡住。
 * 无模型：仍播放文本和语音；ready 且没有任何可演出内容的 guide 以零时长正常完成。
 *
 * 需要：场景、guide、材料就绪信号，以及明确的前台选择或删除指令。
 * 不需要：Chat、Turn、TTS 状态、取消原因、对话编排方式、消息是否入档。
 * 终态和进度仅在需要时观察；不要求调用方为每批 guide 创建统一回执。
 * 这是审查用接口，方法均未实现。
 */
export class Performance {
  /**
   * 建立可持续追加的空 sequence，默认在后台；显式 setForeground 才选择它。
   * @param {Scene} scene 该 sequence 的场景。
   * @returns {Promise<SequenceId>} 临时 sequence 编号。
   */
  async createSequence(scene) {}

  /**
   * 为某个 sequence 追加一系列 guide，按相同顺序返回 guide 编号；任一输入无效则整批不追加。
   * 调用方不必等待前一批完成。原本为空的后台 sequence 也会开始推进。
   * @param {SequenceId} sequenceId 目标 sequence。
   * @param {import('../../../shared/contracts/presentation.js').Guide[]} guides 已完成文本与演出选择的材料。
   * @returns {Promise<GuideId[]>} 每条 guide 各自的编号。
   */
  async appendGuides(sequenceId, guides) {}

  /**
   * 写入语音材料并将 guide 标记 ready；audio 为 null 即无语音放行。
   * 只接受尚未 ready 的 guide；已放行或已经进入终态则返回 false，不能覆盖正在播放的内容。
   * @param {GuideId} guideId 目标 guide。
   * @param {import('../../../shared/contracts/presentation.js').GuideMaterials} materials 最终材料。
   * @returns {Promise<boolean>} 是否接受本次放行；调用方无需解释缺少音频的原因。
   */
  async resolveGuide(guideId, materials) {}

  /**
   * 删除指定 guide，包含正在播放的 guide；终止其音频、动作与等待，再按顺序推进。
   * 进入终态的 guide 不受影响，重复调用无副作用。
   * @param {GuideId[]} guideIds 需要删除的 guide。
   * @returns {Promise<GuideId[]>} 本次实际删除的编号。
   */
  async removeGuides(guideIds) {}

  /**
   * 原子删除给定集合中尚未开始的 guide，保留操作生效时正在播放的 guide。
   * 用于“停止后续演出”；调用方不能用先查询进度、再删除来替代，以免发生切句竞态。
   * 和 removeGuides 的区别：本函数保留操作时正在播放的 guide，前者会移除一个正在播放的 guide，导致播放立刻停止。
   * @param {GuideId[]} guideIds 需要检查的 guide 集合，演出模块无需知道它们属于哪一轮。
   * @returns {Promise<GuideId[]>} 实际删除的编号，用于取消对应的后续工作。
   */
  async discardPendingGuides(guideIds) {}

  /**
   * 明确选择唯一前台 sequence，原前台进入后台。重复选择当前前台不重播。
   * null 表示前台空闲；即使其他 sequence 有内容，也不自动提升到前台。
   * @param {SequenceId | null} sequenceId 目标 sequence。
   * @returns {Promise<void>} 前后台切换已生效。
   */
  async setForeground(sequenceId) {}

  /**
   * 更新模型、背景和布局；保留已有 slotId，存在未完成 guide 时不能删除它引用的演员位置。
   * 模型替换可重新开始当前动作，不重播音频、不重复报告 guide 完成。
   * @param {SequenceId} sequenceId 需要切换场景的目标 sequence。
   * @param {Scene} scene 新场景。
   * @returns {Promise<void>} 场景切换已受理；加载失败按无模型降级并报告问题。
   */
  async setScene(sequenceId, scene) {}

  /**
   * 设置没有可播放 guide 时的默认展示；例如空队列的 IDLE 或等待材料时的默认动作。
   * 不添加 guide，不影响业务等待，不表达“正在生成”等原因。
   * @param {SequenceId} sequenceId 目标 sequence。
   * @param {import('../../../shared/contracts/presentation.js').PerformanceSelection} selection 默认动作与表情。
   * @returns {Promise<void>} 默认展示已更新。
   */
  async setDefaultPresentation(sequenceId, selection) {}

  /**
   * 获取当前进度；精确切句仍由模块内部回调决定，调用方不能根据估算时间自行推进。
   * @param {SequenceId} sequenceId 目标 sequence。
   * @returns {Promise<import('../../../shared/contracts/presentation.js').SequenceSnapshot>} 只读演出快照。
   */
  async getProgress(sequenceId) {}

  /**
   * 先交付当前快照，再按版本交付进度及新终态。每个 guide 只进入终态一次。
   * @param {SequenceId} sequenceId 目标 sequence。
   * @param {(update: import('../../../shared/contracts/presentation.js').PerformanceUpdate) => void} listener 本地观察函数。
   * @returns {import('../../../shared/contracts/common.js').Unsubscribe} 取消观察，不改变演出。
   */
  observe(sequenceId, listener) {}

  /**
   * 按需等待某条 guide；也可在 Promise 完成后执行自己的回调，无需额外注册接口。
   * sequence 关闭前可读取已完成 guide 的最小终态，不保留其模型、音频或文本队列材料。
   * sequence 的删除、关闭必须结算已注册的等待（将所有 guide 视为取消）；sequence 关闭后，新发起的等待将被拒绝。
   * @param {GuideId} guideId 目标 guide。
   * @param {AbortSignal} [signal] 取消本次等待。
   * @returns {Promise<import('../../../shared/contracts/presentation.js').GuideFinish>} 播完、删除或关闭的终态。
   */
  async waitForGuideFinish(guideId, signal) {}

  /**
   * 关闭 sequence，停止其当前演出，结算等待并释放记录。关闭前台后前台空闲。
   * 旧渲染回调与迟到的材料不得重新激活它。重复关闭无副作用，后续不能再追加。
   * @param {SequenceId} sequenceId 目标 sequence。
   * @returns {Promise<void>} sequence 已关闭。
   */
  async closeSequence(sequenceId) {}

  /**
   * 切换整个演出模块的展示模式；不改变前台 sequence 的选择、场景内容、编号、队列或已注册的订阅与等待。
   * 普通模式与桌宠模式共用调度、音频及完成判断；切换请求按受理顺序执行，重复选择已生效模式不重建或重播。
   * 新展示端就绪后再交接，交接以生效时仍有效的前台和当前 guide 为准，准备期间的播放及控制操作仍可推进。
   * 需要重建播放时从当前未完成句开头重播；不重播已完成或已删除的 guide，不覆盖较新的前台选择。
   * 交接时避免双重发声，旧展示端的迟到回调不能推进新播放或重复报告 guide 完成。
   * 调用方只提供模式名称；页面容器绑定、窗口选择和跨窗口通信由模块内部处理。
   * @param {PresentationMode} mode 目标展示模式。
   * @returns {Promise<void>} 新模式已就绪并生效；环境不支持或切换失败时拒绝 Promise，保留原模式与有效演出状态。
   */
  async setPresentationMode(mode) {}
}
