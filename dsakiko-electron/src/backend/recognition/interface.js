/* eslint no-unused-vars: ["error", { "args": "none" }] -- 接口原型保留参数名称，方法体刻意留空。 */

/**
 * @typedef {object} RecognitionRequest
 * @property {import('../../shared/contracts/common.js').AssetRef} audio 已导入且可读取的录音资源。
 * @property {number} sampleRateHz 输入采样率；声道转换和重采样由识别模块处理。
 * @property {string | null} language 识别语言提示；null 使用模型默认行为。
 */

/**
 * @typedef {{ status: 'succeeded', text: string }
 *   | { status: 'failed', problem: import('../../shared/contracts/common.js').Problem }
 *   | { status: 'cancelled' }} RecognitionResult
 */

/**
 * 识别模块：使用 Node 识别引擎，将一份音频转换为文本；管理模型加载与空闲释放。
 * 需要：音频、采样率及识别选项。
 * 不需要：麦克风权限、录音设备操作、Chat、输入框光标、草稿或自动发送策略。
 * 这是审查用接口，方法未实现。
 */
export class Recognition {
  /**
   * 按需唤醒模型，可供录音前准备；并发准备可共享，一个等待取消不影响其他等待者。
   * 模型准备失败允许重试；取消后不得迟到启动录音，模型空闲释放仍由本模块负责。
   * @param {AbortSignal} signal 取消本次等待。
   * @returns {Promise<void>} 引擎可接受识别；输入模块负责 preparing 状态。
   */
  async prepare(signal) {}

  /**
   * 发起一次独立识别，必要时准备模型；空/损坏音频必须校验，语言规范化暂缓。
   * 内部调度不得长时间阻塞 Node 事件循环。
   * 调用方用自己的请求编号关联结果，不需要了解识别线程或模型实例。
   * @param {RecognitionRequest} request 识别输入。
   * @param {AbortSignal} signal 取消本次识别，迟到结果不再交付为成功。
   * @returns {Promise<RecognitionResult>} 完整识别结果。
   */
  async transcribe(request, signal) {}
}
