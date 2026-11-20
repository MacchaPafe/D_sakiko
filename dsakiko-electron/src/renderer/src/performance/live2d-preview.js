import { Application, RenderTexture } from 'pixi.js'
import { Live2DModel } from 'pixi-live2d-display'
import { createRuntimeAdapter } from './live2d-adapter.js'
import { defaultStageLayout } from './stage-layout.js'

/** @typedef {import('../../../shared/contracts/presentation.js').ModelBounds} ModelBounds */
const boundsStorageKey = 'dsakiko.live2d-bounds.v1'
const boundsCache = readBoundsCache()

function readBoundsCache() {
  try {
    return new Map(JSON.parse(localStorage.getItem(boundsStorageKey) ?? '[]'))
  } catch {
    return new Map()
  }
}

function validBounds(bounds) {
  return (
    bounds &&
    Number.isFinite(bounds.left) &&
    Number.isFinite(bounds.top) &&
    Number.isFinite(bounds.width) &&
    Number.isFinite(bounds.height) &&
    bounds.width > 0 &&
    bounds.height > 0
  )
}

function rememberBounds(key, bounds) {
  boundsCache.delete(key)
  boundsCache.set(key, bounds)
  if (boundsCache.size > 64) boundsCache.delete(boundsCache.keys().next().value)
  try {
    localStorage.setItem(boundsStorageKey, JSON.stringify([...boundsCache]))
  } catch {
    // 禁用存储时仍可在本次运行中复用实测结果。
  }
}

/**
 * 在小离屏画布上测量实际人物范围，只在模型加载后执行一次。
 * @param {Application} app 当前渲染器。
 * @param {Live2DModel} model 已加载的模型。
 * @returns {ModelBounds} 用于欢迎页裁切的角色范围。
 */
function measureModel(app, model) {
  const scale = Math.min(512 / model.width, 1024 / model.height)
  model.scale.set(scale)
  const width = Math.ceil(model.width)
  const height = Math.ceil(model.height)
  const texture = RenderTexture.create({ width, height, resolution: 1 })
  try {
    // 首帧动作与物理需要实际绘制才推进，先稳定到中性站姿再测量。
    for (let frame = 0; frame < 30; frame += 1) {
      model.update(1000 / 60)
      app.renderer.render(model, { renderTexture: texture })
    }
    const pixels = app.renderer.extract.pixels(texture)
    let left = width
    let right = -1
    let top = height
    let bottom = -1
    for (let y = 0; y < height; y += 1) {
      for (let x = 0; x < width; x += 1) {
        if (pixels[(y * width + x) * 4 + 3] < 8) continue
        left = Math.min(left, x)
        right = Math.max(right, x)
        top = Math.min(top, y)
        bottom = Math.max(bottom, y)
      }
    }
    if (right < left) throw new Error('模型没有可见内容，请重试。')
    return {
      left: left / scale,
      top: top / scale,
      width: (right - left + 1) / scale,
      height: (bottom - top + 1) / scale
    }
  } finally {
    texture.destroy(true)
  }
}

/** 引导流程共用的真实模型画布；不创建聊天、不调度语音。 */
export class Live2DPreview {
  /**
   * @param {HTMLElement} element 决定画布缓冲区大小的容器。
   * @param {{tracking?: boolean, referenceWidth?: number, stage?: boolean, sharedApp?: Application, framingElement?: HTMLElement, getReferenceWidth?: () => number}} options 舞台构图及追踪。分栏动画可单独指定可见区域和当前参考宽度。
   */
  constructor(
    element,
    {
      tracking = true,
      referenceWidth = 864,
      stage = false,
      sharedApp = null,
      framingElement = element,
      getReferenceWidth = () => referenceWidth
    } = {}
  ) {
    this.element = element
    this.framingElement = framingElement
    this.getReferenceWidth = getReferenceWidth
    this.destroyed = false
    this.visible = true
    this.tracking = tracking
    this.stage = stage
    this.zoom = 1
    this.stageLayout = null
    this.ownsApp = !sharedApp
    this.app =
      sharedApp ??
      new Application({
        backgroundAlpha: 0,
        antialias: true,
        autoDensity: true,
        resolution: Math.min(window.devicePixelRatio || 1, 2),
        autoStart: false
      })
    if (this.ownsApp) {
      this.app.ticker.maxFPS = 60
      this.app.view.setAttribute('aria-label', 'Live2D 角色预览')
      element.appendChild(this.app.view)
    }
    this.resize = new ResizeObserver(() => this.fit())
    this.resize.observe(element)
    if (framingElement !== element) this.resize.observe(framingElement)
    this.updateModel = () => this.model?.update(Math.min(this.app.ticker.deltaMS, 50))
    this.app.ticker.add(this.updateModel)
    this.pointer = (event) => this.lookAt(event.clientX, event.clientY)
    this.leave = (event) => {
      if (!event.relatedTarget) this.center()
    }
    this.visibility = () => this.syncVisibility()
    this.blur = () => this.center()
    window.addEventListener('pointermove', this.pointer)
    window.addEventListener('pointerout', this.leave)
    window.addEventListener('blur', this.blur)
    document.addEventListener('visibilitychange', this.visibility)
    this.fit()
    this.syncVisibility()
  }

  /**
   * @param {string} url 受控模型 URL。
   * @param {() => boolean} current 是否仍属于当前选择。
   * @param {AbortSignal} signal 页面取消信号。
   * @param {{cacheKey?: string, bounds?: ModelBounds, getLayout?: (version: string) => object}} [options] 已测构图与新实例的舞台视角。
   * @returns {Promise<boolean>} 候选首帧绘制成功并接管画布时返回 true。
   */
  async show(
    url,
    current,
    signal,
    { cacheKey, bounds, stageDirection, getLayout = defaultStageLayout } = {}
  ) {
    const response = await fetch(url, { signal })
    if (!response.ok) throw new Error('无法读取预览模型，请重试。')
    const json = await response.json()
    if (!current()) return false
    const version = json.FileReferences ? 'v3' : 'v2'
    const motions = json.FileReferences?.Motions ?? json.motions ?? {}
    const idle = Object.values(motions)
      .flat()
      .find((motion) => /idle.*_C\.(motion3\.json|mtn)$/i.test(motion.File ?? motion.file ?? ''))
    // 原始包把所有表情动作放在 Default 中；预览只能循环正面待机，不能随机抽到生气或说话动作。
    const idleGroup = 'WelcomeIdle'
    const available = this.stage ? { ...motions } : {}
    available[idleGroup] = idle ? [idle] : []
    if (this.stage) {
      // 部分 Python 模型只在完整索引中声明动作；SDK 仍需通过组和下标播放。
      const indexed = json.DSakiko?.MotionAssets
      if (Array.isArray(indexed)) available.StageIndexed = indexed
      available.StageSilentIdle = []
      for (const direction of ['L', 'R', 'C']) {
        const ending = '_' + direction.toLowerCase() + '.motion3.json'
        const entry = Object.values(motions)
          .flat()
          .concat(indexed ?? [])
          .find((item) => {
            const file = (item.File ?? '').toLowerCase()
            return file.includes('idle') && file.endsWith(ending)
          })
        available['StageIdle' + direction] = entry ? [entry] : []
      }
    }
    if (json.FileReferences) json.FileReferences.Motions = available
    else json.motions = available
    const initialIdle = stageDirection ? 'StageIdle' + stageDirection : idleGroup
    const loading = Live2DModel.from(
      { ...json, url },
      {
        autoInteract: false,
        autoUpdate: false,
        motionPreload: 'NONE',
        idleMotionGroup: initialIdle
      }
    )
    let expired = false
    let timeout
    let cancel
    // SDK 的纹理请求无法全部取消；迟到的实例也必须销毁，不能回到已切换的页面。
    void loading
      .then((model) => {
        if (expired || !current() || this.destroyed)
          model.destroy({ texture: true, baseTexture: true })
      })
      .catch(() => {})
    const interrupted = new Promise((_, reject) => {
      cancel = () => {
        expired = true
        reject(new Error('预览已取消。'))
      }
      signal.addEventListener('abort', cancel, { once: true })
      timeout = setTimeout(() => {
        expired = true
        reject(new Error('模型加载超时，请重试。'))
      }, 30000)
      if (signal.aborted) cancel()
    })
    let model
    let adapter
    try {
      model = await Promise.race([loading, interrupted])
      if (!current() || this.destroyed) {
        if (!model.destroyed) model.destroy({ texture: true, baseTexture: true })
        return false
      }
      adapter = createRuntimeAdapter(model, version)
      adapter.setSpeechActive(false)
      // 待机动作可选；动作文件损坏时仍应能显示模型的默认姿态。
      if (available[initialIdle]?.length)
        await Promise.race([model.motion(initialIdle, 0, 1).catch(() => false), interrupted])
      if (!current() || this.destroyed) {
        adapter.destroy()
        return false
      }
      const cached = boundsCache.get(cacheKey)
      let measured = validBounds(bounds) ? bounds : cached
      if (!validBounds(measured)) {
        measured = measureModel(this.app, model)
        if (cacheKey) rememberBounds(cacheKey, measured)
      }
      this.replaceModel({
        model,
        adapter,
        bounds: measured,
        motionGroups: available,
        stageLayout: this.stage ? getLayout(version) : null
      })
      this.syncVisibility()
      return true
    } catch (error) {
      if (adapter) adapter.destroy()
      else if (model && !model.destroyed) model.destroy({ texture: true, baseTexture: true })
      throw error
    } finally {
      clearTimeout(timeout)
      signal.removeEventListener('abort', cancel)
    }
  }

  replaceModel(next) {
    const previous = {
      model: this.model,
      adapter: this.adapter,
      bounds: this.bounds,
      motionGroups: this.motionGroups,
      stageLayout: this.stageLayout
    }
    // 同一帧内试画候选；绘制失败时恢复旧实例和它的视角。
    if (previous.model) previous.model.visible = false
    Object.assign(this, next)
    try {
      this.app.stage.addChild(next.model)
      this.fit()
      next.model.update(1000 / 60)
      this.app.render()
    } catch (error) {
      Object.assign(this, previous)
      if (previous.model) previous.model.visible = true
      throw error
    }
    previous.adapter?.destroy()
  }

  setStageIdle(motion, direction) {
    const manager = this.model?.internalModel.motionManager
    if (!manager) return
    let group = 'StageIdle' + direction
    const file = motion?.entry?.File ?? motion?.entry?.file
    if (file) {
      const found = Object.entries(this.motionGroups).find(
        ([, entries]) => entries.length === 1 && (entries[0].File ?? entries[0].file) === file
      )
      if (found) group = found[0]
    }
    manager.groups.idle = this.motionGroups[group]?.length ? group : 'StageSilentIdle'
  }

  fit() {
    if (this.destroyed) return
    const bufferWidth = Math.max(1, this.element.clientWidth)
    const bufferHeight = Math.max(1, this.element.clientHeight)
    // 引导分栏变宽时沿用已分配的画布，避免逐帧重设高分屏的渲染缓冲区。
    if (this.app.screen.width !== bufferWidth || this.app.screen.height !== bufferHeight)
      this.app.renderer.resize(bufferWidth, bufferHeight)
    if (!this.model || !this.bounds) return
    const width =
      this.framingElement === this.element
        ? bufferWidth
        : this.framingElement.getBoundingClientRect().width
    const layoutScale = Math.min(Math.max(1, width) / this.getReferenceWidth(), bufferHeight / 1024)
    this.layoutScale = layoutScale
    const modelHeight = this.stage ? 1024 * (this.stageLayout?.scale ?? 1) : 1472
    const modelTop = this.stage ? (this.stageLayout?.offsetY ?? 64) : 144
    const scale = (modelHeight * layoutScale * this.zoom) / this.bounds.height
    // 双人舞台以中线为基准限制最大横向间距，避免宽屏下角色被拉到画布两端。
    const baseX = this.stageSlot
      ? width * 0.5 + this.stageSlot * Math.min(width * 0.225, 240 * layoutScale)
      : width * (this.stageCenter ?? 0.5)
    this.model.scale.set(scale)
    this.model.position.set(
      baseX +
        (this.stage ? (this.stageLayout?.offsetX ?? 0) * layoutScale : 0) -
        (this.bounds.left + this.bounds.width / 2) * scale,
      modelTop * layoutScale - this.bounds.top * scale
    )
  }

  /** @param {number} x 窗口内鼠标横坐标。 @param {number} y 窗口内鼠标纵坐标。 @returns {void} 轻微头部、视线与身体跟随，SDK 负责平滑插值。 */
  lookAt(x, y) {
    if (!this.tracking || !this.model || !this.visible || document.hidden) return
    const rect = this.framingElement.getBoundingClientRect()
    const eyeX = rect.left + rect.width / 2
    const eyeY = rect.top + 300 * this.layoutScale
    const horizontal = Math.max(-0.55, Math.min(0.55, (x - eyeX) / (rect.width * 0.9)))
    const vertical = Math.max(-0.4, Math.min(0.4, (eyeY - y) / (rect.height * 0.8)))
    // model.focus 会把小位移归一化成满幅转头；直接给 FocusController 保留距离感。
    this.model.internalModel.focusController.focus(horizontal, vertical)
  }

  center() {
    this.model?.internalModel.focusController.focus(0, 0)
  }

  /** @param {boolean} visible 宿主是否可见且未最小化。 @returns {void} 隐藏时停止绘制和参数推进。 */
  setVisible(visible) {
    this.visible = visible
    this.syncVisibility()
  }

  syncVisibility() {
    if (this.destroyed) return
    if (!this.ownsApp) return
    if (this.visible && !document.hidden) this.app.start()
    else {
      this.center()
      this.app.stop()
    }
  }

  /** @returns {void} 释放当前角色独占纹理；切换不累积后台模型。 */
  clear() {
    this.adapter?.destroy()
    this.adapter = null
    this.model = null
    this.bounds = null
    this.motionGroups = null
  }

  /** @returns {void} 销毁画布和全部窗口监听。 */
  destroy() {
    this.destroyed = true
    this.resize.disconnect()
    window.removeEventListener('pointermove', this.pointer)
    window.removeEventListener('pointerout', this.leave)
    window.removeEventListener('blur', this.blur)
    document.removeEventListener('visibilitychange', this.visibility)
    this.clear()
    this.app.ticker.remove(this.updateModel)
    if (this.ownsApp) this.app.destroy(true)
  }
}
