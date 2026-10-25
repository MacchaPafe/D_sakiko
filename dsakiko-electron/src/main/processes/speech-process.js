import { processUsesResources } from '../../backend/resources/process.js'
import { spawn } from 'node:child_process'
import { randomBytes } from 'node:crypto'
import { mkdir } from 'node:fs/promises'
import { createWriteStream } from 'node:fs'
import { join, delimiter } from 'node:path'
import { problemError } from '../../shared/problems.js'

/**
 * @typedef {object} SpeechProcessSettings
 * @property {(confirmedPid?: number | null) => Promise<void>} [releaseResources] 进程退出后释放基础资源。
 * @property {(pid: number, group?: number) => Promise<void>} [bindResources] 将基础资源引用绑定至真实进程。
 * @property {import('../../shared/contracts/api.js').HostProbe} [hostProbe] 所属后端的只读探测凭据。
 * @property {{resources: object, precision: string, architecture: string, device: string}} gptSovits 已解析绝对路径的 GPT-SoVITS 启动配置。
 * @property {string} pythonExecutable 宿主配置的解释器。
 * @property {string} assetsRoot 应用管理的角色权重与参考音频目录。
 * @property {number} maxConcurrent Python 最大并发推理数。
 * @property {number} maxResident Python 最大驻留模型数。
 */

/** 宿主只管理 Python 生命周期与就绪，不参与语音任务调度。 */
export class SpeechProcess {
  /**
   * 绑定 Python 入口与启动配置，首次请求连接时才创建子进程。
   * @param {string} pythonRoot 包含 speech 模块的目录，加入子进程模块搜索路径。
   * @param {string} cacheRoot 日志和临时音频目录。
   * @param {() => Promise<SpeechProcessSettings>} getSettings 启动时读取一次的宿主配置。
   */
  constructor(pythonRoot, cacheRoot, getSettings) {
    Object.assign(this, { pythonRoot, cacheRoot, getSettings })
    this.child = null
    this.starting = null
    this.resourceCleanups = new Set()
    this.closed = false
  }
  /**
   * 获取就绪连接；并发合成请求共享一次启动，避免启动多个调度器。
   * @returns {Promise<import('../../backend/speech/python-transport.js').SpeechConnection>} 当前 Python 进程的鉴权连接。
   */
  async connect() {
    if (this.closed) throw problemError('backend_closed', '语音宿主已关闭。')
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
    let cleanup = null
    const release = (confirmedPid = null) => {
      if (cleanup) return cleanup
      cleanup = Promise.resolve()
        .then(() => settings.releaseResources?.(confirmedPid))
        .catch((error) => console.error('基础资源释放失败', error))
      this.resourceCleanups.add(cleanup)
      void cleanup.finally(() => this.resourceCleanups.delete(cleanup))
      return cleanup
    }
    try {
      return await this.launch(settings, release)
    } catch (error) {
      if (!this.child) release()
      throw error
    }
  }

  /** 资源由启动前创建的 owner 保护，就绪后绑定 PID，退出后再释放。 */
  async launch(settings, release) {
    if (!settings.gptSovits?.resources)
      throw problemError('missing_resources', '请配置 GPT-SoVITS 文件清单。')
    await mkdir(join(this.cacheRoot, 'logs'), { recursive: true })
    const token = randomBytes(32).toString('hex')
    const log = createWriteStream(join(this.cacheRoot, 'logs', 'speech.log'), { flags: 'a' })
    const child = spawn(
      settings.pythonExecutable,
      [
        '-B',
        '-u',
        '-m',
        'speech.server',
        '--output',
        join(this.cacheRoot, 'speech-temp'),
        '--assets-root',
        settings.assetsRoot,
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
          DSAKIKO_SPEECH_HOST_PID: String(process.pid),
          DSAKIKO_HOST_PROBE: settings.hostProbe ? JSON.stringify(settings.hostProbe) : '',
          PYTHONUNBUFFERED: '1',
          PYTHONDONTWRITEBYTECODE: '1',
          PYTHONPATH: [this.pythonRoot, process.env.PYTHONPATH].filter(Boolean).join(delimiter)
        },
        detached: process.platform !== 'win32',
        stdio: ['pipe', 'pipe', 'pipe']
      }
    )
    this.child = child
    // spawn 失败或子进程提前退出时，关闭的 stdin 不能产生未处理的 EPIPE。
    child.stdin.on('error', () => {})
    // 通过启动管道传清单，避免命令行和环境变量长度限制，也不落盘模型视图。
    child.stdin.end(JSON.stringify(settings.gptSovits) + '\n')
    this.childRelease = new Promise((resolve) => {
      let finished = false
      const finish = () => {
        if (finished) return
        finished = true
        const confirmed = this.treeStopped(child) ? child.pid : null
        if (!this.child || this.child === child) this.lastConfirmedPid = confirmed
        if (this.child === child) {
          this.child = null
          this.connection = null
        }
        void Promise.resolve(release(confirmed)).then(resolve)
      }
      child.once('exit', () => {
        if (this.child === child) {
          this.child = null
          this.connection = null
        }
        if (process.platform === 'win32') finish()
      })
      // POSIX 等待继承管道关闭；spawn 失败没有 exit，也由 close 补齐释放。
      child.once('close', finish)
    })
    child.stderr.pipe(log, { end: false })
    child.once('close', () => log.end())
    return new Promise((resolve, reject) => {
      let buffer = ''
      let settled = false
      const timer = setTimeout(() => fail('Python 启动超时，请检查解释器与语音日志。'), 20000)
      const fail = (message) => {
        if (settled) return
        settled = true
        clearTimeout(timer)
        this.terminate(child, 'SIGTERM')
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
            const connection = {
              url: `http://127.0.0.1:${value.port}`,
              token,
              runId: value.runId,
              processId: child.pid,
              processGroupId: process.platform === 'win32' ? undefined : child.pid
            }
            Promise.resolve()
              .then(() => settings.bindResources?.(connection.processId, connection.processGroupId))
              .then(() => {
                if (this.child !== child)
                  throw problemError('speech_unavailable', 'Python 已退出。')
                this.connection = connection
                resolve(connection)
              })
              .catch((error) => {
                this.terminate(child, 'SIGTERM')
                reject(error)
              })
          }
        }
      })
    })
  }
  /**
   * 请求 Python 关闭并等待实际退出；worker 的退出确认由 Python 完成。
   * @returns {Promise<number | null>} 已确认退出的进程编号，否则保守保留引用。
   */
  async close() {
    this.closed = true
    await this.starting?.catch(() => {})
    const child = this.child
    try {
      await this.stop()
      if (!child) return this.lastConfirmedPid ?? null
      if (this.treeStopped(child)) return child.pid
      return null
    } finally {
      await this.childRelease
      await Promise.allSettled([...this.resourceCleanups])
    }
  }

  /** POSIX 继续查询整个进程组；Windows 以主进程退出为资源生命周期终点。 */
  treeStopped(child) {
    if (!child.pid) return true
    if (process.platform === 'win32') return child.exitCode !== null || child.signalCode !== null
    return !processUsesResources({ processId: child.pid, processGroupId: child.pid })
  }

  /** 启动失败时终止本次进程；正常关闭交给 Python 自身的退出确认流程。 */
  terminate(child, signal) {
    if (!child.pid) return
    try {
      if (process.platform === 'win32') child.kill(signal)
      else process.kill(-child.pid, signal)
    } catch (error) {
      if (error.code !== 'ESRCH') throw error
    }
  }

  /** 关闭请求失败时保留等待，由宿主心跳触发 Python 自主关闭；不提前释放引用。 */
  async stop() {
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
    await this.childRelease
  }
}
