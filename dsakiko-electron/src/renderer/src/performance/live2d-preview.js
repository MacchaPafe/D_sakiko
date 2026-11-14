import { Application, RenderTexture } from 'pixi.js'
import { Live2DModel } from 'pixi-live2d-display'
import { createRuntimeAdapter } from './live2d-adapter.js'

/** @typedef {{left: number, top: number, width: number, height: number}} ModelBounds 模型局部坐标中的不透明范围，避免透明画布改变构图。 */

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
  /** @param {HTMLElement} element 画布容器。 @param {{tracking?: boolean, referenceWidth?: number}} options 鼠标追踪与舞台参考宽度。 */
  constructor(element, { tracking = true, referenceWidth = 864 } = {}) {
    this.element = element
    this.destroyed = false
    this.visible = true
    this.tracking = tracking
    this.referenceWidth = referenceWidth
    this.app = new Application({
      backgroundAlpha: 0,
      antialias: true,
      autoDensity: true,
      resolution: Math.min(window.devicePixelRatio || 1, 2),
      autoStart: false
    })
    this.app.ticker.maxFPS = 60
    this.app.view.setAttribute('aria-label', 'Live2D 角色预览')
    element.appendChild(this.app.view)
    this.resize = new ResizeObserver(() => this.fit())
    this.resize.observe(element)
    this.app.ticker.add(() => this.model?.update(Math.min(this.app.ticker.deltaMS, 50)))
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

  /** @param {string} url 受控预览 URL。 @param {() => boolean} current 是否仍属于当前选择。 @param {AbortSignal} signal 页面取消信号。 @returns {Promise<void>} 模型与纹理就绪后才返回。 */
  async show(url, current, signal) {
    const response = await fetch(url, { signal })
    if (!response.ok) throw new Error('无法读取预览模型，请重试。')
    const json = await response.json()
    if (!current()) return
    const version = json.FileReferences ? 'v3' : 'v2'
    const motions = json.FileReferences?.Motions ?? json.motions ?? {}
    const idle = Object.values(motions)
      .flat()
      .find((motion) => /idle.*_C\.(motion3\.json|mtn)$/i.test(motion.File ?? motion.file ?? ''))
    // 原始包把所有表情动作放在 Default 中；预览只能循环正面待机，不能随机抽到生气或说话动作。
    const idleGroup = 'WelcomeIdle'
    if (json.FileReferences) json.FileReferences.Motions = { [idleGroup]: idle ? [idle] : [] }
    else json.motions = { [idleGroup]: idle ? [idle] : [] }
    const loading = Live2DModel.from(
      { ...json, url },
      {
        autoInteract: false,
        autoUpdate: false,
        motionPreload: 'NONE',
        idleMotionGroup: idleGroup
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
      if (!current() || this.destroyed) return
      adapter = createRuntimeAdapter(model, version)
      adapter.setSpeechActive(false)
      if (idle) await model.motion(idleGroup, 0, 1)
      if (!current() || this.destroyed) {
        adapter.destroy()
        return
      }
      this.bounds = measureModel(this.app, model)
      this.model = model
      this.adapter = adapter
      this.app.stage.addChild(model)
      this.fit()
      this.syncVisibility()
    } catch (error) {
      if (adapter) adapter.destroy()
      else if (model && !model.destroyed) model.destroy({ texture: true, baseTexture: true })
      throw error
    } finally {
      clearTimeout(timeout)
      signal.removeEventListener('abort', cancel)
    }
  }

  /** @param {number} width 当前布局的设计参考宽度。 @returns {void} 保留模型和渲染器，仅更新缩放与位置。 */
  setReferenceWidth(width) {
    this.referenceWidth = width
    this.fit()
  }

  fit() {
    if (this.destroyed) return
    const width = Math.max(1, this.element.clientWidth)
    const height = Math.max(1, this.element.clientHeight)
    this.app.renderer.resize(width, height)
    if (!this.model || !this.bounds) return
    // 两种引导布局的人物高度与顶端一致，仅舞台参考宽度不同。
    const layoutScale = Math.min(width / this.referenceWidth, height / 1024)
    const scale = (1472 * layoutScale) / this.bounds.height
    this.model.scale.set(scale)
    this.model.position.set(
      width / 2 - (this.bounds.left + this.bounds.width / 2) * scale,
      144 * layoutScale - this.bounds.top * scale
    )
  }

  /** @param {number} x 窗口内鼠标横坐标。 @param {number} y 窗口内鼠标纵坐标。 @returns {void} 轻微头部、视线与身体跟随，SDK 负责平滑插值。 */
  lookAt(x, y) {
    if (!this.tracking || !this.model || !this.visible || document.hidden) return
    const rect = this.element.getBoundingClientRect()
    const layoutScale = Math.min(rect.width / this.referenceWidth, rect.height / 1024)
    const eyeX = rect.left + rect.width / 2
    const eyeY = rect.top + 300 * layoutScale
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
    this.app.destroy(true)
  }
}
