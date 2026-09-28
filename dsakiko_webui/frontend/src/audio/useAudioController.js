import { useCallback, useEffect, useRef, useState } from 'react'

const SILENCE_DATA_URL = (
  'data:audio/wav;base64,'
  + 'UklGRigAAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YQQAAACAgICA'
)

const idlePlayback = {
  messageId: null,
  instanceId: 0,
  status: 'idle',
  progress: 0,
  duration: 0,
  error: '',
}

export function timeDomainRms(samples, byteEncoded = false) {
  if (!samples.length) return 0
  let sum = 0
  for (const sample of samples) {
    const amplitude = byteEncoded ? (sample - 128) / 128 : sample
    sum += amplitude * amplitude
  }
  return Math.sqrt(sum / samples.length)
}

export function useAudioController() {
  const audioRef = useRef(null)
  const audioContextRef = useRef(null)
  const analyserRef = useRef(null)
  const animationFrameRef = useRef(null)
  const currentMessageRef = useRef(null)
  const queueRef = useRef([])
  const volumeRef = useRef(0)
  const playbackInstanceRef = useRef(0)
  const textTimerRef = useRef(null)
  const finishPlaybackRef = useRef(null)
  const [unlocked, setUnlocked] = useState(false)
  const [playback, setPlayback] = useState(idlePlayback)

  const ensureAudioGraph = useCallback(() => {
    if (audioContextRef.current) return audioContextRef.current

    const AudioContextClass = window.AudioContext || window.webkitAudioContext
    if (!AudioContextClass) return null
    const audio = audioRef.current
    if (!audio) return null

    const context = new AudioContextClass()
    const analyser = context.createAnalyser()
    analyser.fftSize = 1024
    const source = context.createMediaElementSource(audio)
    source.connect(analyser)
    analyser.connect(context.destination)

    audioContextRef.current = context
    analyserRef.current = analyser

    const supportsFloatSamples = typeof analyser.getFloatTimeDomainData === 'function'
    const samples = supportsFloatSamples
      ? new Float32Array(analyser.fftSize)
      : new Uint8Array(analyser.fftSize)
    const updateVolume = () => {
      if (supportsFloatSamples) analyser.getFloatTimeDomainData(samples)
      else analyser.getByteTimeDomainData(samples)
      const rms = timeDomainRms(samples, !supportsFloatSamples)
      volumeRef.current = audio.paused || rms < 0.008 ? 0 : rms
      animationFrameRef.current = requestAnimationFrame(updateVolume)
    }
    updateVolume()
    return context
  }, [])

  const startPlayback = useCallback(async (message, keepQueue = false) => {
    if (!message?.audio_url && !message?.text) return false
    clearTimeout(textTimerRef.current)
    textTimerRef.current = null
    if (!keepQueue) queueRef.current = []
    const audio = audioRef.current
    if (!audio) return false
    if (!message.audio_url) {
      audio.pause()
      currentMessageRef.current = message
      volumeRef.current = 0
      playbackInstanceRef.current += 1
      const duration = Math.max(6, Math.min(30, ((message.text || '').length + (message.translation || '').length) / 6))
      setPlayback({ ...idlePlayback, messageId: message.id, instanceId: playbackInstanceRef.current, status: 'presenting', duration })
      textTimerRef.current = setTimeout(() => {
        textTimerRef.current = null
        finishPlaybackRef.current?.()
      }, duration * 1000)
      return true
    }

    const context = ensureAudioGraph()
    if (context?.state === 'suspended') {
      await context.resume().catch(() => {})
    }

    currentMessageRef.current = message
    audio.src = message.audio_url
    audio.currentTime = 0
    setPlayback({
      messageId: message.id,
      instanceId: playbackInstanceRef.current,
      status: 'loading',
      progress: 0,
      duration: 0,
      error: '',
    })

    try {
      await audio.play()
      return true
    } catch (error) {
      volumeRef.current = 0
      setPlayback({
        messageId: message.id,
        instanceId: playbackInstanceRef.current,
        status: 'blocked',
        progress: 0,
        duration: 0,
        error: error instanceof Error ? error.message : String(error),
      })
      return false
    }
  }, [ensureAudioGraph])

  useEffect(() => {
    const audio = new Audio()
    audio.preload = 'auto'
    audioRef.current = audio

    const onPlay = () => {
      if (!currentMessageRef.current?.audio_url) return
      playbackInstanceRef.current += 1
      setPlayback((current) => ({
        ...current,
        instanceId: playbackInstanceRef.current,
        status: 'playing',
        error: '',
      }))
    }
    const onPause = () => {
      if (audio.ended || !currentMessageRef.current?.audio_url) return
      volumeRef.current = 0
      setPlayback((current) => ({ ...current, status: 'paused' }))
    }
    const onTimeUpdate = () => {
      if (!currentMessageRef.current?.audio_url) return
      const duration = Number.isFinite(audio.duration) ? audio.duration : 0
      setPlayback((current) => ({
        ...current,
        duration,
        progress: duration > 0 ? audio.currentTime / duration : 0,
      }))
    }
    const finishPlayback = () => {
      volumeRef.current = 0
      currentMessageRef.current = null
      const nextMessage = queueRef.current.shift()
      if (nextMessage) {
        startPlayback(nextMessage, true)
        return
      }
      setPlayback(idlePlayback)
    }
    const onEnded = () => {
      if (currentMessageRef.current?.audio_url) finishPlayback()
    }
    const onError = () => {
      if (!currentMessageRef.current?.audio_url) return
      volumeRef.current = 0
      setPlayback((current) => ({
        ...current,
        status: 'error',
        error: '音频加载失败',
      }))
    }
    finishPlaybackRef.current = finishPlayback

    audio.addEventListener('play', onPlay)
    audio.addEventListener('pause', onPause)
    audio.addEventListener('timeupdate', onTimeUpdate)
    audio.addEventListener('loadedmetadata', onTimeUpdate)
    audio.addEventListener('ended', onEnded)
    audio.addEventListener('error', onError)

    return () => {
      clearTimeout(textTimerRef.current)
      finishPlaybackRef.current = null
      audio.pause()
      audio.removeEventListener('play', onPlay)
      audio.removeEventListener('pause', onPause)
      audio.removeEventListener('timeupdate', onTimeUpdate)
      audio.removeEventListener('loadedmetadata', onTimeUpdate)
      audio.removeEventListener('ended', onEnded)
      audio.removeEventListener('error', onError)
      if (animationFrameRef.current) cancelAnimationFrame(animationFrameRef.current)
      audioContextRef.current?.close().catch(() => {})
      audioRef.current = null
    }
  }, [startPlayback])

  const unlock = useCallback(async () => {
    const context = ensureAudioGraph()
    if (context?.state === 'suspended') await context.resume().catch(() => {})
    const audio = audioRef.current
    if (!audio) return false

    const previousVolume = audio.volume
    audio.volume = 0
    audio.src = SILENCE_DATA_URL
    try {
      await audio.play()
      audio.pause()
      audio.currentTime = 0
      setUnlocked(true)
      return true
    } catch {
      setUnlocked(false)
      return false
    } finally {
      audio.volume = previousVolume
    }
  }, [ensureAudioGraph])

  const enqueue = useCallback((message) => {
    if (!message?.audio_url && !message?.text) return
    if (currentMessageRef.current?.id === message.id) return
    if (queueRef.current.some((item) => item.id === message.id)) return

    const audio = audioRef.current
    if (!audio) return
    if (currentMessageRef.current && (textTimerRef.current || !audio.ended)) {
      queueRef.current.push(message)
      return
    }
    startPlayback(message, true)
  }, [startPlayback])

  const toggleMessage = useCallback((message) => {
    if (!message?.audio_url) return
    const audio = audioRef.current
    if (!audio) return
    if (currentMessageRef.current?.id !== message.id) {
      startPlayback(message)
      return
    }
    if (audio.paused) {
      audio.play().catch(() => {})
    } else {
      audio.pause()
    }
  }, [startPlayback])

  const stop = useCallback(() => {
    clearTimeout(textTimerRef.current)
    textTimerRef.current = null
    queueRef.current = []
    currentMessageRef.current = null
    const audio = audioRef.current
    if (!audio) return
    audio.pause()
    audio.removeAttribute('src')
    audio.load()
    volumeRef.current = 0
    setPlayback(idlePlayback)
  }, [])

  return {
    unlocked,
    unlock,
    enqueue,
    toggleMessage,
    stop,
    playback,
    volumeRef,
  }
}
