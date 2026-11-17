import { once } from 'node:events'
import { spawn } from 'node:child_process'
import { cp, mkdir, mkdtemp, readdir, readFile, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { dirname, join } from 'node:path'
import { afterEach, expect, test, vi } from 'vitest'

const projects = []

afterEach(async () => {
  await Promise.all(projects.splice(0).map((root) => rm(root, { recursive: true, force: true })))
})

async function createProject(source) {
  const root = await mkdtemp(join(tmpdir(), 'dsakiko-dev-test-'))
  projects.push(root)
  const launcher = join(root, 'scripts', 'dev.mjs')
  const cli = join(root, 'node_modules', 'electron-vite', 'bin', 'electron-vite.js')
  await mkdir(dirname(launcher), { recursive: true })
  await mkdir(dirname(cli), { recursive: true })
  await cp(new URL('../../scripts/dev.mjs', import.meta.url), launcher)
  await writeFile(cli, source)
  return { root, launcher, profiles: join(root, '.local', 'dev-fresh') }
}

test.each([0, 7])('fresh-temp 在退出码 %i 时清理档案，普通 fresh 档案保留', async (code) => {
  const { root, launcher, profiles } = await createProject(`
    const { writeFileSync } = require('node:fs')
    const { join } = require('node:path')
    writeFileSync(join(process.env.DSAKIKO_DATA_DIR, 'state'), '存档')
    process.exit(${code})
  `)
  for (const args of [['fresh', '复用'], ['fresh'], ['fresh-temp']]) {
    const child = spawn(process.execPath, [launcher, ...args], { cwd: root, stdio: 'ignore' })
    expect(await once(child, 'close')).toEqual([code, null])
  }
  const directories = (await readdir(profiles)).sort()
  expect(directories).toEqual(['named-复用', expect.stringMatching(/^run-/)])
  for (const name of directories)
    expect(await readFile(join(profiles, name, 'core-v1', 'state'), 'utf8')).toBe('存档')
})

test.skipIf(process.platform === 'win32')(
  'Ctrl+C 等待仍在收尾的开发子进程，再清理档案',
  async () => {
    const worker = `
    const { writeFileSync } = require('node:fs')
    const { join } = require('node:path')
    const timer = setTimeout(() => process.exit(9), 5000)
    process.on('SIGINT', () => {
      setTimeout(() => {
        writeFileSync(join(process.env.DSAKIKO_DATA_DIR, 'state'), '已收尾')
        writeFileSync('finished', '已收尾')
        clearTimeout(timer)
      }, 100)
    })
    writeFileSync('ready', '就绪')
  `
    const { root, launcher, profiles } = await createProject(`
    const { spawn } = require('node:child_process')
    spawn(process.execPath, ['-e', ${JSON.stringify(worker)}], { stdio: 'inherit' })
  `)
    const child = spawn(process.execPath, [launcher, 'fresh-temp'], { cwd: root, stdio: 'ignore' })
    const closed = once(child, 'close')
    try {
      await vi.waitFor(() => readFile(join(root, 'ready')))
      expect(await readdir(profiles)).toEqual([expect.stringMatching(/^temp-/)])
      child.kill('SIGINT')
      expect(await closed).toEqual([130, null])
      expect(await readFile(join(root, 'finished'), 'utf8')).toBe('已收尾')
      expect(await readdir(profiles)).toEqual([])
    } finally {
      if (child.exitCode === null && child.signalCode === null) child.kill('SIGINT')
      await closed
    }
  }
)
