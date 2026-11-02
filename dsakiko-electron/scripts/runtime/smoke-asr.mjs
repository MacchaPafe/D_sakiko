import path from 'node:path'
import { pathToFileURL } from 'node:url'
import { readFile } from 'node:fs/promises'
import wavefile from 'wavefile'
import { decodeAudio } from '../../src/backend/recognition/audio.js'

const [entry, model, audio, language] = process.argv.slice(2)
if (!entry || !model || !audio) throw new Error('需要依赖入口、Whisper 模型目录和 WAV 音频路径')
const { pipeline, env } = await import(pathToFileURL(path.resolve(entry)).href)
env.allowRemoteModels = false
env.allowLocalModels = true
env.useFSCache = false
const transcriber = await pipeline('automatic-speech-recognition', path.resolve(model), {
  device: 'cpu',
  dtype: 'q8',
  local_files_only: true,
  session_options: { intraOpNumThreads: 4, interOpNumThreads: 1 }
})
try {
  const bytes = await readFile(audio)
  const wav = new wavefile.WaveFile(bytes)
  const options = { task: 'transcribe' }
  if (language) options.language = language
  const result = await transcriber(decodeAudio(bytes, wav.fmt.sampleRate), options)
  if (!result.text?.trim()) throw new Error('真实识别未返回文本')
  console.log('离线 ASR 推理通过：', result.text.trim())
} finally {
  await transcriber.dispose()
}
