import { SerialQueue } from '../storage/files.js'
import { problemError } from '../../shared/contracts/desktop.js'

function abortError() {
  return new DOMException('等待已取消', 'AbortError')
}

/** Node 只负责传输与文件交接，不维护第二份推理队列。 */
export class Speech {
  results = new Map()
  controls = new SerialQueue()
  constructor(connect, assets) {
    this.connect = connect
    this.assets = assets
  }
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
    return (await this.request('POST', '/speech/tasks', body)).task_id
  }
  async getTask(taskId) {
    const snapshot = await this.request(
      'GET',
      `/speech/tasks/${taskId}`,
      null,
      taskId.split('-')[0]
    )
    if (snapshot.status === 'succeeded') {
      if (!this.results.has(taskId)) {
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
  async waitForResult(taskId, signal) {
    while (!signal?.aborted) {
      const result = await this.getTask(taskId)
      if (signal?.aborted) throw abortError()
      if (['succeeded', 'failed', 'cancelled'].includes(result.status)) return result
      await new Promise((resolve) => setTimeout(resolve, 350))
    }
    throw abortError()
  }
}
