import { Application, Ticker } from 'pixi.js'
import { Live2DModel, MotionPriority } from 'pixi-live2d-display'
import { createRuntimeAdapter } from './live2d-adapter.js'
import { readModelCatalog } from '../../../shared/contracts/model-catalog.js'

Live2DModel.registerTicker(Ticker)

function waitForPlayback(ms, signal) {
  return new Promise((resolve) => {
    function finish() {
      clearTimeout(timer)
      signal.removeEventListener('abort', finish)
      resolve()
    }
    const timer = setTimeout(finish, ms)
    signal.addEventListener('abort', finish, { once: true })
    if (signal.aborted) finish()
  })
}

/** 独占舞台、音频与口型；调度器销毁播放时旧回调自动失效。 */
export class StageSurface {
  models = new Map()
  generation = 0
  playback = 0
  listeners = new Set()
  scene = null
  current = null
  problems = []
  loading = false
  constructor(client) {
    this.client = client
    this.load = Promise.resolve()
  }
  observe(listener) {
    this.listeners.add(listener)
    listener(this.snapshot())
    return () => this.listeners.delete(listener)
  }
  snapshot() {
    return {
      scene: this.scene,
      current: this.current,
      problems: [...this.problems],
      loading: this.loading
    }
  }
  notify() {
    for (const listener of this.listeners) listener(this.snapshot())
  }
  mount(element) {
    if (this.app) return
    this.app = new Application({
      backgroundAlpha: 0,
      antialias: true,
      autoDensity: true,
      resolution: Math.min(window.devicePixelRatio || 1, 2)
    })
    this.app.view.setAttribute('aria-label', 'Live2D 演出舞台')
    element.appendChild(this.app.view)
    this.resize = new ResizeObserver(() => {
      this.app.renderer.resize(Math.max(1, element.clientWidth), Math.max(1, element.clientHeight))
      this.fit()
    })
    this.resize.observe(element)
    this.app.ticker.add(() => this.updateMouth())
    if (this.scene) this.showScene(this.scene, true)
  }
  fit() {
    if (!this.app || !this.scene) return
    for (const slot of this.scene.slots) {
      const item = this.models.get(slot.id)
      if (!item) continue
      const width = this.app.screen.width,
        height = this.app.screen.height
      const scale = Math.min(
        (width * (this.scene.slots.length > 1 ? 0.65 : 0.92)) / item.width,
        (height * 0.92) / item.height
      )
      item.model.scale.set(scale)
      item.model.x = width * slot.layout.x
      item.model.y = height * 0.94
    }
  }
  showScene(scene, force = false) {
    const previous = JSON.stringify(this.scene)
    this.scene = scene
    if (!force && previous === JSON.stringify(scene)) return
    const generation = ++this.generation
    for (const item of this.models.values()) item.adapter.destroy()
    this.models.clear()
    this.problems = []
    this.loading = Boolean(this.app && scene?.slots.some((slot) => slot.presentation))
    this.notify()
    if (!scene || !this.app) {
      this.load = Promise.resolve()
      return
    }
    this.load = Promise.all(
      scene.slots.map(async (slot) => {
        if (!slot.presentation) return
        try {
          const { url } = await this.client.resolveAsset(slot.presentation.model)
          const json = await (await fetch(url)).json()
          const version = json.FileReferences ? 'v3' : 'v2'
          const loading = Live2DModel.from(url, { autoInteract: false })
          let timer
          let expired = false
          void loading
            .then((loaded) => {
              if (expired) loaded.destroy()
            })
            .catch(() => {})
          const model = await Promise.race([
            loading,
            new Promise((_, reject) => {
              timer = setTimeout(() => {
                expired = true
                reject(new Error('模型加载超时'))
              }, 12000)
            })
          ]).finally(() => clearTimeout(timer))
          if (generation !== this.generation) {
            model.destroy()
            return
          }
          model.anchor.set(0.5, 1)
          const adapter = createRuntimeAdapter(model, version)
          this.models.set(slot.id, {
            model,
            adapter,
            width: model.width,
            height: model.height,
            catalog: readModelCatalog(json)
          })
          this.app.stage.addChild(model)
          this.fit()
          const idle = this.motionFor(this.models.get(slot.id), {
            performance: { motion: 'auto' },
            emotion: 'IDLE'
          })
          if (idle) void adapter.startMotion(idle.group, idle.index, MotionPriority.IDLE)
        } catch (error) {
          console.warn('Live2D 资源加载失败', error)
          if (generation === this.generation) {
            this.problems.push(`${slot.displayName} 的模型未能加载，仍可显示台词和播放语音。`)
            this.notify()
          }
        }
      })
    ).finally(() => {
      if (generation === this.generation) {
        this.loading = false
        this.notify()
      }
    })
  }
  motionFor(item, guide) {
    if (guide.performance.motion !== 'auto')
      return item.catalog.motions.find((motion) => motion.id === guide.performance.motion)
    return (
      item.catalog.motions.find(
        (motion) => motion.group.toLowerCase() === (guide.emotion || 'IDLE').toLowerCase()
      ) ||
      item.catalog.motions.find((motion) => motion.group === 'IDLE') ||
      item.catalog.motions[0]
    )
  }
  stop() {
    this.playback += 1
    this.playbackController?.abort()
    this.playbackController = null
    if (this.audio) {
      this.audio.pause()
      this.audio.removeAttribute('src')
      this.audio.load()
      this.audio = null
    }
    this.source?.disconnect()
    this.source = null
    this.analyser = null
    for (const item of this.models.values()) {
      item.adapter.setMouthOpen(0)
      item.adapter.setSpeechActive(false)
      item.adapter.stopMotions()
    }
    this.current = null
    this.notify()
  }
  async play(guide, scene, estimatedDuration) {
    this.playbackController?.abort()
    const controller = new AbortController()
    this.playbackController = controller
    const signal = controller.signal
    const token = ++this.playback
    this.current = guide
    this.notify()
    let baseDone = false
    const base = this.playAudio(guide, token, estimatedDuration, signal).finally(() => {
      baseDone = true
    })
    const motion = (async () => {
      await this.load
      if (token !== this.playback) return
      const item = this.models.get(guide.slotId)
      if (!item) return
      item.adapter.setSpeechActive(Boolean(guide.audio))
      const expression = guide.performance.expression
      if (expression !== 'auto')
        await item.adapter.setExpression(expression, () => token === this.playback)
      else item.adapter.resetExpression()
      const selected = this.motionFor(item, guide)
      if (!selected) return
      while (token === this.playback && !signal.aborted && !baseDone) {
        const completed = await this.playMotion(item, selected, token, signal)
        // SDK 拒绝、缺失或超时的动作不重试；音频与文字仍继续。
        if (!completed) break
        // 即使 SDK 同步报告完成，也必须让出事件循环给输入、音频和退出事件。
        await waitForPlayback(16, signal)
      }
    })()
    try {
      await Promise.all([base, motion])
    } finally {
      controller.abort()
      if (token === this.playback) {
        this.current = null
        this.notify()
      }
    }
  }
  async playMotion(item, selected, token, signal) {
    return new Promise((resolve) => {
      let settled = false
      let unsubscribe = () => {}
      const finish = (completed) => {
        if (settled) return
        settled = true
        clearTimeout(timer)
        unsubscribe()
        signal.removeEventListener('abort', cancel)
        resolve(completed)
      }
      const cancel = () => finish(false)
      const timer = setTimeout(cancel, 8000)
      signal.addEventListener('abort', cancel, { once: true })
      if (signal.aborted) {
        cancel()
        return
      }
      unsubscribe = item.adapter.onceMotionFinish(() => finish(true))
      Promise.resolve()
        .then(() =>
          item.adapter.startMotion(
            selected.group,
            selected.index,
            MotionPriority.FORCE,
            150,
            true,
            () => token === this.playback && !signal.aborted
          )
        )
        .then((started) => {
          if (!started) finish(false)
        })
        .catch(cancel)
    })
  }
  async playAudio(guide, token, estimate, signal) {
    if (!guide.audio) {
      await waitForPlayback(estimate, signal)
      return
    }
    const { url } = await this.client.resolveAsset(guide.audio.asset)
    if (token !== this.playback || signal.aborted) return
    const audio = new Audio(url)
    this.audio = audio
    try {
      this.context ||= new AudioContext()
      await this.context.resume()
      if (token !== this.playback || signal.aborted) return
      this.source = this.context.createMediaElementSource(audio)
      this.analyser = this.context.createAnalyser()
      this.analyser.fftSize = 256
      this.samples = new Uint8Array(this.analyser.frequencyBinCount)
      this.source.connect(this.analyser)
      this.analyser.connect(this.context.destination)
    } catch {
      /* 分析器不可用时仍让音频按浏览器默认路径播放。 */
    }
    await new Promise((resolve, reject) => {
      function cleanup() {
        clearTimeout(timer)
        audio.removeEventListener('ended', finish)
        audio.removeEventListener('error', fail)
        audio.removeEventListener('emptied', finish)
        signal.removeEventListener('abort', finish)
      }
      function finish() {
        cleanup()
        resolve()
      }
      function fail() {
        cleanup()
        reject(new Error('音频无法播放，已保留台词。'))
      }
      const timer = setTimeout(fail, estimate + 15000)
      audio.addEventListener('ended', finish, { once: true })
      audio.addEventListener('error', fail, { once: true })
      audio.addEventListener('emptied', finish, { once: true })
      signal.addEventListener('abort', finish, { once: true })
      if (signal.aborted) {
        finish()
        return
      }
      audio.play().catch(fail)
    })
  }
  updateMouth() {
    if (!this.analyser || !this.current) return
    this.analyser.getByteTimeDomainData(this.samples)
    let energy = 0
    for (const value of this.samples) energy += ((value - 128) / 128) ** 2
    const amplitude = Math.min(1, Math.sqrt(energy / this.samples.length) * 5)
    this.models.get(this.current.slotId)?.adapter.setMouthOpen(amplitude)
  }
  dispose() {
    this.stop()
    this.generation += 1
    this.resize?.disconnect()
    for (const item of this.models.values()) item.adapter.destroy()
    this.models.clear()
    this.app?.destroy(true)
    void this.context?.close()
  }
}
