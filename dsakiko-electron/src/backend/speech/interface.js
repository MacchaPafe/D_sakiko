import { PythonSpeechTransport } from './python-transport.js'
import { abortable, requireValue } from '../core/support.js'

/** @typedef {import('../../shared/contracts/speech.js').SpeechTaskId} SpeechTaskId */
/** @typedef {import('../../shared/contracts/speech.js').SpeechPriority} SpeechPriority */

/**
 * 语音合成任务管理模块，负责向 Python 语音服务提交合成任务、查询结果、调整优先级与取消任务。
 */
export class Speech {
  /**
   * 创建一个语音合成管理器。
   * @param {object} options 创建选项。
   * @param {() => Promise<import('./python-transport.js').SpeechConnection>} options.connect 获取 Python 语音服务连接的函数。
   * @param {import('../resources/interface.js').MediaAssets} options.assets 媒体资源管理器，用于解析参考音频/模型权重路径并导入合成后的音频文件。
   */
  constructor({ connect, assets }) {
    this.waiting = new Map()
    this.assets = assets
    this.transport = new PythonSpeechTransport(connect, {
      localPath: async (asset) => {
        await assets.inspect(asset)
        return assets.localPath(asset)
      },
      importAudio: async (file, taskId) =>
        (await assets.importAsset({ kind: 'local-file', path: file }, 'speech:' + taskId, 'audio'))
          .asset
    })
  }

  /**
   * 提交一个语音合成任务。
   * @param {import('../../shared/contracts/speech.js').SpeechRequest} request 合成请求参数（包含待合成文本、声音配置、优先级等）。
   * @returns {Promise<SpeechTaskId>} Python 语音服务分配的任务 ID。
   */
  async submit(request) {
    requireValue(Number.isInteger(request.priority) && request.priority >= 0)
    const languages = { Japanese: 'ja', Chinese: 'zh' }
    return this.transport.submit({
      ...request,
      language: languages[request.language] ?? request.language,
      voice: {
        ...request.voice,
        referenceLanguage:
          languages[request.voice.referenceLanguage] ?? request.voice.referenceLanguage
      }
    })
  }

  /**
   * 查询指定语音合成任务的当前状态。
   * @param {SpeechTaskId} taskId 语音任务 ID。
   * @returns {Promise<import('../../shared/contracts/speech.js').SpeechTaskSnapshot>} 任务当前状态快照（若已成功则包含音频资源引用与时长）。
   */
  async getTask(taskId) {
    return this.transport.getTask(taskId)
  }

  /**
   * 等待指定的语音合成任务完成并返回最终结果。
   * @param {SpeechTaskId} taskId 语音任务 ID。
   * @param {AbortSignal} [signal] 控制本次等待的取消信号（取消仅结束当前调用方的等待，不会取消底层合成任务）。
   * @returns {Promise<import('../../shared/contracts/speech.js').SpeechResult>} 任务的最终结果（成功、失败或已取消）。
   */
  async waitForResult(taskId, signal) {
    if (!this.waiting.has(taskId)) {
      const poll = async () => {
        let failures = 0
        for (;;) {
          try {
            const result = await this.getTask(taskId)
            failures = 0
            if (['succeeded', 'failed', 'cancelled'].includes(result.status)) return result
          } catch (error) {
            failures += 1
            // 临时通信故障允许最多重试 3 次，避免永久卡在轮询循环中。
            if (!error.problem?.retryable || failures > 3) throw error
          }
          await new Promise((resolve) => setTimeout(resolve, 350 * (failures + 1)))
        }
      }
      // 同一 taskId 的多个等待者共享同一个轮询 Promise，配合外部 abortable 实现单独取消等待而不打断其他等待者。
      const pending = poll().finally(() => this.waiting.delete(taskId))
      this.waiting.set(taskId, pending)
    }
    return abortable(this.waiting.get(taskId), signal)
  }

  /**
   * 批量调整尚未开始执行的语音任务的排队优先级。
   * @param {SpeechTaskId[]} taskIds 要调整优先级的任务 ID 列表。
   * @param {SpeechPriority} priority 新的优先级数值（非负整数，数值越小越优先）。
   * @returns {Promise<void>}
   */
  async setPriority(taskIds, priority) {
    return this.transport.setPriority([...new Set(taskIds)], priority)
  }

  /**
   * 批量取消指定的未完成语音任务。
   * @param {SpeechTaskId[]} taskIds 要取消的任务 ID 列表。
   * @returns {Promise<void>}
   */
  async cancel(taskIds) {
    return this.transport.cancel([...new Set(taskIds)])
  }
}
