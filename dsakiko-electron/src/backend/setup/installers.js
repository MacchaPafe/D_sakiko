import path from 'node:path'
import { pathToFileURL } from 'node:url'
import { mkdir, chmod, stat, rm } from 'node:fs/promises'
import { createWriteStream } from 'node:fs'
import { pipeline } from 'node:stream/promises'
import { Transform } from 'node:stream'
import { spawn } from 'node:child_process'
import unzipper from 'unzipper'
import * as tar from 'tar'
import { requireValue, failure } from '../core/support.js'
import { relativeFile, sourcePath } from './files.js'

/** 解包仅接受普通文件/目录；拒绝链接、路径穿越、大小超额和同名覆盖。 */
export async function extractArchive(file, directory, format, limit, signal) {
  await mkdir(directory, { recursive: true })
  let total = 0
  const names = new Set()
  function entry(name, size, type) {
    signal?.throwIfAborted()
    const relative = relativeFile(name.replace(/\/$/, ''))
    requireValue(['File', 'Directory'].includes(type), 'archive_link_denied')
    requireValue(!names.has(relative.toLowerCase()), 'archive_duplicate_path')
    names.add(relative.toLowerCase())
    total += size
    requireValue(total <= limit, 'archive_too_large')
    return relative
  }
  if (format === 'zip') {
    const archive = await unzipper.Open.file(file)
    for (const item of archive.files) {
      const mode = (item.externalFileAttributes >>> 16) & 0xf000
      requireValue(mode === 0 || mode === 0x8000 || mode === 0x4000, 'archive_link_denied')
      const name = entry(item.path, item.uncompressedSize, item.type)
      const destination = path.join(directory, name)
      if (item.type === 'Directory') {
        await mkdir(destination, { recursive: true })
        continue
      }
      await mkdir(path.dirname(destination), { recursive: true })
      let bytes = 0
      const counter = new Transform({
        transform(chunk, encoding, callback) {
          bytes += chunk.length
          if (bytes > item.uncompressedSize)
            return callback(failure('archive_too_large', '压缩包大小声明无效。'))
          callback(null, chunk)
        }
      })
      await pipeline(
        item.stream(),
        counter,
        createWriteStream(destination, { flags: 'wx', mode: 0o600 }),
        { signal }
      )
      requireValue(bytes === item.uncompressedSize, 'archive_size_mismatch')
    }
  } else {
    requireValue(format === 'tar.gz', 'unsupported_archive')
    // 先遍历验证再解包，避免 tar 的 filter 抛错时留下仍运行的解包流。
    let invalid = null
    await tar.t({
      file,
      strict: true,
      onReadEntry(item) {
        try {
          entry(item.path, item.size, item.type)
        } catch (error) {
          invalid ??= error
        }
      }
    })
    if (invalid) throw invalid
    signal?.throwIfAborted()
    await tar.x({
      file,
      cwd: directory,
      strict: true,
      preservePaths: false,
      noChmod: true,
      noMtime: true
    })
    signal?.throwIfAborted()
  }
}

/** 固定离线安装命令，清单不能提供命令行模板；输出不进入公开任务信息。 */
export async function runRuntimeCommand(executable, args, { cwd, signal, env = {} }) {
  signal?.throwIfAborted()
  await new Promise((resolve, reject) => {
    const child = spawn(executable, args, {
      cwd,
      env: { ...process.env, ...env },
      stdio: 'ignore',
      windowsHide: true,
      detached: process.platform !== 'win32'
    })
    let stopping = Promise.resolve()
    const abort = () => {
      if (!child.pid) return
      if (process.platform === 'win32')
        stopping = new Promise((done) => {
          const killer = spawn(
            path.join(process.env.SystemRoot ?? 'C:\\Windows', 'System32', 'taskkill.exe'),
            ['/PID', String(child.pid), '/T', '/F'],
            { stdio: 'ignore', windowsHide: true }
          )
          killer.once('error', () => {
            child.kill()
            done()
          })
          killer.once('exit', done)
        })
      else {
        try {
          process.kill(-child.pid, 'SIGKILL')
        } catch (error) {
          if (error.code !== 'ESRCH') child.kill('SIGKILL')
        }
      }
    }
    signal?.addEventListener('abort', abort, { once: true })
    child.once('error', (error) => {
      signal?.removeEventListener('abort', abort)
      reject(error)
    })
    child.once('close', async (code) => {
      await stopping
      signal?.removeEventListener('abort', abort)
      if (signal?.aborted) reject(new DOMException('环境安装已暂停', 'AbortError'))
      else if (code === 0) resolve()
      else reject(failure('runtime_validation_failed', '运行环境安装或验证失败。'))
    })
    if (signal?.aborted) abort()
  })
}

/** 在最终不可移动目录离线创建 GSV 环境，完成实际依赖导入才允许启用。 */
export async function prepareRuntime(component, directory, signal, run = runRuntimeCommand) {
  const windows = component.target.startsWith('win32-')
  if (component.id === 'asr.runtime') {
    const entry = await sourcePath(
      directory,
      'node_modules/@huggingface/transformers/dist/transformers.node.mjs'
    )
    await run(
      process.execPath,
      [
        '--input-type=module',
        '-e',
        'const m=await import(process.argv[1]); if(!m.pipeline)process.exit(1)',
        pathToFileURL(entry).href
      ],
      { cwd: directory, signal, env: { ELECTRON_RUN_AS_NODE: '1' } }
    )
    return { entry }
  }
  const python = await sourcePath(directory, windows ? 'python/python.exe' : 'python/bin/python3')
  const uv = await sourcePath(directory, windows ? 'uv/uv.exe' : 'uv/uv')
  const ffmpeg = await sourcePath(directory, windows ? 'bin/ffmpeg.exe' : 'bin/ffmpeg')
  await sourcePath(directory, 'requirements.lock')
  await sourcePath(directory, 'wheelhouse')
  if (!windows) {
    await chmod(python, 0o755)
    await chmod(uv, 0o755)
    await chmod(ffmpeg, 0o755)
  }
  const environment = path.join(directory, 'env')
  // 中断后只重建尚未启用的候选环境，已启用版本从不原地修改。
  await rm(environment, { recursive: true, force: true })
  const env = {
    UV_OFFLINE: '1',
    UV_PYTHON_DOWNLOADS: 'never',
    PIP_NO_INDEX: '1',
    HF_HUB_OFFLINE: '1',
    PYTHONNOUSERSITE: '1'
  }
  await run(uv, ['venv', '--python', python, environment, '--offline'], {
    cwd: directory,
    signal,
    env
  })
  const interpreter = path.join(environment, windows ? 'Scripts/python.exe' : 'bin/python')
  await run(
    uv,
    [
      'pip',
      'install',
      '--python',
      interpreter,
      '--no-index',
      '--find-links',
      path.join(directory, 'wheelhouse'),
      '--require-hashes',
      '--only-binary',
      ':all:',
      '-r',
      path.join(directory, 'requirements.lock')
    ],
    { cwd: directory, signal, env }
  )
  await run(
    interpreter,
    [
      '-I',
      '-c',
      'import torch,transformers,soundfile,pyopenjtalk,onnxruntime; assert torch.cuda.is_available() if ' +
        (component.target.endsWith('-cuda') ? 'True' : 'False') +
        ' else True'
    ],
    { cwd: directory, signal, env }
  )
  requireValue((await stat(interpreter)).isFile(), 'runtime_validation_failed')
  await run(ffmpeg, ['-version'], { cwd: directory, signal, env })
  return { pythonExecutable: interpreter, ffmpegExecutable: ffmpeg }
}
