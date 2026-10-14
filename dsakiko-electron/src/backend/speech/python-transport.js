import { SerialQueue } from '../core/files.js'
import { problemError } from '../../shared/problems.js'

/** @typedef {{ url: string, token: string, runId: string }} SpeechConnection 宿主就绪握手提供的本机连接；runId 标识一次 Python 运行。 */
/**
 * @typedef {object} SpeechAssetAccess
 * @property {(asset: import('../../shared/contracts/common.js').AssetRef) => Promise<string>} localPath 可信资源引用解析为 Python 可读路径。
 * @property {(path: string, taskId: string) => Promise<import('../../shared/contracts/common.js').AssetRef>} importAudio 将临时结果交接到受管理资源。
 */

/** Python 协议适配器只负责传输与文件交接，不维护第二份推理队列；轮询策略由 Speech 接口统一实现。 */
export class PythonSpeechTransport {
  /** @type {Map<string, Promise<import('../../shared/contracts/presentation.js').AudioMaterial>>} 同任务的查询共享一次资源导入。 */
  results = new Map()
  controls = new SerialQueue()
  /**
   * 绑定进程就绪能力与资源交接能力，不在构造时启动 Python。
   * @param {() => Promise<SpeechConnection>} connect 获取当前进程的鉴权连接。
   * @param {SpeechAssetAccess} assets 后端专用文件能力，不接受浏览器提交的路径。
   */
  constructor(connect, assets) {
    this.connect = connect
    this.assets = assets
  }
  /**
   * 发送一次 Python 请求；提交结果不确定时不自动重交，避免重复推理。
   * @param {string} method HTTP 方法。
   * @param {string} path 协议内的相对路径。
   * @param {object | null} [body] 可选 JSON 请求体。
   * @param {string} [expectedRun] 已有任务所属的进程身份。
   * @returns {Promise<object | null>} Python 响应；204 无结果。
   */
  async request(method, path, body, expectedRun) {
    const connection = await this.connect()
    if (expectedRun && connection.runId !== expectedRun)
      throw problemError('task_unavailable', '语音进程已重启，旧任务不会自动重交。')
    let response
    try {
      response = await fetch(connection.url + path, {
        method,
        headers: {
          Authorization: `Bearer ${connection.token}`,
          'Content-Type': 'application/json'
        },
        body: body ? JSON.stringify(body) : undefined,
        signal: AbortSignal.timeout(15000)
      })
    } catch {
      throw problemError(
        'speech_unavailable',
        '语音服务通信中断；提交结果可能不确定，请检查语音日志。',
        true
      )
    }
    if (response.status === 204) return null
    const value = await response.json()
    if (!response.ok)
      throw Object.assign(new Error(value.problem?.message || '语音请求失败'), {
        problem: value.problem
      })
    return value
  }
  /**
   * 将固定的声音、读音替换和采样参数转换为 Python 任务材料。
   * @param {import('../../shared/contracts/speech.js').SpeechRequest} request 语言已由 Speech 接口转为 Python 使用的代码。
   * @returns {Promise<string>} Python 分配的任务身份；不等待合成结束。
   */
  async submit(request) {
    const voice = request.voice
    let text = request.text
    for (const override of request.pronunciationOverrides || [])
      if (override.text) text = text.split(override.text).join(override.reading)
    const body = {
      text,
      language: request.language,
      priority: request.priority,
      speed: request.speed ?? 1,
      sentence_pause_ms: request.sentencePauseMs ?? 300,
      voice: {
        gpt_weights_path: await this.assets.localPath(voice.gptWeights),
        sovits_weights_path: await this.assets.localPath(voice.sovitsWeights),
        reference_audio_path: await this.assets.localPath(voice.referenceAudio),
        reference_text: voice.referenceText,
        reference_language: voice.referenceLanguage
      }
    }
    if (request.sampling)
      body.sampling = {
        top_k: request.sampling.topK,
        top_p: request.sampling.topP,
        temperature: request.sampling.temperature,
        seed: request.sampling.seed,
        sample_steps: request.sampling.sampleSteps
      }
    return (await this.request('POST', '/speech/tasks', body)).task_id
  }
  /**
   * 读取任务快照，成功音频先导入受管理资源再返回。
   * @param {string} taskId 含进程身份的临时任务编号。
   * @returns {Promise<import('../../shared/contracts/speech.js').SpeechTaskSnapshot>} 不包含临时磁盘路径的快照。
   */
  async getTask(taskId) {
    const snapshot = await this.request(
      'GET',
      `/speech/tasks/${taskId}`,
      null,
      taskId.split('-')[0]
    )
    if (snapshot.status === 'succeeded') {
      if (!this.results.has(taskId)) {
        // 缓存 Promise 而非完成值，并发轮询才不会复制出多份音频；导入失败允许再试。
        const importing = this.assets
          .importAudio(snapshot.audio_path, taskId)
          .then((asset) => ({ asset, durationMs: snapshot.duration_ms }))
          .catch((error) => {
            this.results.delete(taskId)
            throw error
          })
        this.results.set(taskId, importing)
      }
      return { taskId, status: 'succeeded', audio: await this.results.get(taskId) }
    }
    return {
      taskId,
      status: snapshot.status,
      ...(snapshot.problem ? { problem: snapshot.problem } : {})
    }
  }
  /**
   * 按调用顺序向 Python 提交优先级变更；排队与执行仍由 Python 决定。
   * @param {string[]} taskIds 当前进程中的任务编号。
   * @param {number} priority 非负整数，越小越优先。
   * @returns {Promise<void>} Python 已受理变更。
   */
  async setPriority(taskIds, priority) {
    if (taskIds.length)
      await this.controls.run('control', () =>
        this.request(
          'POST',
          '/speech/tasks/priority',
          { task_ids: taskIds, priority },
          taskIds[0].split('-')[0]
        )
      )
  }
  /**
   * 取消所列任务；Python 确认取消不代表底层推理已释放设备。
   * @param {string[]} taskIds 当前进程中的任务编号。
   * @returns {Promise<void>} Python 已受理取消。
   */
  async cancel(taskIds) {
    if (taskIds.length)
      await this.controls.run('control', () =>
        this.request(
          'POST',
          '/speech/tasks/cancel',
          { task_ids: taskIds },
          taskIds[0].split('-')[0]
        )
      )
  }
}
