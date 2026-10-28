import { fork } from 'node:child_process'
import { access } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { failure, problem, requireValue, uuid } from '../core/support.js'

/**
 * @typedef {object} RecognitionRequest
 * @property {import('../../shared/contracts/common.js').AssetRef} audio WAV 录音资源引用。
 * @property {number} sampleRateHz WAV 原始采样率，须与文件头一致。
 * @property {string | null} language 识别语言提示；null 自动检测。
 * @property {string} [requestId] 调用方身份，供 cancel 精确取消，防止旧页面取消新任务。
 */
/** @typedef {{status: 'succeeded', text: string} | {status: 'failed', problem: object} | {status: 'cancelled'}} RecognitionResult */
/** @typedef {{state: 'idle'|'loading'|'ready'|'transcribing'|'failed'|'closed', requestId: string|null, progress: object|null}} RecognitionState */

/** 本地 Whisper 识别；独立进程执行，模型获取由宿主准备，运行时禁止联网下载。 */
export class Recognition {
  /** @param {{assets: object, modelDirectory: string, workerPath?: string, idleMs?: number, timeoutMs?: number}} options 本地路径与生命周期配置。 */
  constructor({
    assets,
    modelDirectory,
    workerPath = fileURLToPath(new URL('./worker.js', import.meta.url)),
    idleMs = 300000,
    timeoutMs = 300000
  }) {
    Object.assign(this, { assets, modelDirectory, workerPath, idleMs, timeoutMs })
    this.child = null
    this.active = null
    this.closed = false
    this.state = 'idle'
    this.progress = null
  }

  /** @returns {RecognitionState} 可轮询的模型加载与识别状态，不泄露本机路径。 */
  getState() {
    return { state: this.state, requestId: this.active?.id ?? null, progress: this.progress }
  }

  /** 单任务执行；取消时终止实际推理，避免仅取消等待却继续占用 CPU。 */
  async task(id, signal, operation) {
    requireValue(!this.closed, 'recognition_closed', '识别服务已关闭。')
    requireValue(!this.active, 'recognition_busy', '已有识别任务正在执行。')
    requireValue(typeof id === 'string' && id.length > 0 && id.length <= 200)
    clearTimeout(this.idleTimer)
    const controller = new AbortController()
    const combined = AbortSignal.any([
      controller.signal,
      AbortSignal.timeout(this.timeoutMs),
      ...(signal ? [signal] : [])
    ])
    let complete
    const done = new Promise((resolve) => {
      complete = resolve
    })
    this.active = { id, controller, done }
    const cancel = () => {
      void this.stopChild()
    }
    combined.addEventListener('abort', cancel, { once: true })
    try {
      combined.throwIfAborted()
      return await operation(combined)
    } catch (error) {
      if (combined.aborted) {
        if (combined.reason?.name === 'TimeoutError')
          throw failure('recognition_timeout', '语音识别超时。', true)
        throw new DOMException('识别已取消', 'AbortError')
      }
      await this.stopChild()
      this.state = 'failed'
      throw error
    } finally {
      combined.removeEventListener('abort', cancel)
      if (combined.aborted) await this.stopChild()
      this.active = null
      if (this.closed) this.state = 'closed'
      else if (this.child) {
        this.state = 'ready'
        this.idleTimer = setTimeout(() => {
          void this.stopChild()
        }, this.idleMs)
        this.idleTimer.unref()
      } else if (this.state !== 'failed') this.state = 'idle'
      complete()
    }
  }

  /** 确认本地模型存在后才启动工作进程。 */
  async startChild(signal) {
    await this.stopping
    if (this.child) return
    requireValue(
      path.isAbsolute(this.modelDirectory),
      'recognition_model_missing',
      '请配置本地 Whisper 模型目录。'
    )
    try {
      await access(path.join(this.modelDirectory, 'onnx/encoder_model_quantized.onnx'))
      await access(path.join(this.modelDirectory, 'onnx/decoder_model_merged_quantized.onnx'))
    } catch {
      throw failure(
        'recognition_model_missing',
        '本地 Whisper Small q8 模型未准备好，请先下载开发资源。'
      )
    }
    signal.throwIfAborted()
    const child = fork(this.workerPath, [], {
      env: { ...process.env, ELECTRON_RUN_AS_NODE: '1' },
      execArgv: [],
      stdio: ['ignore', 'ignore', 'ignore', 'ipc']
    })
    this.child = child
    child.on('error', () => {})
    child.once('exit', () => {
      if (this.child === child) this.child = null
    })
    this.state = 'loading'
  }

  /** 向工作进程请求一次操作，响应身份不匹配时忽略迟到消息。 */
  async invoke(kind, payload, signal) {
    await this.startChild(signal)
    signal.throwIfAborted()
    const child = this.child
    const id = uuid()
    return new Promise((resolve, reject) => {
      const finish = (error, value) => {
        child.off('message', message)
        child.off('error', failed)
        child.off('exit', exited)
        signal.removeEventListener('abort', aborted)
        if (error) reject(error)
        else resolve(value)
      }
      const failed = () =>
        finish(failure('recognition_worker_failed', '识别进程启动或运行失败。', true))
      const exited = () => failed()
      const aborted = () => finish(new DOMException('识别已取消', 'AbortError'))
      const message = (value) => {
        if (value.id !== id) return
        if (value.kind === 'progress') {
          this.progress = value.progress
          return
        }
        if (value.kind === 'state') {
          this.state = value.state
          return
        }
        if (value.problem)
          finish(failure(value.problem.code, value.problem.message, value.problem.retryable))
        else finish(null, value.result)
      }
      child.on('message', message)
      child.once('error', failed)
      child.once('exit', exited)
      signal.addEventListener('abort', aborted, { once: true })
      child.send({ id, kind, modelDirectory: this.modelDirectory, ...payload }, (error) => {
        if (error) failed()
      })
    })
  }

  /** @param {AbortSignal} [signal] 取消加载。 @returns {Promise<void>} 本地模型准备完成。 */
  async prepare(signal) {
    await this.task(uuid(), signal, (combined) => this.invoke('prepare', {}, combined))
  }

  /** @param {RecognitionRequest} request 录音与语言提示。 @param {AbortSignal} [signal] 取消本任务。 @returns {Promise<RecognitionResult>} 识别结果，不自动写入对话。 */
  async transcribe(request, signal) {
    try {
      return await this.task(request.requestId ?? uuid(), signal, async (combined) => {
        requireValue(
          Number.isInteger(request.sampleRateHz) &&
            request.sampleRateHz >= 8000 &&
            request.sampleRateHz <= 192000,
          'invalid_audio',
          '录音采样率无效。'
        )
        requireValue(
          request.language == null ||
            (typeof request.language === 'string' && request.language.length <= 40),
          'invalid_audio'
        )
        return this.assets.using(request.audio, async () => {
          const info = await this.assets.inspect(request.audio)
          requireValue(
            info.kind === 'file' && info.sizeBytes > 44 && info.sizeBytes <= 32 * 1024 * 1024,
            'invalid_audio',
            '请提供不超过 32 MiB 的 WAV 录音。'
          )
          const file = await this.assets.localPath(request.audio)
          const result = await this.invoke(
            'transcribe',
            { file, sampleRateHz: request.sampleRateHz, language: request.language ?? null },
            combined
          )
          return { status: 'succeeded', text: result.text }
        })
      })
    } catch (error) {
      if (error.name === 'AbortError') return { status: 'cancelled' }
      return { status: 'failed', problem: problem(error) }
    }
  }

  /** @param {string} requestId 当前任务身份。 @returns {boolean} 是否取消了匹配任务。 */
  cancel(requestId) {
    if (this.active?.id !== requestId) return false
    this.active.controller.abort()
    return true
  }

  /** 等待进程真正退出后才允许下一次加载，避免取消后叠加多个模型实例。 */
  async stopChild() {
    if (this.stopping) return this.stopping
    const child = this.child
    this.child = null
    this.progress = null
    if (!child) return
    this.stopping = new Promise((resolve) => {
      if (child.exitCode !== null || child.signalCode !== null) return resolve()
      child.once('exit', resolve)
      child.kill('SIGKILL')
    })
    await this.stopping
    this.stopping = null
    if (!this.closed) this.state = 'idle'
  }

  /** 关闭服务，取消识别并释放模型进程。 */
  async close() {
    this.closed = true
    clearTimeout(this.idleTimer)
    const active = this.active
    active?.controller.abort()
    await this.stopChild()
    await active?.done
    this.state = 'closed'
  }
}
