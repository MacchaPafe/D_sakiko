/* eslint no-unused-vars: ["error", { "args": "none" }] -- 接口原型保留参数名称，方法体刻意留空。 */

/** @typedef {import('../../shared/contracts/speech.js').SpeechTaskId} SpeechTaskId */
/** @typedef {import('../../shared/contracts/speech.js').SpeechPriority} SpeechPriority */

/**
 * 语音模块的 Node 客户端：转换合成输入、传递任务控制、等待结果，并将文件交给资源模块。
 * 排队、优先级执行、模型加载与复用、并发和资源预算全部由 Python 管理，Node 不维护第二份调度队列。
 * 请求中的资源引用通过受控本地资源访问能力解析为路径，读音覆盖转换成实际发声文本后再提交。
 * 成功文件导入资源模块后才向业务交付；同一任务的并发查询或等待复用同一次导入与结果。
 * 任务结果的临时资源引用保留至约定的结果保留期；调用方应在交付后为存档或演出建立自己的引用。
 * 任务编号由 Python 分配，重启后失效；不能把通信失败当作合成失败，或自动重新提交产生重复任务。
 *
 * 需要：实际发声文本、发音配置、读音覆盖、优先级数字及待控制的任务编号。
 * 不需要：Chat、Turn、任务组、角色关系、guide、播放进度、前台对话身份，或合成失败后的展示策略。
 * 批量编号由调用方关联；后续新任务必须显式携带当时的优先级，不隐式继承某个组。
 * 这是审查用接口，方法均未实现。
 */
export class Speech {
  /**
   * 提交一条语音请求；Python 完成校验并登记任务后返回，不等待模型加载或合成。
   * 文本和发音配置在受理时固定，后续修改角色配置不影响已提交任务。
   * @param {import('../../shared/contracts/speech.js').SpeechRequest} request 完整输入，包含初始优先级。
   * @returns {Promise<SpeechTaskId>} Python 分配的临时任务编号。
   */
  async submit(request) {}

  /**
   * 获取任务状态；成功时同时取得资源引用与音频时长，不单独暴露文件路径或模型状态。
   * 快照可能在交付前已被后续控制改变；调用方不能靠先查询再操作来实现原子控制。
   * @param {SpeechTaskId} taskId 任务编号。
   * @returns {Promise<import('../../shared/contracts/speech.js').SpeechTaskSnapshot>} 当前快照；未知、过期或 Python 重启前的编号拒绝查询。
   */
  async getTask(taskId) {}

  /**
   * 客户端封装对 getTask 的轮询；同一任务可共享查询，等待不会占用 Python 推理槽。
   * 保留期内已完成的任务返回同一结果；signal 只终止本次等待，不取消合成或其他等待者。
   * 通信故障采用有限重试，仍不能查询时拒绝 Promise；不能永远把未知任务当成排队中。
   * @param {SpeechTaskId} taskId 任务编号。
   * @param {AbortSignal} [signal] 仅取消本次等待。
   * @returns {Promise<import('../../shared/contracts/speech.js').SpeechResult>} 合成终态。
   */
  async waitForResult(taskId, signal) {}

  /**
   * 在 Python 内一次性修改所列任务中尚未开始任务的优先级，保留原提交顺序。
   * 运行中或已有终态的任务不受影响；空数组无副作用，重复编号只处理一次。
   * Python 先校验全部编号；有未知或过期编号则整批拒绝，不留下部分修改。
   * @param {SpeechTaskId[]} taskIds 需要调整的任务编号，不要求属于同一对话。
   * @param {SpeechPriority} priority 新优先级。
   * @returns {Promise<void>} 排队策略已更新。
   */
  async setPriority(taskIds, priority) {}

  /**
   * 在 Python 内一次性取消所列未完成任务；已进入终态的任务保持原结果。
   * 取消先于完成生效时，任务固定为 cancelled，底层迟到结果不能再覆盖为成功。
   * 正在推理的任务尽力中断，实际资源仍计入占用直到推理退出；不能因此超额启动新任务。
   * 空数组无副作用，重复编号只处理一次；未知或过期编号导致整批拒绝。
   * @param {SpeechTaskId[]} taskIds 要取消的任务编号。
   * @returns {Promise<void>} 取消已生效，不保证底层设备已立刻空闲。
   */
  async cancel(taskIds) {}
}
