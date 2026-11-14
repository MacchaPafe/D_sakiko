import { spawn } from 'node:child_process'
import { existsSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { join } from 'node:path'

const root = fileURLToPath(new URL('../', import.meta.url))
if (existsSync(join(root, '.env.local'))) process.loadEnvFile(join(root, '.env.local'))
process.env.DSAKIKO_DATA_DIR ||= join(root, '.local', 'core-v1')
process.env.DSAKIKO_RESOURCE_LIBRARY_DIR ||= join(root, '.local', 'library')
process.env.DSAKIKO_CACHE_DIR ||= join(root, '.local', 'cache')
delete process.env.ELECTRON_RUN_AS_NODE
// 已安装的本地 CLI 足够启动，不再嵌套调用可能联网切换版本的包管理器。
const executable = join(root, 'node_modules', 'electron-vite', 'bin', 'electron-vite.js')
const child = spawn(process.execPath, [executable, 'dev'], {
  cwd: root,
  env: process.env,
  stdio: 'inherit'
})
child.on('exit', (code) => {
  process.exitCode = code || 0
})
