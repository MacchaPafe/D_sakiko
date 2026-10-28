import { mkdtemp, mkdir, rm, writeFile } from 'node:fs/promises'
import path from 'node:path'
import os from 'node:os'
import { fileURLToPath } from 'node:url'
import wavefile from 'wavefile'
import { afterEach, expect, test } from 'vitest'
import { Recognition } from '../../src/backend/recognition/interface.js'
import { decodeAudio } from '../../src/backend/recognition/audio.js'

const cleanups = []
afterEach(async () => {
  for (const cleanup of cleanups.splice(0).reverse()) await cleanup()
})
async function fixture(options = {}) {
  const directory = await mkdtemp(path.join(os.tmpdir(), 'recognition-'))
  cleanups.push(() => rm(directory, { recursive: true, force: true }))
  await mkdir(path.join(directory, 'onnx'))
  for (const name of ['encoder_model_quantized.onnx', 'decoder_model_merged_quantized.onnx'])
    await writeFile(path.join(directory, 'onnx', name), 'fixture')
  let owners = 0
  const assets = {
    using: async (_, operation) => {
      owners++
      try {
        return await operation()
      } finally {
        owners--
      }
    },
    inspect: async () => ({ kind: 'file', sizeBytes: 1000 }),
    localPath: async () => path.join(directory, 'recording.wav')
  }
  const recognition = new Recognition({
    assets,
    modelDirectory: directory,
    workerPath: fileURLToPath(new URL('../fixtures/recognition-worker.mjs', import.meta.url)),
    ...options
  })
  cleanups.push(() => recognition.close())
  return { recognition, owners: () => owners }
}
const request = (id, language = 'zh') => ({
  requestId: id,
  audio: { id: 'audio' },
  sampleRateHz: 16000,
  language
})
async function running(recognition) {
  for (let index = 0; index < 100; index++) {
    if (recognition.getState().state === 'transcribing') return
    await new Promise((resolve) => setTimeout(resolve, 10))
  }
  throw Error('子进程未进入识别')
}

test('WAV 混音与重采样；拒绝采样率不一致、空音频和非有限样本', () => {
  const wav = new wavefile.WaveFile()
  wav.fromScratch(2, 48000, '32f', [
    new Float32Array(4800).fill(0.2),
    new Float32Array(4800).fill(0.4)
  ])
  const audio = decodeAudio(wav.toBuffer(), 48000)
  expect(audio).toBeInstanceOf(Float32Array)
  expect(audio.length).toBe(1600)
  expect(audio[800]).toBeCloseTo(0.3, 2)
  expect(() => decodeAudio(wav.toBuffer(), 16000)).toThrow()
  wav.fromScratch(1, 16000, '32f', new Float32Array(100))
  expect(() => decodeAudio(wav.toBuffer(), 16000)).toThrow()
  wav.fromScratch(1, 16000, '32f', new Float32Array(2000).fill(NaN))
  expect(() => decodeAudio(wav.toBuffer(), 16000)).toThrow()
})

test('精确取消、拒绝并发、忽略旧结果、取消后可重启，关闭前释放音频引用', async () => {
  const { recognition, owners } = await fixture()
  const first = recognition.transcribe(request('first', 'wait'))
  await running(recognition)
  expect(owners()).toBe(1)
  expect(await recognition.transcribe(request('busy'))).toMatchObject({
    status: 'failed',
    problem: { code: 'recognition_busy' }
  })
  expect(recognition.cancel('other')).toBe(false)
  expect(recognition.cancel('first')).toBe(true)
  expect(await first).toEqual({ status: 'cancelled' })
  expect(recognition.child).toBeNull()
  expect(owners()).toBe(0)
  expect(await recognition.transcribe(request('second'))).toEqual({
    status: 'succeeded',
    text: '本次识别结果'
  })
  const third = recognition.transcribe(request('third', 'wait'))
  await running(recognition)
  await recognition.close()
  expect(await third).toEqual({ status: 'cancelled' })
  expect(owners()).toBe(0)
  expect(recognition.getState().state).toBe('closed')
})

test('空闲释放、超时、进程崩溃与缺失模型可明确诊断', async () => {
  const { recognition } = await fixture({ idleMs: 20, timeoutMs: 300 })
  expect(await recognition.transcribe(request('first'))).toHaveProperty('status', 'succeeded')
  await new Promise((resolve) => setTimeout(resolve, 100))
  expect(recognition.child).toBeNull()
  expect(await recognition.transcribe(request('timeout', 'wait'))).toMatchObject({
    status: 'failed',
    problem: { code: 'recognition_timeout' }
  })
  expect(await recognition.transcribe(request('crash', 'crash'))).toMatchObject({
    status: 'failed',
    problem: { code: 'recognition_worker_failed' }
  })
  recognition.modelDirectory = path.join(recognition.modelDirectory, 'missing')
  expect(await recognition.transcribe(request('missing'))).toMatchObject({
    status: 'failed',
    problem: { code: 'recognition_model_missing' }
  })
})
