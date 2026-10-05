import { spawn } from 'node:child_process'
import { existsSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { join } from 'node:path'

const root = fileURLToPath(new URL('../', import.meta.url))
if (existsSync(join(root, '.env.local'))) process.loadEnvFile(join(root, '.env.local'))
process.env.DSAKIKO_DATA_DIR ||= join(root, '.local', 'data')
delete process.env.ELECTRON_RUN_AS_NODE
const executable = process.platform === 'win32' ? 'pnpm.cmd' : 'pnpm'
const child = spawn(executable, ['exec', 'electron-vite', 'dev'], {
  cwd: root,
  env: process.env,
  stdio: 'inherit',
  shell: process.platform === 'win32'
})
child.on('exit', (code) => {
  process.exitCode = code || 0
})
