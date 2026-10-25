import { extractAssetReferences } from '../resources/references.js'
import { PythonSpeechTransport } from './python-transport.js'
import { abortable, requireValue, fingerprint, SerialQueue } from '../core/support.js'

/** @typedef {import('../../shared/contracts/speech.js').SpeechTaskId} SpeechTaskId */
/** @typedef {import('../../shared/contracts/speech.js').SpeechPriority} SpeechPriority */
/**
 * @typedef {object} SpeechTaskOwnership
 * @property {string} inputOwner 合成期间持有输入参考音频与权重的临时持有者 ID。
 * @property {string} outputOwner 合成结果尚未被调用方接管前，持有输出音频的临时持有者 ID。
 * @property {boolean} terminal 底层语音任务是否已执行结束。
 * @property {boolean} released 调用方是否已接管或放弃该任务的结果。
 */

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
    /** @type {Map<string, SpeechTaskOwnership>} */
    this.tasks = new Map()
    this.retired = new Set()
    this.residentOwners = new Set()
    this.residentModels = new Map()
    this.lifecycle = new SerialQueue()
    this.runId = null
    this.closed = false
    this.connect = connect
    this.transport = new PythonSpeechTransport(connect, {
      localPath: async (asset) => {
        await assets.inspect(asset)
        return assets.localPath(asset)
      },
      importAudio: async (file, taskId) => {
        const task = this.tasks.get(taskId)
        requireValue(task && !task.released, 'task_unavailable')
        return (
          await assets.importAsset({ kind: 'local-file', path: file }, task.outputOwner, 'audio')
        ).asset
      }
    })
  }

  /**
   * 提交一个语音合成任务。
   * @param {import('../../shared/contracts/speech.js').SpeechRequest} request 合成请求参数（包含待合成文本、声音配置、优先级等）。
   * @returns {Promise<SpeechTaskId>} Python 语音服务分配的任务 ID。
   */
  async submit(request) {
    requireValue(Number.isInteger(request.priority) && request.priority >= 0)
    requireValue(!this.closed, 'backend_closed')
    const inputOwner = await this.assets.createTemporaryOwner(
      'speech-input',
      extractAssetReferences(request.voice)
    )
    let outputOwner
    let runId
    try {
      runId = await this.retainModels(request.voice, inputOwner)
      outputOwner = await this.assets.createTemporaryOwner('speech')
    } catch (error) {
      await this.assets.releaseWhenPossible(inputOwner)
      throw error
    }
    const languages = { Japanese: 'ja', Chinese: 'zh' }
    try {
      const taskId = await this.transport.submit(
        {
          ...request,
          language: languages[request.language] ?? request.language,
          voice: {
            ...request.voice,
            referenceLanguage:
              languages[request.voice.referenceLanguage] ?? request.voice.referenceLanguage
          }
        },
        runId
      )
      this.tasks.set(taskId, { inputOwner, outputOwner, terminal: false, released: false })
      return taskId
    } catch (error) {
      await this.assets.releaseWhenPossible(outputOwner)
      // 为什么提交请求报错时不能立即释放 inputOwner：
      // 网络超时或连接中断时 Python 可能已经收到并开始执行该任务，将 inputOwner 转入驻留集合等到进程结束再释放，可防止 Python 读取输入文件时文件被删。
      this.residentOwners.add(inputOwner)
      throw error
    }
  }

  /**
   * 为本次合成使用的模型权重建立进程级驻留保护，确保 Python 进程存活期间权重文件不会被清理。
   */
  async retainModels(voice, inputOwner) {
    return this.lifecycle.run('process', async () => {
      const connection = await this.connect()
      await this.assets.bindOwnerProcess(
        inputOwner,
        connection.processId,
        connection.processGroupId
      )
      if (this.runId && this.runId !== connection.runId) {
        this.residentModels.clear()
      }
      this.runId = connection.runId
      const references = [voice.gptWeights, voice.sovitsWeights]
      const key = fingerprint(references)
      if (this.residentModels.has(key)) return connection.runId
      const owner = await this.assets.createTemporaryOwner('speech-model', references)
      await this.assets.bindOwnerProcess(owner, connection.processId, connection.processGroupId)
      this.residentModels.set(key, owner)
      this.residentOwners.add(owner)
      return connection.runId
    })
  }

  /**
   * 查询指定语音合成任务的当前状态。
   * @param {SpeechTaskId} taskId 语音任务 ID。
   * @returns {Promise<import('../../shared/contracts/speech.js').SpeechTaskSnapshot>} 任务当前状态快照（若已成功则包含音频资源引用与时长）。
   */
  async getTask(taskId) {
    requireValue(!this.retired.has(taskId), 'task_unavailable')
    const task = this.tasks.get(taskId)
    try {
      const result = await this.transport.getTask(taskId, () => !task?.released)
      if (['succeeded', 'failed', 'cancelled'].includes(result.status) && task) {
        task.terminal = true
        // cancelled 仅表示用户可见终态，Python worker 可能仍在读取输入。
        if (result.status === 'cancelled') this.residentOwners.add(task.inputOwner)
        else await this.assets.releaseWhenPossible(task.inputOwner)
        if (task.released) await this.finishRelease(taskId, task)
      }
      return result
    } catch (error) {
      if (task && error.problem?.code === 'task_unavailable') {
        task.terminal = true
        task.released = true
        this.residentOwners.add(task.inputOwner)
        await this.finishRelease(taskId, task)
      }
      throw error
    }
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
          requireValue(!this.closed, 'backend_closed')
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
  /**
   * 通知语音管理器调用方已处理完该任务的结果（或已放弃等待），可以释放任务占用的临时资源。
   * @param {SpeechTaskId} taskId 语音任务 ID。
   * @returns {Promise<void>}
   */
  async releaseTask(taskId) {
    const task = this.tasks.get(taskId)
    if (!task) return
    task.released = true
    if (task.terminal) await this.finishRelease(taskId, task)
    // 若调用方提前放弃等待但底层任务仍在运行，需等任务真正结束后再释放资源。
    else void this.waitForResult(taskId).catch((error) => this.assets.reportMaintenance(error))
  }

  /**
   * 等待音频导入操作完成后，释放该任务的输出资源持有者并清理任务记录。
   */
  async finishRelease(taskId, task) {
    await this.transport.results.get(taskId)?.catch(() => {})
    this.transport.results.delete(taskId)
    await this.assets.releaseWhenPossible(task.outputOwner)
    this.retired.add(taskId)
    this.tasks.delete(taskId)
  }

  /**
   * 关闭语音管理器，取消所有未完成的语音任务并停止本地轮询等待。
   * @returns {Promise<void>}
   */
  async close() {
    if (this.closed) return
    this.closed = true
    const active = [...this.tasks].filter(([, task]) => !task.terminal).map(([id]) => id)
    await this.cancel(active).catch((error) => this.assets.reportMaintenance(error))
    await Promise.allSettled([...this.waiting.values()])
  }

  /** 隔离测试可不提供进程元数据；正式资源模块按进程树事实决定是否释放。 */
  async releaseProcessOwner(owner, confirmedPid) {
    if (this.assets.releaseProcessOwner) {
      await this.assets
        .releaseProcessOwner(owner, confirmedPid)
        .catch((error) => this.assets.reportMaintenance(error))
    } else await this.assets.releaseWhenPossible(owner)
  }

  /**
   * 在确认 Python 语音子进程已退出后，释放该进程占用的所有输入音频与驻留模型引用。
   * @returns {Promise<void>}
   */
  async processStopped(confirmedPid = null) {
    await this.close()
    for (const [id, task] of this.tasks) {
      await this.releaseProcessOwner(task.inputOwner, confirmedPid)
      await this.finishRelease(id, task)
    }
    for (const owner of this.residentOwners) await this.releaseProcessOwner(owner, confirmedPid)
    this.residentOwners.clear()
    this.residentModels.clear()
  }
}
