/* eslint no-unused-vars: ["error", { "args": "none" }] -- 接口原型保留参数名称，方法体刻意留空。 */

/**
 * @typedef {object} RecognitionRequest
 * @property {import('../../shared/contracts/common.js').AssetRef} audio 待识别的录音资源引用。
 * @property {number} sampleRateHz 输入音频的采样率（Hz）。
 * @property {string | null} language 识别语言提示；传 null 时使用模型默认语言检测。
 */

/**
 * @typedef {{ status: 'succeeded', text: string }
 *   | { status: 'failed', problem: import('../../shared/contracts/common.js').Problem }
 *   | { status: 'cancelled' }} RecognitionResult
 */

/**
 * 语音识别（ASR）模块，负责将音频文件转换为文本。
 */
export class Recognition {
  /**
   * 预热并加载语音识别模型，以便后续录音完成后立即识别。
   * @param {AbortSignal} [signal] 控制本次等待的取消信号。
   * @returns {Promise<void>}
   */
  async prepare(signal) {}

  /**
   * 将指定的录音资源识别为文本。
   * @param {RecognitionRequest} request 语音识别请求参数（包含音频引用、采样率及语言提示）。
   * @param {AbortSignal} [signal] 控制本次识别的取消信号。
   * @returns {Promise<RecognitionResult>} 识别结果（包含成功文本、失败错误或取消状态）。
   */
  async transcribe(request, signal) {}
}
