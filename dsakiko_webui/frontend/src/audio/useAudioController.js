import { useCallback, useEffect, useRef, useState } from 'react'

const SILENCE_URL = '/mock-assets/audio/silence.wav'

const idlePlayback = {
  messageId: null,
  status: 'idle',
  progress: 0,
  duration: 0,
  error: '',
}

export function useAudioController() {
  const audioRef = useRef(null)
  const audioContextRef = useRef(null)
  const analyserRef = useRef(null)
  const animationFrameRef = useRef(null)
  const currentMessageRef = useRef(null)
  const queueRef = useRef([])
  const volumeRef = useRef(0)
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
    analyser.fftSize = 256
    analyser.smoothingTimeConstant = 0.7
    const source = context.createMediaElementSource(audio)
    source.connect(analyser)
    analyser.connect(context.destination)

    audioContextRef.current = context
    analyserRef.current = analyser

    const samples = new Uint8Array(analyser.fftSize)
    const updateVolume = () => {
      analyser.getByteTimeDomainData(samples)
      let sum = 0
      for (const sample of samples) sum += Math.abs(sample - 128)
      const normalized = Math.min(1, (sum / samples.length / 22) * 1.7)
      volumeRef.current = audio.paused ? 0 : normalized
      animationFrameRef.current = requestAnimationFrame(updateVolume)
    }
    updateVolume()
    return context
  }, [])

  const startPlayback = useCallback(async (message, keepQueue = false) => {
    if (!message?.audio_url) return false
    if (!keepQueue) queueRef.current = []
    const audio = audioRef.current
    if (!audio) return false

    const context = ensureAudioGraph()
    if (context?.state === 'suspended') {
      await context.resume().catch(() => {})
    }

    currentMessageRef.current = message
    audio.src = message.audio_url
    audio.currentTime = 0
    setPlayback({
      messageId: message.id,
      status: 'loading',
      progress: 0,
      duration: 0,
      error: '',
    })

    try {
      await audio.play()
      return true
    } catch (error) {
      setPlayback({
        messageId: message.id,
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
      setPlayback((current) => ({ ...current, status: 'playing', error: '' }))
    }
    const onPause = () => {
      if (audio.ended || !currentMessageRef.current) return
      setPlayback((current) => ({ ...current, status: 'paused' }))
    }
    const onTimeUpdate = () => {
      const duration = Number.isFinite(audio.duration) ? audio.duration : 0
      setPlayback((current) => ({
        ...current,
        duration,
        progress: duration > 0 ? audio.currentTime / duration : 0,
      }))
    }
    const onEnded = () => {
      volumeRef.current = 0
      currentMessageRef.current = null
      const nextMessage = queueRef.current.shift()
      if (nextMessage) {
        startPlayback(nextMessage, true)
        return
      }
      setPlayback(idlePlayback)
    }
    const onError = () => {
      volumeRef.current = 0
      setPlayback((current) => ({
        ...current,
        status: 'error',
        error: '音频加载失败',
      }))
    }

    audio.addEventListener('play', onPlay)
    audio.addEventListener('pause', onPause)
    audio.addEventListener('timeupdate', onTimeUpdate)
    audio.addEventListener('loadedmetadata', onTimeUpdate)
    audio.addEventListener('ended', onEnded)
    audio.addEventListener('error', onError)

    return () => {
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
    audio.src = SILENCE_URL
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
    if (!message?.audio_url) return
    if (currentMessageRef.current?.id === message.id) return
    if (queueRef.current.some((item) => item.id === message.id)) return

    const audio = audioRef.current
    if (!audio) return
    if (currentMessageRef.current && !audio.ended) {
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
