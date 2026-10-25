import http from 'node:http'
import { mkdtemp, mkdir, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { expect, test } from 'vitest'
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
    const resourceRoot = path.join(directory, 'runtime')
    await mkdir(path.join(resourceRoot, 'GPT_SoVITS/pretrained_models'), { recursive: true })
    let released = false
    speech = new SpeechProcess(
      fileURLToPath(new URL('../../python', import.meta.url)),
      roots.cacheRoot,
      async () => ({
        resourceRoot,
        pythonExecutable: 'python3',
        device: 'cpu',
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
    await expect.poll(() => probes).toBeGreaterThan(0)
    expect((await assets.collectUnused()).deleted).toEqual([])
    backendRunId = 'replacement-host'
    await expect.poll(() => released, { timeout: 8000 }).toBe(true)
    expect(probes).toBeGreaterThan(1)
    expect((await assets.collectUnused()).deleted).toEqual([resource])
    await speech.close()
    await expect(speech.connect()).rejects.toHaveProperty('problem.code', 'backend_closed')
  } finally {
    await speech?.close()
    await new Promise((resolve) => host.close(resolve))
    await rm(directory, { recursive: true, force: true })
  }
}, 12000)
