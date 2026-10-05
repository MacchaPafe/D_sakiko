import { spawn } from 'node:child_process'
import { randomBytes } from 'node:crypto'
import { mkdir } from 'node:fs/promises'
import { createWriteStream } from 'node:fs'
import { join } from 'node:path'
import { problemError } from '../../shared/contracts/desktop.js'

/** 宿主只管理 Python 生命周期与就绪，不参与语音任务调度。 */
export class SpeechProcess {
  constructor(pythonRoot, dataRoot, getSettings) {
    Object.assign(this, { pythonRoot, dataRoot, getSettings })
    this.child = null
    this.starting = null
  }
  async connect() {
    if (this.connection) return this.connection
    if (!this.starting)
      this.starting = this.start().finally(() => {
        this.starting = null
      })
    return this.starting
  }
  async start() {
    const settings = this.getSettings()
    if (!settings.resourceRoot) throw problemError('missing_resources', '请配置资源目录。')
    await mkdir(join(this.dataRoot, 'logs'), { recursive: true })
    const token = randomBytes(32).toString('hex')
    const log = createWriteStream(join(this.dataRoot, 'logs', 'speech.log'), { flags: 'a' })
    const child = spawn(
      settings.pythonExecutable,
      [
        '-u',
        '-m',
        'speech.server',
        '--root',
        settings.resourceRoot,
        '--output',
        join(this.dataRoot, 'speech-temp'),
        '--device',
        settings.device,
        '--concurrent',
        String(settings.maxConcurrent),
        '--resident',
        String(settings.maxResident)
      ],
      {
        cwd: this.pythonRoot,
        env: { ...process.env, DSAKIKO_SPEECH_TOKEN: token, PYTHONUNBUFFERED: '1' },
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
