import { useEffect, useRef, useState } from 'react'
import { LoaderCircle, Mic, Square, X } from 'lucide-react'
import { Button } from '../../components/ui/button.jsx'

const recordingLimitMs = 180000
const sampleRate = 16000

async function wavBase64(blob) {
  const context = new AudioContext()
  let decoded
  try {
    decoded = await context.decodeAudioData(await blob.arrayBuffer())
  } finally {
    await context.close()
  }
  const output = new OfflineAudioContext(1, Math.ceil(decoded.duration * sampleRate), sampleRate)
  const source = output.createBufferSource()
  source.buffer = decoded
  source.connect(output.destination)
  source.start()
  const rendered = await output.startRendering()
  const samples = rendered.getChannelData(0)
  const bytes = new ArrayBuffer(44 + samples.length * 2)
  const header = new DataView(bytes)
  function label(offset, value) {
    for (let index = 0; index < value.length; index += 1)
      header.setUint8(offset + index, value.charCodeAt(index))
  }
  label(0, 'RIFF')
  header.setUint32(4, bytes.byteLength - 8, true)
  label(8, 'WAVE')
  label(12, 'fmt ')
  header.setUint32(16, 16, true)
  header.setUint16(20, 1, true)
  header.setUint16(22, 1, true)
  header.setUint32(24, sampleRate, true)
  header.setUint32(28, sampleRate * 2, true)
  header.setUint16(32, 2, true)
  header.setUint16(34, 16, true)
  label(36, 'data')
  header.setUint32(40, samples.length * 2, true)
  for (let index = 0; index < samples.length; index += 1) {
    const sample = Math.max(-1, Math.min(1, samples[index]))
    header.setInt16(44 + index * 2, sample * (sample < 0 ? 32768 : 32767), true)
  }
  // WAV 经过 Base64 放进 8 MiB JSON 请求，录音上限预留封装空间。
  if (bytes.byteLength > 6_000_000) throw new Error('录音太长，请分段录制。')
  const blobReader = new FileReader()
  return new Promise((resolve, reject) => {
    blobReader.onload = () => resolve(blobReader.result.split(',')[1])
    blobReader.onerror = () => reject(new Error('无法读取录音。'))
    blobReader.readAsDataURL(new Blob([bytes], { type: 'audio/wav' }))
  })
}

function releaseRecorder(operation) {
  clearTimeout(operation.timer)
  if (operation.recorder?.state === 'recording') operation.recorder.stop()
  for (const track of operation.stream?.getTracks() ?? []) track.stop()
}

/** @param {object} props 主页输入会话、后端连接与安装入口。 @returns {import('react').ReactElement} 点按录音及取消。 */
export function VoiceInput({ client, flow, ready, disabled, onInstall }) {
  const active = useRef(null)
  const [status, setStatus] = useState('idle')
  const [problem, setProblem] = useState(null)

  useEffect(
    () => () => {
      const operation = active.current
      active.current = null
      if (!operation) return
      releaseRecorder(operation)
      if (operation.requested) void client.recognition.cancel(operation.id).catch(() => {})
    },
    [client]
  )

  function finish(operation, error) {
    if (active.current !== operation) return
    active.current = null
    releaseRecorder(operation)
    setStatus('idle')
    if (error) setProblem(error)
  }

  async function transcribe(operation, blob) {
    if (active.current !== operation) return
    setStatus('transcribing')
    let draft
    let asset
    try {
      if (!blob.size) throw new Error('录音太短，请再试一次。')
      const base64 = await wavBase64(blob)
      if (active.current !== operation) return
      draft = await client.assets.createDraft()
      if (active.current !== operation) return
      const audio = await client.assets.upload({
        name: 'voice-input.wav',
        mediaType: 'audio/wav',
        purpose: 'audio',
        draftId: draft.draftId,
        base64
      })
      asset = audio.asset
      if (active.current !== operation) return
      operation.requested = true
      const value = await client.recognition.transcribe({
        audio: audio.asset,
        sampleRateHz: sampleRate,
        language: null,
        requestId: operation.id
      })
      if (active.current !== operation) return
      if (value.status === 'succeeded') {
        if (value.text?.trim()) flow.appendRecognition(operation.input, value.text.trim())
        else setProblem('没有识别到文字，请再试一次。')
      } else if (value.status === 'failed') setProblem(value.problem?.message ?? '识别失败。')
      finish(operation)
    } catch (error) {
      finish(operation, error.message)
    } finally {
      if (draft) await client.assets.discardDraft(draft.draftId).catch(() => {})
      if (asset) await client.assets.collectUnused({ resources: [asset] }).catch(() => {})
    }
  }

  async function start() {
    if (active.current) return
    setProblem(null)
    const operation = { id: crypto.randomUUID(), input: flow.input() }
    active.current = operation
    setStatus('starting')
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      operation.stream = stream
      if (active.current !== operation) {
        releaseRecorder(operation)
        return
      }
      const recorder = new MediaRecorder(stream)
      operation.recorder = recorder
      const chunks = []
      recorder.ondataavailable = (event) => chunks.push(event.data)
      recorder.onerror = () => finish(operation, '录音设备中断，请再试一次。')
      recorder.onstop = () =>
        void transcribe(operation, new Blob(chunks, { type: recorder.mimeType }))
      recorder.start()
      operation.timer = setTimeout(() => releaseRecorder(operation), recordingLimitMs)
      setStatus('recording')
    } catch (error) {
      const message =
        error.name === 'NotAllowedError'
          ? '请允许数字小祥使用麦克风。'
          : error.name === 'NotFoundError'
            ? '没有找到可用的麦克风。'
            : '无法开始录音，请检查麦克风。'
      finish(operation, message)
    }
  }

  function cancel() {
    const operation = active.current
    if (!operation) return
    finish(operation)
    if (operation.requested) void client.recognition.cancel(operation.id).catch(() => {})
  }

  function click() {
    if (!ready) onInstall()
    else if (status === 'recording') releaseRecorder(active.current)
    else void start()
  }

  let label = '语音输入'
  if (status === 'recording') label = '结束录音并识别'
  if (status === 'starting') label = '正在打开麦克风'
  if (status === 'transcribing') label = '正在识别'
  const processing = status === 'starting' || status === 'transcribing'
  return (
    <div className="home-voice-input">
      {problem && (
        <span className="home-voice-problem" role="alert">
          {problem}
        </span>
      )}
      <Button
        type="button"
        size="icon"
        variant={status === 'recording' ? 'default' : 'ghost'}
        onClick={click}
        disabled={processing || (disabled && status === 'idle')}
        aria-label={label}
        title={label}
      >
        {processing ? (
          <LoaderCircle className="welcome-spinner" />
        ) : status === 'recording' ? (
          <Square />
        ) : (
          <Mic />
        )}
      </Button>
      {status !== 'idle' && (
        <Button
          type="button"
          size="icon"
          variant="ghost"
          onClick={cancel}
          aria-label="取消语音输入"
        >
          <X />
        </Button>
      )}
    </div>
  )
}

/** @param {object} props ASR 安装入口的确认动作。 @returns {import('react').ReactElement} 当前主页上的原生确认框。 */
export function VoiceInstallDialog({ onCancel, onConfirm }) {
  const element = useRef(null)
  useEffect(() => {
    const dialog = element.current
    dialog.showModal()
    return () => dialog.close()
  }, [])
  function cancel(event) {
    event.preventDefault()
    onCancel()
  }
  return (
    <dialog
      ref={element}
      className="home-delete"
      onCancel={cancel}
      aria-labelledby="voice-install-title"
    >
      <h2 id="voice-install-title">启用语音输入</h2>
      <p>使用语音输入需要先下载语音识别资源。</p>
      <div>
        <Button variant="outline" onClick={onCancel}>
          取消
        </Button>
        <Button onClick={onConfirm}>前往下载</Button>
      </div>
    </dialog>
  )
}
