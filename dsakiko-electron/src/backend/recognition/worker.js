import { readFile } from 'node:fs/promises'
import { pipeline, env } from '@huggingface/transformers'
import { decodeAudio } from './audio.js'

env.allowRemoteModels = false
env.allowLocalModels = true
env.useFSCache = false
let transcriber

/** 子进程只接收父进程已解析的本地文件；宿主断开时立即退出，避免孤儿推理进程。 */
process.on('disconnect', () => process.exit(0))
process.on('message', async (request) => {
  const send = (value) => {
    if (process.connected) process.send({ id: request.id, ...value })
  }
  try {
    let audio
    if (request.kind === 'transcribe') {
      try {
        audio = decodeAudio(await readFile(request.file), request.sampleRateHz)
      } catch (error) {
        send({
          problem: error.problem ?? {
            code: 'invalid_audio',
            message: '无法解码 WAV 录音。',
            retryable: false
          }
        })
        return
      }
    }
    if (!transcriber) {
      send({ kind: 'state', state: 'loading' })
      transcriber = await pipeline('automatic-speech-recognition', request.modelDirectory, {
        device: 'cpu',
        dtype: 'q8',
        local_files_only: true,
        session_options: { intraOpNumThreads: 4, interOpNumThreads: 1 },
        progress_callback: (event) =>
          send({
            kind: 'progress',
            progress: { status: event.status, file: event.file, progress: event.progress }
          })
      })
    }
    if (request.kind === 'prepare') {
      send({ result: null })
      return
    }
    send({ kind: 'state', state: 'transcribing' })
    const options = { task: 'transcribe', chunk_length_s: 30, stride_length_s: 5 }
    if (request.language) options.language = request.language
    const result = await transcriber(audio, options)
    send({ result: { text: result.text.trim() } })
  } catch {
    send({
      problem: {
        code: 'recognition_failed',
        message: '本地语音识别失败，请检查模型文件及识别语言。',
        retryable: true
      }
    })
  }
})
