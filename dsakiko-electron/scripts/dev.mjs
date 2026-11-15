import { spawn } from 'node:child_process'
import { existsSync, lstatSync, mkdirSync, mkdtempSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { join } from 'node:path'

const root = fileURLToPath(new URL('../', import.meta.url))
const local = join(root, '.local')
const [mode, rawName, ...extra] = process.argv.slice(2)
const name = rawName?.normalize('NFC')
if (
  mode !== undefined &&
  (mode !== 'fresh' ||
    extra.length ||
    (name !== undefined && !/^[\p{L}\p{N}][\p{L}\p{N}_-]{0,63}$/u.test(name)))
) {
  console.error(
    '用法：pnpm dev 或 pnpm dev fresh [名称]。名称以字母或数字开头，最多 64 个字符，可包含下划线和连字符。'
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

if (existsSync(join(root, '.env.local'))) process.loadEnvFile(join(root, '.env.local'))
const env = { ...process.env }
delete env.ELECTRON_RUN_AS_NODE
// 已安装的本地 CLI 足够启动，不再嵌套调用可能联网切换版本的包管理器。
const executable = join(root, 'node_modules', 'electron-vite', 'bin', 'electron-vite.js')
const args = [executable, 'dev']
if (mode === 'fresh') {
  const profiles = ensureProfileDirectory(join(local, 'dev-fresh'))
  const profile = name
    ? ensureProfileDirectory(join(profiles, `named-${name}`))
    : mkdtempSync(join(profiles, 'run-'))
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
  stdio: 'inherit'
})
child.on('exit', (code) => {
  process.exitCode = code || 0
})
