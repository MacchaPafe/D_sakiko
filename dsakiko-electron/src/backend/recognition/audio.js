import wavefile from 'wavefile'
import { requireValue } from '../core/support.js'

/**
 * 解码 WAV 并转成 Whisper 所需的 16 kHz 单声道 Float32，拒绝过长或非有限数据。
 * @param {Uint8Array} bytes WAV 文件字节。
 * @param {number} sampleRateHz 调用方声明的原始采样率。
 * @returns {Float32Array} 标准化的单声道样本。
 */
export function decodeAudio(bytes, sampleRateHz) {
  const wav = new wavefile.WaveFile(bytes)
  requireValue(
    wav.fmt.sampleRate === sampleRateHz && wav.fmt.numChannels >= 1 && wav.fmt.numChannels <= 8,
    'invalid_audio',
    'WAV 声道或采样率与声明不符。'
  )
  const seconds = wav.data.samples.length / wav.fmt.byteRate
  requireValue(
    Number.isFinite(seconds) && seconds >= 0.1 && seconds <= 300,
    'invalid_audio',
    '录音时长须为 0.1 到 300 秒。'
  )
  wav.toBitDepth('32f')
  wav.toSampleRate(16000)
  const samples = wav.getSamples()
  const channels = Array.isArray(samples) ? samples : [samples]
  const audio = new Float32Array(channels[0].length)
  requireValue(audio.length <= 4800000, 'invalid_audio')
  for (const channel of channels)
    for (let index = 0; index < audio.length; index++) {
      requireValue(Number.isFinite(channel[index]), 'invalid_audio')
      audio[index] += channel[index] / channels.length
    }
  return audio
}
