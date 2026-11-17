import { spawn, spawnSync } from 'node:child_process'
import { once } from 'node:events'
import { existsSync, lstatSync, mkdirSync, mkdtempSync, rmSync } from 'node:fs'
import { constants } from 'node:os'
import { fileURLToPath } from 'node:url'
import { join } from 'node:path'

const root = fileURLToPath(new URL('../', import.meta.url))
const local = join(root, '.local')
const [mode, rawName, ...extra] = process.argv.slice(2)
const name = rawName?.normalize('NFC')
const temporary = mode === 'fresh-temp'
if (
  mode !== undefined &&
  (temporary
    ? rawName !== undefined
    : mode !== 'fresh' ||
      extra.length ||
      (name !== undefined && !/^[\p{L}\p{N}][\p{L}\p{N}_-]{0,63}$/u.test(name)))
) {
  console.error(
    '用法：pnpm dev、pnpm dev fresh [名称] 或 pnpm dev fresh-temp。名称以字母或数字开头，最多 64 个字符，可包含下划线和连字符。'
  )
  process.exit(1)
}

/** @param {string} directory 调试档案或其直接子目录。 @returns {string} 拒绝借符号链接复用其他档案的存储。 */
function ensureProfileDirectory(directory) {
  mkdirSync(directory, { recursive: true, mode: 0o700 })
  if (!lstatSync(directory).isDirectory())
    throw new Error(`调试档案目录不能是符号链接：${directory}`)
  return directory
}

/** @param {import('node:child_process').ChildProcess} child 开发服务器进程。 @returns {Promise<void>} 等 Electron 关闭继承的输出管道后才允许清理临时档案。 */
async function waitForTemporaryRun(child) {
  child.stdout.pipe(process.stdout, { end: false })
  child.stderr.pipe(process.stderr, { end: false })
  const signals = ['SIGINT', 'SIGTERM', 'SIGHUP']
  let interrupted
  function stop(signal) {
    interrupted ??= signal
    if (!child.pid) return
    if (process.platform === 'win32') {
      if (child.exitCode === null && child.signalCode === null)
        spawnSync('taskkill.exe', ['/PID', String(child.pid), '/T', '/F'], {
          stdio: 'ignore',
          windowsHide: true
        })
      return
    }
    // Vite 可能先退出，向本次开发进程组发送信号才能覆盖 Electron。
    try {
      process.kill(-child.pid, signal)
    } catch (error) {
      if (error.code !== 'ESRCH') throw error
    }
  }
  for (const signal of signals) process.on(signal, stop)
  try {
    const [code, signal] = await once(child, 'close')
    const reason = interrupted ?? signal
    process.exitCode = reason ? 128 + constants.signals[reason] : (code ?? 0)
  } finally {
    for (const signal of signals) process.off(signal, stop)
  }
}

if (existsSync(join(root, '.env.local'))) process.loadEnvFile(join(root, '.env.local'))
const env = { ...process.env }
delete env.ELECTRON_RUN_AS_NODE
// 已安装的本地 CLI 足够启动，不再嵌套调用可能联网切换版本的包管理器。
const executable = join(root, 'node_modules', 'electron-vite', 'bin', 'electron-vite.js')
const args = [executable, 'dev']
let profile
if (mode !== undefined) {
  const profiles = ensureProfileDirectory(join(local, 'dev-fresh'))
  profile = name
    ? ensureProfileDirectory(join(profiles, `named-${name}`))
    : mkdtempSync(join(profiles, temporary ? 'temp-' : 'run-'))
}

try {
  if (profile) {
    // 调试档案必须覆盖 .env.local 和终端里的路径，以免写到日常使用的目录。
    env.DSAKIKO_DATA_DIR = ensureProfileDirectory(join(profile, 'core-v1'))
    env.DSAKIKO_RESOURCE_LIBRARY_DIR = ensureProfileDirectory(join(profile, 'library'))
    env.DSAKIKO_CACHE_DIR = ensureProfileDirectory(join(profile, 'cache'))
    const userData = ensureProfileDirectory(join(profile, 'electron'))
    // Electron 在业务启动前按 userData 获取实例锁。通过 CLI 参数在启动时就选定档案。
    args.push('--', `--user-data-dir=${userData}`)
    console.log(`引导调试档案：${profile}`)
  } else {
    env.DSAKIKO_DATA_DIR ||= join(local, 'core-v1')
    env.DSAKIKO_RESOURCE_LIBRARY_DIR ||= join(local, 'library')
    env.DSAKIKO_CACHE_DIR ||= join(local, 'cache')
  }
  const child = spawn(process.execPath, args, {
    cwd: root,
    env,
    detached: temporary && process.platform !== 'win32',
    stdio: temporary ? ['inherit', 'pipe', 'pipe'] : 'inherit'
  })
  if (temporary) await waitForTemporaryRun(child)
  else {
    const [code] = await once(child, 'exit')
    process.exitCode = code ?? 0
  }
} finally {
  if (temporary) {
    try {
      rmSync(profile, { recursive: true, force: true, maxRetries: 3 })
      console.log(`已清理临时档案：${profile}`)
    } catch (error) {
      console.error(`无法清理临时档案 ${profile}：${error.message}`)
      process.exitCode ||= 1
    }
  }
}
