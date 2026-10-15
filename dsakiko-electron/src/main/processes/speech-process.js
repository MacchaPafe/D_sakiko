import { spawn } from 'node:child_process'
import { randomBytes } from 'node:crypto'
import { mkdir, stat } from 'node:fs/promises'
import { createWriteStream } from 'node:fs'
import { join, delimiter } from 'node:path'
import { problemError } from '../../shared/problems.js'

/**
 * @typedef {object} SpeechProcessSettings
 * @property {string} resourceRoot 只读基础权重和词典目录。
 * @property {string} pythonExecutable 宿主配置的解释器。
 * @property {string} [assetsRoot] 应用管理的角色权重与参考音频目录。
 * @property {string} [precision] 模型精度，下次启动进程时生效。
 * @property {string} [architecture] 模型结构，下次启动进程时生效。
 * @property {string} device 推理设备。
 * @property {number} maxConcurrent Python 最大并发推理数。
 * @property {number} maxResident Python 最大驻留模型数。
 */

/** 宿主只管理 Python 生命周期与就绪，不参与语音任务调度。 */
export class SpeechProcess {
  /**
   * 绑定 Python 入口与启动配置，首次请求连接时才创建子进程。
   * @param {string} pythonRoot 包含 speech 模块的目录，作为子进程工作目录。
   * @param {string} cacheRoot 日志和临时音频目录。
   * @param {() => Promise<SpeechProcessSettings>} getSettings 启动时读取一次的宿主配置。
   */
  constructor(pythonRoot, cacheRoot, getSettings) {
    Object.assign(this, { pythonRoot, cacheRoot, getSettings })
    this.child = null
    this.starting = null
  }
  /**
   * 获取就绪连接；并发合成请求共享一次启动，避免启动多个调度器。
   * @returns {Promise<import('../../backend/speech/python-transport.js').SpeechConnection>} 当前 Python 进程的鉴权连接。
   */
  async connect() {
    if (this.connection) return this.connection
    if (!this.starting)
      this.starting = this.start().finally(() => {
        this.starting = null
      })
    return this.starting
  }
  /** 启动可信解释器并等待握手；端口和运行身份必须来自此次子进程，而非旧缓存。 */
  async start() {
    const settings = await this.getSettings()
    if (!settings.resourceRoot) throw problemError('missing_resources', '请配置资源目录。')
    await mkdir(join(this.cacheRoot, 'logs'), { recursive: true })
    if (
      !(
        await stat(join(settings.resourceRoot, 'GPT_SoVITS', 'pretrained_models')).catch(() => null)
      )?.isDirectory()
    )
      throw problemError('missing_resources', '资源库中缺少 GPT-SoVITS 基础模型。')
    const token = randomBytes(32).toString('hex')
    const log = createWriteStream(join(this.cacheRoot, 'logs', 'speech.log'), { flags: 'a' })
    const child = spawn(
      settings.pythonExecutable,
      [
        '-B',
        '-u',
        '-m',
        'speech.server',
        '--root',
        settings.resourceRoot,
        '--output',
        join(this.cacheRoot, 'speech-temp'),
        ...(settings.assetsRoot ? ['--assets-root', settings.assetsRoot] : []),
        '--precision',
        settings.precision ?? 'float32',
        '--architecture',
        settings.architecture ?? 'v2',
        '--device',
        settings.device,
        '--concurrent',
        String(settings.maxConcurrent),
        '--resident',
        String(settings.maxResident)
      ],
      {
        cwd: this.cacheRoot,
        env: {
          ...process.env,
          DSAKIKO_SPEECH_TOKEN: token,
          PYTHONUNBUFFERED: '1',
          PYTHONDONTWRITEBYTECODE: '1',
          PYTHONPATH: [this.pythonRoot, process.env.PYTHONPATH].filter(Boolean).join(delimiter)
        },
        stdio: ['ignore', 'pipe', 'pipe']
      }
    )
    this.child = child
    child.stderr.pipe(log, { end: false })
    child.once('exit', () => {
      if (this.child === child) {
        this.child = null
        this.connection = null
      }
      log.end()
    })
    return new Promise((resolve, reject) => {
      let buffer = ''
      let settled = false
      const timer = setTimeout(() => fail('Python 启动超时，请检查解释器与语音日志。'), 20000)
      function fail(message) {
        if (settled) return
        settled = true
        clearTimeout(timer)
        child.kill()
        reject(problemError('speech_unavailable', message, true))
      }
      child.once('error', () => fail('无法启动 Python，请检查解释器路径。'))
      child.once('exit', () => fail('Python 提前退出，请检查语音日志。'))
      child.stdout.on('data', (data) => {
        if (settled) {
          log.write(data)
          return
        }
        buffer += data.toString()
        // stdout 分块不等于完整一行，保留尾部直到握手 JSON 收齐。
        const rows = buffer.split('\n')
        buffer = rows.pop()
        for (const row of rows) {
          let value
          try {
            value = JSON.parse(row)
          } catch {
            log.write(row + '\n')
            continue
          }
          if (value.ready && Number.isInteger(value.port)) {
            settled = true
            clearTimeout(timer)
            this.connection = { url: `http://127.0.0.1:${value.port}`, token, runId: value.runId }
            resolve(this.connection)
          }
        }
      })
    })
  }
  /**
   * 先请求 Python 清理，超过期限再终止进程，避免阻塞 Electron 退出。
   * @returns {Promise<void>} 子进程已退出或已收到终止信号。
   */
  async close() {
    const child = this.child
    if (!child) return
    const connection = this.connection
    this.connection = null
    if (connection)
      await fetch(`${connection.url}/shutdown`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${connection.token}` },
        signal: AbortSignal.timeout(2000)
      }).catch(() => {})
    if (child.exitCode !== null) return
    await new Promise((resolve) => {
      const timer = setTimeout(() => {
        child.kill('SIGTERM')
        resolve()
      }, 8000)
      child.once('exit', () => {
        clearTimeout(timer)
        resolve()
      })
    })
  }
}
