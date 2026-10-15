import { SerialQueue } from '../core/files.js'
import { problemError } from '../../shared/problems.js'

/**
 * @typedef {object} SpeechConnection
 * @property {string} url Python 语音服务的本地 HTTP 地址。
 * @property {string} token 请求鉴权令牌。
 * @property {string} runId 当前 Python 进程运行实例的唯一标识。
 */

/**
 * @typedef {object} SpeechAssetAccess
 * @property {(asset: import('../../shared/contracts/common.js').AssetRef) => Promise<string>} localPath 将资源引用解析为本地文件路径。
 * @property {(path: string, taskId: string) => Promise<import('../../shared/contracts/common.js').AssetRef>} importAudio 将合成生成的临时音频文件导入到媒体资源库。
 */

/**
 * Python 语音服务 HTTP 传输客户端，负责与本地 Python 语音进程通信并交接音频文件。
 */
export class PythonSpeechTransport {
  /** @type {Map<string, Promise<import('../../shared/contracts/presentation.js').AudioMaterial>>} */
  results = new Map()
  controls = new SerialQueue()

  /**
   * 创建一个 Python 语音传输客户端。
   * @param {() => Promise<SpeechConnection>} connect 获取当前 Python 语音服务连接信息的函数。
   * @param {SpeechAssetAccess} assets 资源路径解析与音频导入回调。
   */
  constructor(connect, assets) {
    this.connect = connect
    this.assets = assets
  }

  /**
   * 向 Python 语音服务发送 HTTP 请求。
   * @param {string} method HTTP 请求方法。
   * @param {string} path 请求的接口相对路径。
   * @param {object | null} [body] JSON 请求体数据。
   * @param {string} [expectedRun] 期望的 Python 进程运行 ID（用于校验旧任务所属进程是否已重启）。
   * @returns {Promise<object | null>} 响应的 JSON 数据；状态码为 204 时返回 null。
   */
  async request(method, path, body, expectedRun) {
    const connection = await this.connect()
    // 为什么比对 expectedRun：
    // Python 进程重启后内存中的旧任务队列已丢失，核对 runId 可直接告知调用方旧任务已失效，避免向新进程查询不存在的任务。
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
      // 网络中断时无法确认 Python 是否已收到 POST 请求，因此不自动重发提交请求，防止重复合成。
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
   * 将合成请求转换为 Python 接口格式并提交任务。
   * @param {import('../../shared/contracts/speech.js').SpeechRequest} request 语音合成请求参数。
   * @returns {Promise<string>} Python 服务返回的任务 ID。
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
   * 查询语音任务状态；若任务已合成成功，则先将生成的音频导入媒体资源库再返回。
   * @param {string} taskId 任务 ID。
   * @returns {Promise<import('../../shared/contracts/speech.js').SpeechTaskSnapshot>} 任务状态快照。
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
        // 为什么缓存导入过程的 Promise 而不是等导入完成后再缓存结果：
        // 防止多个调用方同时查询同一个刚完成的任务时，触发多次重复的文件导入。
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
   * 向 Python 服务发送批量修改任务优先级的请求。
   * @param {string[]} taskIds 任务 ID 列表。
   * @param {number} priority 新优先级数值。
   * @returns {Promise<void>}
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
   * 向 Python 服务发送批量取消任务的请求。
   * @param {string[]} taskIds 要取消的任务 ID 列表。
   * @returns {Promise<void>}
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
