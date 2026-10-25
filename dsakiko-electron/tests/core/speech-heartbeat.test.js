import http from 'node:http'
import { mkdir, mkdtemp, readFile, readdir, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { expect, test } from 'vitest'
import { gptSovitsStartup } from '../helpers/gpt-sovits.js'
import { SpeechProcess } from '../../src/main/processes/speech-process.js'
import { MediaAssets } from '../../src/backend/resources/interface.js'

test('真实 Python 持续探测所属后端，运行身份改变后自主退出并解除资源保护', async () => {
  const directory = await mkdtemp(path.join(tmpdir(), 'dsakiko-heartbeat-'))
  let backendRunId = 'first-host'
  let probes = 0
  const host = http.createServer((request, response) => {
    expect(request.url).toBe('/api/v1/health')
    expect(request.headers.authorization).toBe('Bearer probe-only')
    probes += 1
    response.writeHead(200, { 'Content-Type': 'application/json' })
    response.end(JSON.stringify({ backendRunId, status: 'running' }))
  })
  let speech
  try {
    await new Promise((resolve, reject) => {
      host.once('error', reject)
      host.listen(0, '127.0.0.1', resolve)
    })
    const roots = {
      appDataRoot: path.join(directory, 'app'),
      resourceLibraryRoot: path.join(directory, 'library'),
      cacheRoot: path.join(directory, 'cache')
    }
    const assets = new MediaAssets(roots)
    await assets.initialize()
    const resource = (
      await assets.importAsset(
        {
          kind: 'bytes',
          name: 'model',
          mediaType: 'application/octet-stream',
          bytes: Buffer.from('model')
        },
        'source',
        'model'
      )
    ).asset
    const owner = await assets.createTemporaryOwner('speech-base', [resource])
    await assets.releaseOwner('source')
    const resources = gptSovitsStartup(await assets.localPath(resource))
    let released = false
    speech = new SpeechProcess(
      fileURLToPath(new URL('../../python', import.meta.url)),
      roots.cacheRoot,
      async () => ({
        gptSovits: resources,
        assetsRoot: roots.resourceLibraryRoot,
        pythonExecutable: 'python3',
        maxConcurrent: 1,
        maxResident: 1,
        hostProbe: {
          url: `http://127.0.0.1:${host.address().port}/api/v1/health`,
          token: 'probe-only',
          backendRunId: 'first-host'
        },
        bindResources: (pid, group) => assets.bindOwnerProcess(owner, pid, group),
        releaseResources: async (pid) => {
          await assets.releaseProcessOwner(owner, pid)
          released = true
        }
      })
    )
    await speech.connect()
    const runs = path.join(roots.cacheRoot, 'speech-temp')
    const [run] = await readdir(runs)
    await writeFile(path.join(runs, run, 'unclaimed.wav'), 'audio')
    await expect.poll(() => probes).toBeGreaterThan(0)
    expect((await assets.collectUnused()).deleted).toEqual([])
    backendRunId = 'replacement-host'
    await expect.poll(() => released, { timeout: 8000 }).toBe(true)
    expect(probes).toBeGreaterThan(1)
    expect((await assets.collectUnused()).deleted).toEqual([resource])
    await speech.close()
    // 宿主还活着但运行身份已经改变，保留可能仍在导入的音频供下次启动回收。
    expect(await readdir(runs)).toEqual([run])
    await expect(speech.connect()).rejects.toHaveProperty('problem.code', 'backend_closed')
  } finally {
    await speech?.close()
    await new Promise((resolve) => host.close(resolve))
    await rm(directory, { recursive: true, force: true })
  }
}, 12000)

test('正常关闭真实 Python 后清理本次输出与缓存，并保留模型文件', async () => {
  const directory = await mkdtemp(path.join(tmpdir(), 'dsakiko-shutdown-'))
  let speech
  try {
    const model = path.join(directory, 'model')
    await writeFile(model, 'model')
    const cache = path.join(directory, 'cache')
    speech = new SpeechProcess(
      fileURLToPath(new URL('../../python', import.meta.url)),
      cache,
      async () => ({
        gptSovits: gptSovitsStartup(model),
        assetsRoot: directory,
        pythonExecutable: 'python3',
        maxConcurrent: 1,
        maxResident: 1
      })
    )
    await speech.connect()
    const runs = path.join(cache, 'speech-temp')
    const [run] = await readdir(runs)
    const runtime = path.join(runs, run)
    const owner = JSON.parse(await readFile(path.join(runtime, '.owner.json'), 'utf8'))
    expect(owner.hostPid).toBe(process.pid)
    expect(owner.pythonPid).toBe(speech.child.pid)
    await writeFile(path.join(runtime, 'complete.wav'), 'audio')
    await writeFile(path.join(runtime, 'unfinished.wav.tmp.wav'), 'partial')
    const fonts = path.join(runtime, 'worker', 'matplotlib')
    await mkdir(fonts, { recursive: true })
    await writeFile(path.join(fonts, 'font-cache.json'), '{}')
    await speech.close()
    expect(await readdir(runs)).toEqual([])
    expect(await readFile(model, 'utf8')).toBe('model')
    await speech.close()
  } finally {
    await speech?.close()
    await rm(directory, { recursive: true, force: true })
  }
}, 12000)
