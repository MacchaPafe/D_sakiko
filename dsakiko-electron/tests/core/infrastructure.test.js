import { mkdtemp, readdir, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { afterEach, expect, test, vi } from 'vitest'
import { SerialQueue, readJson, writeJson } from '../../src/backend/core/files.js'
import { Speech } from '../../src/backend/speech/interface.js'
import { PythonSpeechTransport } from '../../src/backend/speech/python-transport.js'

const directories = []
afterEach(async () => {
  vi.unstubAllGlobals()
  vi.useRealTimers()
  for (const directory of directories.splice(0))
    await rm(directory, { recursive: true, force: true })
})

test('串行操作失败不吞掉调用方错误，也不阻塞后续修改或其他身份', async () => {
  const queue = new SerialQueue()
  const order = []
  let release
  const first = queue.run('chat', async () => {
    await new Promise((resolve) => {
      release = resolve
    })
    order.push('first')
    throw new Error('保存失败')
  })
  const rejected = expect(first).rejects.toThrow('保存失败')
  const second = queue.run('chat', () => order.push('second'))
  await queue.run('other-chat', () => order.push('other'))
  expect(order).toEqual(['other'])
  release()
  await rejected
  await second
  expect(order).toEqual(['other', 'first', 'second'])
})

test('JSON 发布失败保留旧文件并清理临时文件，损坏 JSON 不能当作缺省值', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'dsakiko-files-'))
  directories.push(directory)
  const file = join(directory, 'settings.json')
  expect(await readJson(file, { revision: 0 })).toEqual({ revision: 0 })
  await writeJson(file, { revision: 1 })
  const cyclic = {}
  cyclic.self = cyclic
  await expect(writeJson(file, cyclic)).rejects.toThrow()
  expect(await readJson(file)).toEqual({ revision: 1 })
  expect(await readdir(directory)).toEqual(['settings.json'])
  await writeFile(file, '{invalid-json')
  await expect(readJson(file, { revision: 0 })).rejects.toBeInstanceOf(SyntaxError)
})

function speechFixture(fetcher) {
  const assets = {
    inspect: vi.fn(async () => ({})),
    localPath: (asset) => '/managed/' + asset.id,
    importAsset: vi.fn(async () => ({ asset: { id: 'audio' } }))
  }
  const connection = { url: 'http://127.0.0.1:12345', token: 'test-token', runId: 'runA' }
  vi.stubGlobal('fetch', fetcher)
  const speech = new Speech({ connect: async () => connection, assets })
  return { speech, assets, connection }
}

test('正式 Speech 接口转换请求，并发查询同一任务只导入一次成功音频', async () => {
  const requests = []
  const { speech, assets } = speechFixture(async (url, options) => {
    requests.push({ url, options })
    const value =
      options.method === 'POST'
        ? { task_id: 'runA-task' }
        : { status: 'succeeded', audio_path: '/temporary/result.wav', duration_ms: 1250 }
    return new Response(JSON.stringify(value))
  })
  const id = await speech.submit({
    text: '祥子你好',
    language: 'Chinese',
    priority: 1,
    speed: 1.2,
    sentencePauseMs: 400,
    pronunciationOverrides: [{ text: '祥子', reading: '小祥' }],
    voice: {
      gptWeights: { id: 'gpt' },
      sovitsWeights: { id: 'sovits' },
      referenceAudio: { id: 'reference' },
      referenceText: '参考',
      referenceLanguage: 'Japanese'
    },
    sampling: { topK: 15, topP: 0.9, temperature: 0.7, seed: 42, sampleSteps: 32 }
  })
  expect(id).toBe('runA-task')
  expect(JSON.parse(requests[0].options.body)).toMatchObject({
    text: '小祥你好',
    language: 'zh',
    speed: 1.2,
    sentence_pause_ms: 400,
    voice: { gpt_weights_path: '/managed/gpt', reference_language: 'ja' },
    sampling: { top_k: 15, top_p: 0.9, temperature: 0.7, seed: 42, sample_steps: 32 }
  })
  expect(requests[0].options.headers.Authorization).toBe('Bearer test-token')
  const results = await Promise.all([speech.getTask(id), speech.getTask(id)])
  expect(results[0]).toEqual(results[1])
  expect(results[0].audio).toEqual({ asset: { id: 'audio' }, durationMs: 1250 })
  expect(assets.importAsset).toHaveBeenCalledExactlyOnceWith(
    { kind: 'local-file', path: '/temporary/result.wav' },
    'speech:runA-task'
  )
})

test('取消一个 Speech 等待不取消共享轮询，Python 重启后旧任务不重新提交', async () => {
  vi.useFakeTimers()
  let reads = 0
  const fetcher = vi.fn(
    async () =>
      new Response(JSON.stringify(++reads === 1 ? { status: 'running' } : { status: 'cancelled' }))
  )
  const { speech, connection } = speechFixture(fetcher)
  const controller = new AbortController()
  const first = speech.waitForResult('runA-task', controller.signal)
  const second = speech.waitForResult('runA-task')
  const cancelled = expect(first).rejects.toHaveProperty('name', 'AbortError')
  controller.abort()
  await cancelled
  await vi.advanceTimersByTimeAsync(400)
  expect(await second).toMatchObject({ status: 'cancelled' })
  expect(fetcher).toHaveBeenCalledTimes(2)
  connection.runId = 'runB'
  await expect(speech.getTask('runA-task')).rejects.toHaveProperty(
    'problem.code',
    'task_unavailable'
  )
  expect(fetcher).toHaveBeenCalledTimes(2)
})

test('Python 提交通信失败只发送一次，无法确认结果时不自动重试副作用', async () => {
  const fetcher = vi.fn(async () => {
    throw new TypeError('连接中断')
  })
  vi.stubGlobal('fetch', fetcher)
  const transport = new PythonSpeechTransport(
    async () => ({ url: 'http://127.0.0.1:12345', token: 'test-token', runId: 'runA' }),
    { localPath: async () => '/managed/voice', importAudio: vi.fn() }
  )
  await expect(
    transport.submit({ text: '你好', language: 'zh', priority: 1, voice: {} })
  ).rejects.toHaveProperty('problem.code', 'speech_unavailable')
  expect(fetcher).toHaveBeenCalledTimes(1)
})
